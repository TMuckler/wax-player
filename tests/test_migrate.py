"""Migration preserves user data and never resurrects a signed-out account."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.migrate import migrate, OLD_ID, NEW_ID
from backend.navidrome import private_json


class MigrationTests(unittest.TestCase):
    def test_copy_once_preserve_sources_and_respect_signout(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, state = Path(tmp) / "config", Path(tmp) / "state"
            old = config / OLD_ID / "connection.json"
            new = config / NEW_ID / "connection.json"
            creds = {"url": "http://example.test", "username": "test", "token": "test-token", "salt": "test-salt"}
            session = {"identity": "http://example.test\0test", "items": []}
            private_json(old, creds)
            private_json(state / OLD_ID / "session.json", session)
            migrate(config, state)
            self.assertEqual(json.loads(new.read_text()), creds)
            self.assertEqual(json.loads((state / NEW_ID / "session.json").read_text()), session)
            self.assertEqual(new.stat().st_mode & 0o777, 0o600)
            self.assertTrue(old.exists())
            new.unlink()
            migrate(config, state)
            self.assertFalse(new.exists())

    def test_corrupt_legacy_connection_is_not_installed(self):
        for payload in ("{broken", "[]", '{"url":"http://example.test","username":"test"}'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                config, state = Path(tmp) / "config", Path(tmp) / "state"
                source = config / OLD_ID / "connection.json"
                source.parent.mkdir(parents=True)
                source.write_text(payload)
                result = migrate(config, state)
                self.assertEqual(result, "invalid-connection")
                self.assertFalse((config / NEW_ID / "connection.json").exists())
                self.assertEqual(source.read_text(), payload)

    def test_existing_wax_account_is_not_mixed_with_old_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, state = Path(tmp) / "config", Path(tmp) / "state"
            new = config / NEW_ID / "connection.json"
            private_json(new, {"username": "new"})
            private_json(config / OLD_ID / "connection.json", {"username": "old"})
            private_json(state / OLD_ID / "session.json", {"items": []})
            migrate(config, state)
            self.assertEqual(json.loads(new.read_text()), {"username": "new"})
            self.assertFalse((state / NEW_ID / "session.json").exists())


if __name__ == "__main__":
    unittest.main()
