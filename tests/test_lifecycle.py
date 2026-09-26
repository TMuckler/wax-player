"""The actual entry point and CLI, isolated from all user data and D-Bus."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LifecycleTests(unittest.TestCase):
    def test_daemon_socket_single_instance_cli_and_shutdown(self):
        with tempfile.TemporaryDirectory(prefix="wax-daemon-") as tmp:
            root = Path(tmp)
            env = dict(os.environ, XDG_RUNTIME_DIR=tmp, XDG_CONFIG_HOME=str(root / "config"),
                       XDG_STATE_HOME=str(root / "state"), XDG_CACHE_HOME=str(root / "cache"),
                       WAX_RUNTIME_DIR=str(root / "runtime"), WAX_NO_LAUNCH="1",
                       WAX_ORPHAN_SECONDS="0", DBUS_SESSION_BUS_ADDRESS="unix:path=" + str(root / "no-bus"))
            path = root / "runtime/bridge.sock"
            proc = subprocess.Popen([sys.executable, str(ROOT / "bin/wax-bridge")], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not path.exists() and time.monotonic() < deadline:
                    if proc.poll() is not None: self.fail(proc.communicate()[1].decode())
                    time.sleep(0.03)
                self.assertTrue(path.exists())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                second = subprocess.run([sys.executable, str(ROOT / "bin/wax-bridge")], env=env, capture_output=True, timeout=5)
                self.assertEqual(second.returncode, 0, second.stderr)
                status = subprocess.run([sys.executable, str(ROOT / "bin/wax"), "status"], env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(status.returncode, 0, status.stderr)
                self.assertIn("signed out", status.stdout)
                quit_reply = subprocess.run([sys.executable, str(ROOT / "bin/wax"), "quit"], env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(quit_reply.returncode, 0, quit_reply.stderr)
                stdout, stderr = proc.communicate(timeout=5)
                self.assertEqual(proc.returncode, 0, stderr.decode())
                self.assertFalse(path.exists())
                self.assertNotIn(b"Traceback", stderr)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                proc.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
