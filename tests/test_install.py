"""Exercise installer file copies and migration without touching the desktop."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.migrate import OLD_ID, NEW_ID
from backend.navidrome import credentials, private_json


class InstallerTests(unittest.TestCase):
    @unittest.skipUnless(Path("/usr/bin/omarchy").exists(), "Omarchy unavailable")
    def test_install_migrates_final_queue_and_can_be_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, state = Path(tmp) / "config", Path(tmp) / "state"
            plugins = config / "omarchy/plugins"
            (plugins / OLD_ID).mkdir(parents=True)
            creds = credentials("https://example.test", "listener", "test-only")
            private_json(config / OLD_ID / "connection.json", creds)
            calls = []
            def command(argv, **kwargs):
                calls.append(argv)
                if argv[:3] == ["/usr/bin/systemctl", "--user", "stop"]:
                    private_json(state / OLD_ID / "session.json", {
                        "identity": "https://example.test\0listener", "items": [], "position": 42
                    })
                return subprocess.CompletedProcess(argv, 0)
            env = {"XDG_CONFIG_HOME": str(config), "XDG_STATE_HOME": str(state)}
            with patch.dict(os.environ, env), patch("subprocess.run", side_effect=command), contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(ROOT / "bin/install-local"), run_name="__main__")
                installed = plugins / NEW_ID
                self.assertEqual(json.loads((installed / "manifest.json").read_text())["id"], NEW_ID)
                self.assertTrue(os.access(installed / "bin/wax", os.X_OK))
                self.assertEqual(json.loads((config / NEW_ID / "connection.json").read_text()), creds)
                self.assertEqual(json.loads((state / NEW_ID / "session.json").read_text())["position"], 42)
                self.assertLess(next(i for i, c in enumerate(calls) if "disable" in c),
                                next(i for i, c in enumerate(calls) if "enable" in c))
                # A later update backs up Wax and respects a previous sign-out.
                (config / NEW_ID / "connection.json").unlink()
                runpy.run_path(str(ROOT / "bin/install-local"), run_name="__main__")
                self.assertFalse((config / NEW_ID / "connection.json").exists())
                self.assertTrue(list((state / NEW_ID / "install-backups").iterdir()))


if __name__ == "__main__":
    unittest.main()
