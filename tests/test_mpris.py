"""Exercise the actual GIO MPRIS service on a private D-Bus session."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.bridge import Bridge
from backend.mpris import Mpris
from backend.player import Player
from test_navidrome import Server, SONG


async def exercise():
    server = Server()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bridge = Bridge(runtime=root / "run", config=root / "config", state=root / "state", cache=root / "cache",
                        player_factory=lambda cb: Player(cb, extra_args=("--ao=null",)))
        mpris = Mpris(bridge)
        bridge.mpris = mpris
        mpris.start()
        try:
            async with asyncio.timeout(5):
                while not mpris.available: await asyncio.sleep(0.03)
            await bridge.dispatch("connection.save", {"url": server.url, "username": "test", "password": "test password"})
            await bridge.dispatch("play", {"trackId": SONG})
            async def dbus(method, *args):
                p = await asyncio.create_subprocess_exec("/usr/bin/gdbus", "call", "--session", "--dest", "org.mpris.MediaPlayer2.wax_player",
                     "--object-path", "/org/mpris/MediaPlayer2", "--method", method, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await p.communicate()
                assert p.returncode == 0, stderr.decode()
                return stdout.decode()
            props = await dbus("org.freedesktop.DBus.Properties.GetAll", "org.mpris.MediaPlayer2.Player")
            assert "First" in props and "Playing" in props, props
            assert "test password" not in props and bridge.api.config["token"] not in props
            await dbus("org.mpris.MediaPlayer2.Player.Pause")
            assert not bridge.playing
            await dbus("org.freedesktop.DBus.Properties.Set", "org.mpris.MediaPlayer2.Player", "Volume", "<0.42>")
            async with asyncio.timeout(3):
                while bridge.volume != 42: await asyncio.sleep(0.03)
            await dbus("org.mpris.MediaPlayer2.Player.Stop")
            assert not bridge.loaded
            props = await dbus("org.freedesktop.DBus.Properties.Get", "org.mpris.MediaPlayer2.Player", "PlaybackStatus")
            assert "Stopped" in props, props
            print("MPRIS_OK")
        finally:
            await bridge.stop()
            await asyncio.gather(*bridge.tasks, return_exceptions=True)
            mpris.stop()
            await asyncio.to_thread(server.close)


class MprisTest(unittest.TestCase):
    def test_private_bus(self):
        try:
            import gi
        except ImportError:
            self.skipTest("python-gobject is not installed")
        for exe in ("dbus-run-session", "gdbus", "mpv"):
            if not Path("/usr/bin", exe).exists(): self.skipTest(exe + " is not installed")
        run = subprocess.run(["/usr/bin/dbus-run-session", "--", sys.executable, __file__, "--child"], capture_output=True, text=True, timeout=25)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("MPRIS_OK", run.stdout)
        self.assertNotIn("Traceback", run.stderr)


if __name__ == "__main__":
    if "--child" in sys.argv:
        asyncio.run(exercise())
    else:
        unittest.main()
