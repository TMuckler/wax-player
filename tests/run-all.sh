#!/usr/bin/env bash
# Local checks only. Playback tests use a mock HTTP server, null audio output
# and a private D-Bus session; UI scenes use temporary offscreen configurations.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m compileall -q backend bin/wax bin/wax-bridge bin/install-local
node --test tests/model.test.cjs
python3 tests/test_navidrome.py
python3 tests/test_review.py
python3 tests/test_migrate.py
python3 tests/test_install.py
python3 tests/test_mpris.py
python3 tests/test_lifecycle.py
python3 tests/test_connection.py
bash tests/lint-qml.sh
python3 tests/test_render.py
python3 tests/test_hit_targets.py
python3 tests/test_bridge_socket.py
python3 tests/test_closed.py
python3 tests/test_panel_settings.py
if command -v omarchy-plugin-validate >/dev/null 2>&1; then omarchy-plugin-validate .; fi
printf 'All local checks passed.\n'
