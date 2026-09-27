# Wax Player design

```text
Bar / panel / CLI
        │ private JSON-lines Unix socket
  Python bridge ───── Navidrome Subsonic/OpenSubsonic REST API
        │ inherited socket pair                         │
        └──────────── mpv ◀──────── authenticated stream ┘
        │
        └──────────── MPRIS (GIO session D-Bus)
```

`Service.qml` owns the shell-side model. `backend/bridge.py` owns the queue,
playback lifecycle, state persistence, request dispatch and scrobbling.
`backend/navidrome.py` normalizes API results into the panel's data model,
fetches artwork and performs authenticated requests off the asyncio loop.
`backend/player.py` manages an mpv child and its asynchronous JSON IPC.
`backend/mpris.py` exports desktop controls through PyGObject/GIO on a
separate GLib thread, dispatching operations back to the asyncio loop.

The bridge runs as a transient systemd user service. Shell reconnection
does not interrupt music. mpv receives a private inherited socket; it has
no listening network port and no window. Losing that socket exits mpv.
The bridge's UI lease exits after the shell has been absent for 30 seconds.
There is no browser profile, CDP injection, hidden Hyprland window, or
browser memory recycler.

## Protocol

Requests: `{"id":7,"op":"search","args":{"q":"example"}}`

Replies: `{"id":7,"ok":true,"data":{...}}` or
`{"id":7,"ok":false,"error":"auth-failed"}`.

Events: `{"event":"player|engine|account|queue|library","data":{...}}`.

Requests have integer IDs. Errors use stable codes and do not echo server
URLs, credentials, or request arguments. The socket is mode 0600 in a
mode-0700 runtime directory. Large input lines and slow clients are bounded.

Song IDs are opaque strings in `trackId`; collection IDs use `album:ID`,
`artist:ID`, or `playlist:ID` in `browseId`/`playlistId`. This retains the
original UI's list navigation while making the collection type explicit.
The bridge tracks queue occurrences by index, so repeated songs can be
moved independently. It stores original IDs and metadata, never stream URLs.

The API powers `search`, `browse`, `home`, `library`, `lyrics`, `like`,
`playlist.create`, and `playlist.add`. The bridge/player handles `play`,
`transport`, `seek`, `volume`, `mute`, `repeat`, `shuffle`, `queue.*`,
`eq.set`, and engine lifecycle. `connection.save` verifies credentials
before replacing a working connection; `signout` stops audio and forgets
credentials/session. `hello` returns state without credential material.

`play` supports a song, collection, or both (start at that song within the
collection), plus `shuffle: true`. `transport` accepts `play`, `pause`,
`toggle`, `stop`, `next`, or `previous`. Search and album-list browsing use
string offsets in `continuation`. Covers are fetched lazily by visible row
delegates through `art {coverId}` and returned as local file URIs.

## State and limitations

mpv property events update playback state. Track loading waits for mpv's
`file-loaded` event, so immediate seek/pause requests cannot race loading.
EOF advances the client queue; repeat-one reloads the same occurrence.
Queue changes and periodic position snapshots are written atomically.
Restoration is scoped to the server URL and username. The shell retains
its progress interpolation, notifications and sleep timer.

The backend supports a maximum queue of 10,000 songs. It fetches complete
albums/playlists; artists are expanded through their albums. Playback has
ordinary track transitions, without a promise of gapless playback or
crossfade. Network/playback errors are reported; the power control retries
player startup. There is no server-side queue synchronization.

Credentials use Subsonic salted token authentication. The saved token/salt
pair is bearer-equivalent; only the user can read its file. The client
rejects redirects and verifies HTTPS certificates. Server URLs require HTTPS
unless the host is a numeric loopback address. This validation applies to
new credentials and saved connections before any authenticated URL is built. API calls have bounded
response sizes, concurrency, and timeouts. mpv receives a stream URL only
through its private socket, never argv. Artwork and public models contain
no authentication query strings.

## Tests

`tests/test_navidrome.py` covers authentication, base paths, IDs, artwork,
lyrics fallbacks, protocol errors, real mpv transport/EQ, duplicate queues,
EOF/repeat and persistence. `tests/test_mpris.py` exercises desktop controls
on a private D-Bus session. `tests/test_connection.py` exercises the real
QML form and password clearing. Existing model, geometry, socket retry,
closed-state and panel layout tests remain in the local suite.

Intentional shutdown uses `app.quit`: the bridge notifies the shell before
exiting, and the shell persists `poweredOff` in its plugin settings to suppress
restarts. `bridge.quit` remains an internal restart/update operation. Power-on
clears the setting and starts a new bridge; the power button also stops the
systemd unit if IPC is unavailable during startup.
