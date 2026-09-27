"""Regressions found during the Navidrome port review."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.bridge import Bridge
from backend.navidrome import Error, credentials, private_json


class FakePlayer:
    def __init__(self, callback):
        self.callback = callback
        self.commands = []
        self.loads = []

    async def start(self): pass
    async def stop(self): pass
    async def command(self, *args): self.commands.append(args)
    async def load(self, url, position=0): self.loads.append((url, position))


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.kwargs = dict(runtime=self.root / "run", config=self.root / "connection.json",
                           state=self.root / "session.json", cache=self.root / "cache", player_factory=FakePlayer)
        self.config = credentials("http://127.0.0.1:12345", "test", "test-only")
        private_json(self.kwargs["config"], self.config)
        self.bridge = Bridge(**self.kwargs)
        self.bridge.account["signedIn"] = True
        self.bridge.status = "ready"
        self.bridge.api.request = AsyncMock(return_value={"status": "ok"})
        self.bridge.api.cover = AsyncMock(return_value="")
        self.bridge.items = [{"trackId": "one", "title": "One", "duration": 120}, {"trackId": "two", "title": "Two", "duration": 120}]
        self.bridge.index = 0
        self.bridge.loaded = self.bridge.playing = True

    async def asyncTearDown(self):
        for task in tuple(self.bridge.tasks): task.cancel()
        await asyncio.gather(*self.bridge.tasks, return_exceptions=True)
        self.tmp.cleanup()

    async def test_insecure_signin_does_not_send_credentials_or_replace_account(self):
        api = self.bridge.api
        before = self.kwargs["config"].read_bytes()
        with patch("backend.navidrome.Client.request", new_callable=AsyncMock) as request:
            with self.assertRaisesRegex(Error, "https-required"):
                await self.bridge.dispatch("connection.save", {
                    "url": "http://192.168.1.2:4533", "username": "test", "password": "test-only"})
            request.assert_not_awaited()
        self.assertIs(self.bridge.api, api)
        self.assertEqual(self.kwargs["config"].read_bytes(), before)
        self.assertTrue(self.bridge.playing)

    async def test_saved_insecure_connection_cannot_start_or_restore_playback(self):
        config = dict(self.config, url="http://example.com/music")
        private_json(self.kwargs["config"], config)
        private_json(self.kwargs["state"], {
            "identity": config["url"] + "\0test", "items": self.bridge.items,
            "index": 0, "playing": True})
        with patch("backend.navidrome.Client.request", new_callable=AsyncMock) as request:
            other = Bridge(**self.kwargs)
            await other.start()
            request.assert_not_awaited()
        self.assertIsNone(other.api)
        self.assertFalse(other.account["signedIn"])
        self.assertEqual(other.status, "unconfigured")
        self.assertEqual(other.items, [])
        self.assertEqual(other.player.loads, [])

    async def test_failed_track_replacement_stops_old_audio_and_publishes_state(self):
        self.bridge.api.song = AsyncMock(return_value={"trackId": "new", "duration": 10})
        self.bridge.player.load = AsyncMock(side_effect=Error("playback-error"))
        emitted = []
        self.bridge.emit = lambda event, data: emitted.append((event, data))
        with self.assertRaises(Error):
            await self.bridge.dispatch("play", {"trackId": "new"})
        self.assertIn(("stop",), self.bridge.player.commands)
        self.assertFalse(self.bridge.loaded)
        self.assertFalse(self.bridge.playing)
        self.assertTrue(any(e == "queue" for e, d in emitted))
        self.assertTrue(any(e == "player" and d["trackId"] == "new" and d["playbackStatus"] == "Stopped" for e, d in emitted))
        self.assertEqual(self.bridge.error, "playback-error")

    async def test_failed_start_cleans_up_player(self):
        self.bridge.player.command = AsyncMock(side_effect=Error("playback-error"))
        self.bridge.player.stop = AsyncMock()
        with self.assertRaises(Error):
            await self.bridge.start()
        self.bridge.player.stop.assert_awaited_once()
        self.assertFalse(self.bridge.loaded)
        self.assertFalse(self.bridge.playing)
        self.assertEqual(self.bridge.status, "stopped")

    async def test_disconnected_buffered_attach_does_not_hold_ui_lease(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"id":1,"op":"ui.attach"}\n')
        reader.feed_eof()
        writer = MagicMock()
        writer.is_closing.return_value = False
        writer.transport.get_write_buffer_size.return_value = 0
        await self.bridge.client(reader, writer)
        self.assertNotIn(writer, self.bridge.ui)

    async def test_shutdown_cancels_pending_client_mutations(self):
        self.bridge.want_running = False
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def pending(op, args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.bridge.dispatch = pending
        with patch("backend.mpris.Mpris"):
            runner = asyncio.create_task(self.bridge.run())
            path = self.bridge.runtime / "bridge.sock"
            for _ in range(100):
                if path.exists(): break
                await asyncio.sleep(0.01)
            reader, writer = await asyncio.open_unix_connection(path)
            try:
                writer.write(b'{"id":1,"op":"pending"}\n')
                await writer.drain()
                await asyncio.wait_for(entered.wait(), 1)
                self.bridge.done.set()
                await asyncio.wait_for(runner, 2)
                self.assertTrue(cancelled.is_set(), "client mutation survived bridge shutdown")
            finally:
                writer.close()
                await writer.wait_closed()
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)

    async def test_slow_favorite_request_does_not_block_pause(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return {"status": "ok"}
        self.bridge.api.request = delayed
        favorite = asyncio.create_task(self.bridge.dispatch("like", {"trackId": "one", "status": "LIKE"}))
        try:
            await entered.wait()
            await asyncio.wait_for(self.bridge.dispatch("transport", {"action": "pause"}), 0.2)
            self.assertFalse(self.bridge.playing)
        finally:
            release.set()
            await favorite

    async def test_favorite_completion_after_signout_does_not_change_queue(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
        self.bridge.api.request = delayed
        favorite = asyncio.create_task(self.bridge.dispatch("like", {"trackId": "one", "status": "LIKE"}))
        try:
            await entered.wait()
            await asyncio.wait_for(self.bridge.dispatch("signout", {}), 0.2)
        finally:
            release.set()
        with self.assertRaises(Error):
            await favorite
        self.assertEqual(self.bridge.items, [])
        self.assertFalse(self.kwargs["state"].exists())

    def test_valid_saved_queue_restores(self):
        self.bridge.position, self.bridge.volume = 37, 42
        self.bridge.save()
        restored = Bridge(**self.kwargs)
        self.assertEqual(restored.items, self.bridge.items)
        self.assertEqual((restored.index, restored.position, restored.volume), (0, 37, 42))
        self.assertTrue(restored.resume_playing)

    async def test_late_eof_during_replacement_does_not_skip_new_track(self):
        async with self.bridge.lock:
            self.bridge.loaded = False
            self.bridge.player_event({"event": "end-file", "reason": "eof"})
            self.bridge.index = 1
            self.bridge.loaded = self.bridge.playing = True
        await asyncio.gather(*self.bridge.tasks, return_exceptions=True)
        self.assertTrue(self.bridge.loaded, "the replacement track was stopped by the old track's EOF")
        self.assertEqual(self.bridge.player.commands, [])

    def test_invalid_saved_queue_is_discarded_atomically(self):
        identity = self.config["url"] + "\0" + self.config["username"]
        for saved in ([], {"identity": identity, "items": [None], "index": 0},
                      {"identity": identity, "items": [{"trackId": "one"}], "index": 0.5},
                      {"identity": identity, "items": [{"trackId": "one"}], "index": 0, "volume": "broken"}):
            with self.subTest(saved=saved):
                private_json(self.kwargs["state"], saved)
                bridge = Bridge(**self.kwargs)
                self.assertEqual(bridge.items, [])
                self.assertEqual(bridge.snapshot()["playbackStatus"], "Stopped")
                self.assertIsNotNone(bridge.api, "a corrupt queue must not lose connection settings")

    async def test_cached_cover_missing_after_restart_is_refetched(self):
        self.bridge.items[0].update(coverId="cover", thumb=(self.root / "evicted.img").as_uri())
        await self.bridge.load(0)
        self.bridge.api.cover.assert_awaited_once_with("cover")

    async def test_seeked_reports_clamped_position(self):
        self.bridge.duration = 120
        signals = []
        class Mpris:
            def seeked(self, seconds): signals.append(seconds)
        self.bridge.mpris = Mpris()
        await self.bridge.dispatch("seek", {"seconds": 500})
        self.assertEqual(self.bridge.player.commands[-1], ("seek", 120, "absolute+exact"))
        self.assertEqual(signals, [120])


if __name__ == "__main__":
    unittest.main()
