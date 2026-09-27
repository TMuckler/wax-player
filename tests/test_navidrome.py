"""Hermetic API + real mpv tests. No user server, speakers, or desktop changes."""
import asyncio
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.parse
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.bridge import Bridge
from backend.navidrome import Client, Error, base_url, credentials
from backend.player import Player

SONG = "b2a946dc-414d-45ef-986e-429b90d28115"
SONG2 = "721dfbc46faf6921a634107068b0c3e7"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=")


def song(ident=SONG):
    return {"id": ident, "title": "First" if ident == SONG else "Second", "artist": "Artist", "artistId": "ar1",
            "album": "Album", "albumId": "al1", "coverArt": "cover1", "duration": 8, "starred": "2026-01-01"}


def wav_data(seconds=8):
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(8000)
        f.writeframes(b"\0\0" * int(8000 * seconds))
    return out.getvalue()


class Server:
    def __init__(self):
        self.calls = []
        self.fail_auth = False
        self.old_lyrics = False
        self.redirect = False
        self.audio = wav_data()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/music"

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def handler(self):
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                endpoint = parsed.path.rsplit("/", 1)[-1].removesuffix(".view")
                fixture.calls.append((endpoint, query))
                if not parsed.path.startswith("/music/rest/"):
                    self.send_error(404); return
                if fixture.redirect:
                    self.send_response(302); self.send_header("Location", "http://127.0.0.1:1/stolen"); self.end_headers(); return
                token = hashlib.md5(("test password" + query.get("s", [""])[0]).encode()).hexdigest()
                if fixture.fail_auth or query.get("t") != [token] or query.get("u") != ["test"]:
                    self.json({"status": "failed", "error": {"code": 40}}); return
                album = {"id": "al1", "name": "Album", "artist": "Artist", "artistId": "ar1", "song": [song(), song(SONG2)], "coverArt": "cover1"}
                playlist = {"id": "pl1", "name": "My playlist", "entry": [song(), song(), song(SONG2)]}
                if endpoint == "stream":
                    data = fixture.audio
                    offset = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
                    ranged = "Range" in self.headers
                    self.send_response(206 if ranged else 200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(data) - offset))
                    self.send_header("Accept-Ranges", "bytes")
                    if ranged:
                        self.send_header("Content-Range", f"bytes {offset}-{len(data)-1}/{len(data)}")
                    self.end_headers()
                    try: self.wfile.write(data[offset:])
                    except (BrokenPipeError, ConnectionResetError): pass
                    return
                if endpoint == "getCoverArt":
                    self.send_response(200); self.send_header("Content-Type", "image/png")
                    self.end_headers(); self.wfile.write(PNG); return
                payload = {}
                if endpoint == "getSong": payload = {"song": song(query["id"][0])}
                elif endpoint == "search3":
                    offset = int(query.get("songOffset", ["0"])[0])
                    payload = {"searchResult3": {"song": [song()] if offset == 0 else [], "album": [album], "artist": [{"id": "ar1", "name": "Artist"}]}}
                elif endpoint == "getAlbum": payload = {"album": album}
                elif endpoint == "getArtist": payload = {"artist": {"id": "ar1", "name": "Artist", "album": [album]}}
                elif endpoint == "getArtists": payload = {"artists": {"index": [{"artist": [{"id": "ar1", "name": "Artist"}]}]}}
                elif endpoint == "getAlbumList2": payload = {"albumList2": {"album": [album]}}
                elif endpoint == "getStarred2": payload = {"starred2": {"song": [song()]}}
                elif endpoint == "getPlaylists": payload = {"playlists": {"playlist": [playlist]}}
                elif endpoint in ("getPlaylist", "createPlaylist"): payload = {"playlist": playlist}
                elif endpoint == "getSimilarSongs2": payload = {"similarSongs2": {"song": []}}
                elif endpoint == "getRandomSongs": payload = {"randomSongs": {"song": [song(SONG2)]}}
                elif endpoint == "getLyricsBySongId":
                    if fixture.old_lyrics:
                        self.json({"status": "failed", "error": {"code": 0}}); return
                    payload = {"lyricsList": {"structuredLyrics": [{"synced": True, "offset": 100, "line": [{"start": 0, "value": "One"}, {"start": 1000, "value": "Two"}]}]}}
                elif endpoint == "getLyrics": payload = {"lyrics": {"value": "Plain lyrics"}}
                self.json(dict(status="ok", version="1.16.1", **payload))

            def json(self, body):
                data = json.dumps({"subsonic-response": body}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        return Handler


class NavidromeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.server = Server()
        self.client = Client(credentials(self.server.url, "test", "test password"), self.root / "cache")

    async def asyncTearDown(self):
        await asyncio.to_thread(self.server.close)
        self.tmp.cleanup()

    async def test_auth_base_path_and_opaque_ids(self):
        item = await self.client.song(SONG)
        self.assertEqual(item["trackId"], SONG)
        self.assertEqual(item["album"]["id"], "album:al1")
        self.assertTrue(item["thumb"].startswith("file:///"))
        self.assertNotIn("test password", json.dumps(self.client.config))
        endpoint, query = self.server.calls[0]
        self.assertEqual(endpoint, "getSong")
        self.assertEqual(query["id"], [SONG])
        self.assertNotIn("p", query)

    async def test_cover_is_cached_and_model_contains_no_token(self):
        a = await self.client.song(SONG)
        b = await self.client.song(SONG)
        self.assertEqual(a["thumb"], b["thumb"])
        self.assertEqual(sum(ep == "getCoverArt" for ep, _ in self.server.calls), 1)
        self.assertNotIn(self.client.config["token"], json.dumps(a))

    async def test_api_errors_and_redirects_are_sanitized(self):
        self.server.fail_auth = True
        with self.assertRaisesRegex(Error, "auth-failed"):
            await self.client.request("ping")
        self.server.fail_auth = False; self.server.redirect = True
        with self.assertRaisesRegex(Error, "server-redirect"):
            await self.client.request("ping")

    async def test_browse_search_and_library(self):
        results = await self.client.search("playlist", "playlists")
        self.assertEqual(results["playlists"][0]["browseId"], "playlist:pl1")
        self.assertEqual(len((await self.client.browse("album:al1"))["tracks"]), 2)
        self.assertEqual(len(await self.client.playlist_tracks("artist:ar1")), 2)
        self.assertEqual(len((await self.client.home())["sections"]), 3)
        self.assertEqual((await self.client.library("songs"))["tracks"][0]["like"], "LIKE")
        self.assertEqual((await self.client.library("albums"))["sections"][0]["more"]["browseId"], "albums:alphabeticalByName")

    async def test_timed_lyrics_and_legacy_fallback(self):
        lyrics = await self.client.lyrics(SONG)
        self.assertEqual(lyrics["kind"], "timed")
        self.assertEqual(lyrics["lines"][1], {"t": 900, "text": "Two"})
        self.server.old_lyrics = True
        self.assertEqual((await self.client.lyrics(SONG))["text"], "Plain lyrics")

    def test_reject_invalid_urls(self):
        for url in ("file:///tmp/music", "https://user:pass@example.com", "https://example.com?token=secret", "not a url"):
            with self.subTest(url=url), self.assertRaises(Error): base_url(url)


class TransportPolicyTests(unittest.TestCase):
    def test_https_and_numeric_loopback_are_allowed(self):
        for url in ("https://music.example.com/navidrome", "https://192.168.1.2:4533",
                    "http://127.0.0.1:4533/music", "http://127.1.2.3:4533", "http://[::1]:4533"):
            with self.subTest(url=url):
                config = credentials(url + "/", "test", "test-only")
                client = Client(config, "/tmp/wax-unused-cache")
                for endpoint in ("ping", "stream", "getCoverArt"):
                    self.assertTrue(client.url(endpoint).startswith(url + "/rest/"))

    def test_remote_http_is_rejected_for_new_and_saved_credentials(self):
        for host in ("example.com", "192.168.1.2", "10.0.0.2", "172.16.0.2", "169.254.1.2",
                     "0.0.0.0", "[::]", "[2001:db8::1]", "localhost", "localhost.",
                     "127.0.0.1.example.com", "127.1", "2130706433", "0x7f000001",
                     "%31%32%37.0.0.1", "[::ffff:127.0.0.1]", "[::1%25eth0]"):
            url = "http://" + host + ":4533/music"
            with self.subTest(url=url):
                with self.assertRaisesRegex(Error, "https-required"):
                    credentials(url, "test", "test-only")
                with self.assertRaisesRegex(Error, "https-required"):
                    Client(dict(url=url, username="test", salt="salt", token="token"), "/tmp/wax-unused-cache")

    def test_malformed_urls_raise_sanitized_errors(self):
        for url in ("https://[invalid", "https://example.com:bad", "http://@127.0.0.1",
                    "https://example.com?token=secret", "https://user:secret@example.com"):
            with self.subTest(url=url), self.assertRaisesRegex(Error, "bad-server-url"):
                base_url(url)


@unittest.skipUnless(Path("/usr/bin/mpv").exists(), "mpv is required for playback integration tests")
class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await NavidromeTests.asyncSetUp(self)
        self.bridge = Bridge(runtime=self.root / "run", config=self.root / "connection.json",
                             state=self.root / "session.json", cache=self.root / "cache",
                             player_factory=lambda cb: Player(cb, extra_args=("--ao=null",)))
        await self.bridge.dispatch("connection.save", {"url": self.server.url, "username": "test", "password": "test password"})

    async def asyncTearDown(self):
        await self.bridge.stop()
        await asyncio.gather(*self.bridge.tasks, return_exceptions=True)
        await NavidromeTests.asyncTearDown(self)

    async def until(self, condition, timeout=4):
        async with asyncio.timeout(timeout):
            while not condition(): await asyncio.sleep(0.03)

    async def test_full_quit_reaps_real_mpv_and_removes_socket(self):
        await self.bridge.dispatch("play", {"trackId": SONG})
        process = self.bridge.player.process
        self.assertIsNone(process.returncode)
        self.bridge.want_running = False
        with patch("backend.mpris.Mpris"):
            runner = asyncio.create_task(self.bridge.run())
            path = self.bridge.runtime / "bridge.sock"
            try:
                await self.until(path.exists)
                await self.bridge.dispatch("app.quit", {})
                await asyncio.wait_for(runner, 5)
                self.assertIsNotNone(process.returncode)
                self.assertFalse(path.exists())
            finally:
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)

    async def test_playback_verifies_tls_certificates(self):
        self.assertTrue(await self.bridge.player.command("get_property", "tls-verify"))

    async def test_real_play_pause_seek_volume_and_eq(self):
        await self.bridge.dispatch("play", {"trackId": SONG})
        await self.until(lambda: self.bridge.position > 0.1)
        await self.bridge.dispatch("transport", {"action": "pause"})
        self.assertFalse(self.bridge.playing)
        await self.bridge.dispatch("seek", {"seconds": 3})
        await self.until(lambda: abs(self.bridge.position - 3) < 0.2)
        await self.bridge.dispatch("volume", {"level": 37})
        await self.until(lambda: self.bridge.volume == 37)
        await self.bridge.dispatch("eq.set", {"bands": [1] * 10, "preamp": -3, "loudness": True})
        filters = await self.bridge.player.command("get_property", "af")
        self.assertTrue(filters)
        await self.bridge.dispatch("transport", {"action": "play"})
        await self.until(lambda: self.bridge.position > 3.1)

    async def test_duplicate_queue_occurrences_move_remove_and_play(self):
        await self.bridge.dispatch("play", {"playlistId": "playlist:pl1"})
        await self.bridge.dispatch("queue.jump", {"index": 1})
        await self.bridge.dispatch("queue.move", {"from": 0, "to": 2})
        self.assertEqual(self.bridge.index, 0)
        self.assertEqual(self.bridge.current["trackId"], SONG)
        await self.bridge.dispatch("queue.remove", {"index": 2})
        self.assertEqual(len(self.bridge.items), 2)
        with self.assertRaisesRegex(Error, "playing"):
            await self.bridge.dispatch("queue.remove", {"index": 0})
        await self.bridge.dispatch("transport", {"action": "next"})
        self.assertEqual(self.bridge.current["trackId"], SONG2)

    async def test_queue_add_empty_then_play_and_like(self):
        await self.bridge.dispatch("queue.add", {"trackIds": [SONG], "next": True})
        await self.bridge.dispatch("transport", {"action": "play"})
        self.assertEqual(self.bridge.current["trackId"], SONG)
        await self.bridge.dispatch("like", {"trackId": SONG, "status": "INDIFFERENT"})
        self.assertEqual(self.bridge.current["like"], "INDIFFERENT")
        self.assertTrue(any(ep == "unstar" for ep, _ in self.server.calls))

    async def test_eof_advances_and_repeat_one_reloads(self):
        self.server.audio = wav_data(0.4)
        await self.bridge.dispatch("play", {"playlistId": "album:al1"})
        await self.until(lambda: self.bridge.index == 1)
        await self.until(lambda: not self.bridge.loaded)
        await self.bridge.dispatch("repeat", {"mode": "ONE"})
        await self.bridge.dispatch("play", {"trackId": SONG})
        epoch = self.bridge.epoch
        await self.until(lambda: self.bridge.epoch > epoch and self.bridge.loaded)
        self.assertEqual(self.bridge.index, 0)
        self.assertTrue(self.bridge.loaded)

    async def test_session_restore_and_credentials_permissions(self):
        await self.bridge.dispatch("play", {"playlistId": "playlist:pl1"})
        await self.bridge.dispatch("queue.jump", {"index": 1})
        await self.bridge.dispatch("transport", {"action": "pause"})
        await self.bridge.dispatch("seek", {"seconds": 2})
        await self.until(lambda: abs(self.bridge.position - 2) < 0.2)
        await self.bridge.stop()
        other = Bridge(runtime=self.root / "run2", config=self.root / "connection.json", state=self.root / "session.json", cache=self.root / "cache", player_factory=lambda cb: Player(cb, extra_args=("--ao=null",)))
        try:
            await other.start()
            await self.until(lambda: abs(other.position - 2) < 0.2)
            self.assertEqual(other.index, 1)
            self.assertFalse(other.playing)
            self.assertEqual(len(other.items), 3)
            self.assertEqual((self.root / "connection.json").stat().st_mode & 0o777, 0o600)
            self.assertNotIn("test password", (self.root / "connection.json").read_text())
            self.assertNotIn(self.bridge.api.config["token"], (self.root / "session.json").read_text())
        finally:
            await other.stop()
            await asyncio.gather(*other.tasks, return_exceptions=True)

    async def test_failed_account_change_preserves_existing_connection(self):
        with self.assertRaisesRegex(Error, "auth-failed"):
            await self.bridge.dispatch("connection.save", {"url": self.server.url, "username": "test", "password": "wrong"})
        self.assertTrue(self.bridge.account["signedIn"])
        await self.bridge.dispatch("signout", {})
        self.assertIsNone(self.bridge.api)
        self.assertFalse((self.root / "connection.json").exists())
        self.assertFalse((self.root / "session.json").exists())

    async def test_radio_fallback_and_playlist_edits(self):
        await self.bridge.dispatch("radio", {"trackId": SONG})
        self.assertEqual([i["trackId"] for i in self.bridge.items], [SONG, SONG2])
        created = await self.bridge.dispatch("playlist.create", {"title": "New", "trackIds": [SONG]})
        self.assertEqual(created["playlistId"], "playlist:pl1")
        await self.bridge.dispatch("playlist.add", {"playlistId": "playlist:pl1", "trackId": SONG2})
        query = next(q for ep, q in self.server.calls if ep == "updatePlaylist")
        self.assertEqual(query["songIdToAdd"], [SONG2])

    async def test_invalid_controls_do_not_mutate_queue(self):
        for op, args in (("volume", {"level": float("nan")}), ("repeat", {"mode": "BAD"}), ("queue.add", {"trackIds": []}), ("queue.jump", {"index": -1}), ("eq.set", {"bands": [0]})):
            with self.subTest(op=op), self.assertRaises(Error): await self.bridge.dispatch(op, args)
        self.assertEqual(self.bridge.items, [])

    async def test_socket_contract_and_malformed_input(self):
        sock = self.root / "bridge.sock"
        server = await asyncio.start_unix_server(self.bridge.client, path=sock)
        reader, writer = await asyncio.open_unix_connection(sock)
        try:
            writer.write(b'bad json\n{"id":1,"op":"hello"}\n{"id":2,"op":"ui.attach"}\n')
            replies = {}
            async with asyncio.timeout(3):
                while 2 not in replies or 1 not in replies or None not in replies:
                    msg = json.loads(await reader.readline())
                    if "id" in msg: replies[msg["id"]] = msg
            self.assertEqual(replies[None]["error"], "bad-json")
            self.assertTrue(replies[1]["data"]["account"]["signedIn"])
            self.assertNotIn(self.bridge.api.config["token"], json.dumps(replies))
            self.assertTrue(self.bridge.ui)
        finally:
            writer.close(); await writer.wait_closed()
            server.close(); await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
