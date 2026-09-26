"""One-time migration from the pre-rename Navidrome fork."""
import json
from pathlib import Path

from .navidrome import Client, Error, private_json

OLD_ID = "local.solfa.navidrome"
NEW_ID = "local.wax.player"


def migrate(config_home, state_home):
    config_home, state_home = Path(config_home), Path(state_home)
    marker = config_home / NEW_ID / "migration.json"
    if marker.exists():
        return
    target_config = config_home / NEW_ID / "connection.json"
    # An existing Wax connection owns its session; never mix accounts.
    if not target_config.exists():
        source = config_home / OLD_ID / "connection.json"
        if source.exists():
            try:
                config = json.loads(source.read_text())
                if not isinstance(config, dict):
                    raise ValueError("invalid connection")
                # Validate locally; migration must never contact the server.
                Client(config, config_home / NEW_ID)
            except (ValueError, KeyError, TypeError, Error):
                private_json(marker, {"from": OLD_ID})
                return "invalid-connection"
            session_source = state_home / OLD_ID / "session.json"
            target_session = state_home / NEW_ID / "session.json"
            session = None
            if session_source.exists() and not target_session.exists():
                try:
                    candidate = json.loads(session_source.read_text())
                    if isinstance(candidate, dict) and candidate.get("identity") == config["url"] + "\0" + config["username"]:
                        session = candidate
                except (ValueError, KeyError, TypeError):
                    pass  # A malformed session must not prevent migrating credentials.
            if session is not None:
                private_json(target_session, session)
            private_json(target_config, config)
    # Keep the marker after sign-out so reinstalling cannot restore credentials.
    private_json(marker, {"from": OLD_ID})
