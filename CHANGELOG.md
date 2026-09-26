# Changelog

## 2.2.0 — Full power-off

- Make the power button, CLI quit, and MPRIS Quit exit both mpv and the bridge.
- Persist the off state and suppress reconnection/restart until explicit power-on.
- Keep the bar and panel power button available to launch Wax again.
- Stop the systemd unit if power-off is requested before IPC connects.

## 2.1.2 — Failure recovery and stale requests

- Stop leftover playback and publish the queue when a track load fails.
- Shut down mpv and clear playback state after a failed engine start.
- Invalidate old search results immediately when the query changes.
- Keep stale library replies from undoing refreshes or repopulating signed-out data.

## 2.1.1 — Upgrade and shutdown fixes

- Close client sockets and cancel pending requests before stopping the bridge.
- Remove stale UI leases after buffered attach requests finish.
- Reject malformed legacy connection files during migration.
- Ignore stale album/playlist responses after reopening or switching accounts.
- Exercise installer migration and repeat installs in an isolated test.

## 2.1.0 — Wax Player

- Rename the fork to Wax Player, with Wax in the bar and `bin/wax` as its CLI.
- Use `local.wax.player` for the plugin, service, settings, and saved data.
- Migrate the earlier Navidrome connection and queue once during installation.
- Retain the original Solfa license and attribution.

## 2.0.1 — Playback reliability

- Ignore stale end-of-track events while replacing a song.
- Keep favorite and playlist requests from blocking playback controls.
- Validate saved sessions before restoring the queue.
- Refetch missing cached artwork and report the clamped MPRIS seek position.

## 2.0.0 — Navidrome port

- Replace the YouTube/Chromium engine with a Navidrome API client and mpv.
- Add private connection storage and an in-panel server/login form.
- Preserve search, library, queue, artwork, lyrics, favorites, EQ, sleep timer,
  bar controls and keyboard navigation.
- Add MPRIS integration and local queue/position restoration.
- Separate plugin identity and data from upstream Solfa.
- Replace browser-specific tests with local API/mpv/D-Bus integration tests.

Based on upstream Solfa 1.0.2 by SirAllap; see Git history for earlier changes.
