"""Render and exercise the real connection form, without a live server."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHELL = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "shell"


class ConnectionTest(unittest.TestCase):
    @unittest.skipUnless(Path("/usr/bin/qs").exists() and (SHELL / "Ui").exists(), "Quickshell/Omarchy unavailable")
    def test_connection_form(self):
        self.run_scene("ConnectionScene.qml", "CONNECTION_OK")

    @unittest.skipUnless(Path("/usr/bin/qs").exists() and (SHELL / "Ui").exists(), "Quickshell/Omarchy unavailable")
    def test_stale_detail_requests(self):
        self.run_scene("DetailRaceScene.qml", "DETAIL_OK")

    @unittest.skipUnless(Path("/usr/bin/qs").exists() and (SHELL / "Ui").exists(), "Quickshell/Omarchy unavailable")
    def test_search_and_library_request_races(self):
        self.run_scene("ListRaceScene.qml", "LISTS_OK")

    @unittest.skipUnless(Path("/usr/bin/qs").exists() and (SHELL / "Ui").exists(), "Quickshell/Omarchy unavailable")
    def test_power_off_stays_off_until_explicit_start(self):
        self.run_scene("PowerScene.qml", "POWER_OK")

    @unittest.skipUnless(Path("/usr/bin/qs").exists() and (SHELL / "Ui").exists(), "Quickshell/Omarchy unavailable")
    def test_power_button_exits_connected_bridge(self):
        with tempfile.TemporaryDirectory(prefix="wax-power-") as tmp:
            env = dict(os.environ, XDG_RUNTIME_DIR=tmp, XDG_CONFIG_HOME=tmp + "/config",
                       XDG_STATE_HOME=tmp + "/state", XDG_CACHE_HOME=tmp + "/cache",
                       WAX_RUNTIME_DIR=tmp + "/local.wax.player", WAX_NO_LAUNCH="1",
                       WAX_ORPHAN_SECONDS="0", WAX_LAUNCH_KEY="true", DBUS_SESSION_BUS_ADDRESS="unix:path=" + tmp + "/no-bus")
            bridge = subprocess.Popen([sys.executable, str(ROOT / "bin/wax-bridge")], env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not Path(env["WAX_RUNTIME_DIR"], "bridge.sock").exists() and time.monotonic() < deadline:
                    time.sleep(0.03)
                self.run_scene("PowerConnectedScene.qml", "CONNECTED_POWER_OK", env)
                _, stderr = bridge.communicate(timeout=5)
                self.assertEqual(bridge.returncode, 0, stderr.decode())
            finally:
                if bridge.poll() is None: bridge.terminate()
                bridge.communicate(timeout=5)

    def run_scene(self, scene, expected, environment=None):
        with tempfile.TemporaryDirectory(prefix="wax-connection-") as temp:
            cfg = Path(temp)
            for name, target in (("Ui", SHELL / "Ui"), ("Commons", SHELL / "Commons"), ("views", ROOT / "views"),
                                 ("lib", ROOT / "lib"), ("WaxService.qml", ROOT / "Service.qml"),
                                 ("manifest.json", ROOT / "manifest.json"), ("shell.qml", ROOT / "tests/qml" / scene)):
                (cfg / name).symlink_to(target)
            env = dict(environment or os.environ, QT_QPA_PLATFORM="offscreen")
            if environment is None: env["XDG_RUNTIME_DIR"] = temp
            run = subprocess.run(["/usr/bin/qs", "-p", str(cfg / "shell.qml"), "--no-color"], env=env, capture_output=True, text=True, timeout=15)
            log = run.stdout + run.stderr
            self.assertIn(expected, log, log)
            self.assertNotIn("FAIL!", log, log)
            self.assertNotIn("TypeError", log, log)
            self.assertNotIn("ReferenceError", log, log)
            self.assertNotIn("Binding loop", log, log)


if __name__ == "__main__":
    unittest.main()
