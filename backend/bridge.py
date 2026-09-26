"""Wax's private JSON-lines service: API, playback, queue and persistence."""
import asyncio
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import random
import signal
import time
import urllib.parse

from .navidrome import Client, Error, credentials, private_json, text
from .player import Player

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ID = "local.wax.player"
VERSION = json.loads((ROOT / "manifest.json").read_text())["version"]
MAX_QUEUE = 10000


def number(value, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise Error("bad-args")
    if integer and int(value) != value:
        raise Error("bad-args")
    return int(value) if integer else value


def boolean(value):
    if not isinstance(value, bool):
        raise Error("bad-args")
    return value


def choice(value, values):
    if value not in values:
        raise Error("bad-args")
    return value


class Bridge:
    def __init__(self, runtime=None, config=None, state=None, cache=None, player_factory=Player):
        home = Path.home()
        self.runtime = Path(runtime or os.environ.get("WAX_RUNTIME_DIR") or Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / PLUGIN_ID)
        self.config_path = Path(config or Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / PLUGIN_ID / "connection.json")
        self.state_path = Path(state or Path(os.environ.get("XDG_STATE_HOME", home / ".local/state")) / PLUGIN_ID / "session.json")
        self.cache = Path(cache or Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")) / PLUGIN_ID)
        self.api = None
        self.account = {"signedIn": False, "url": "", "username": ""}
        self.status = "stopped"
        self.error = ""
        self.want_running = not bool(os.environ.get("WAX_NO_LAUNCH"))
        self.start_paused = os.environ.get("WAX_START_PAUSED") == "1"
        self.start_volume = None
        raw_volume = os.environ.get("WAX_START_VOLUME", "")
        if raw_volume.isdigit():
            self.start_volume = max(0, min(100, int(raw_volume)))
        self.player = player_factory(self.player_event)
        self.items = []
        self.index = -1
        self.position = 0
        self.duration = 0
        self.volume = 75
        self.muted = False
        self.repeat = "NONE"
        self.playing = False
        self.buffering = False
        self.loaded = False
        self.queue_version = 0
        self.epoch = 0
        self.eq = {"bands": [0] * 10, "preamp": 0, "loudness": False}
        self.clients = set()
        self.ui = set()
        self.ui_last = time.monotonic()
        self.lock = asyncio.Lock()
        self.library_lock = asyncio.Lock()
        self.done = asyncio.Event()
        self.tasks = set()
        self.mpris = None
        self.listened = 0
        self.listen_at = time.monotonic()
        self.scrobbled = False
        self.saved_at = 0
        self.resume_playing = False
        try:
            config_data = json.loads(self.config_path.read_text())
            self.api = Client(config_data, self.cache)
            self.account.update(url=self.api.config["url"], username=self.api.config["username"])
            saved = json.loads(self.state_path.read_text())
            identity = self.api.config["url"] + "\0" + self.api.config["username"]
            if isinstance(saved, dict) and saved.get("identity") == identity:
                items = saved.get("items", [])
                if not isinstance(items, list):
                    raise Error("bad-session")
                items = items[:MAX_QUEUE]
                for item in items:
                    if not isinstance(item, dict):
                        raise Error("bad-session")
                    text(item.get("trackId"))
                    number(item.get("duration", 0), 0, 864000)
                    for key in ("title", "thumb", "coverId"):
                        if not isinstance(item.get(key, ""), str):
                            raise Error("bad-session")
                    artists, album = item.get("artists", []), item.get("album", {})
                    if not isinstance(artists, list) or any(not isinstance(a, dict) or not isinstance(a.get("name"), str) for a in artists):
                        raise Error("bad-session")
                    if not isinstance(album, dict) or not isinstance(album.get("name", ""), str):
                        raise Error("bad-session")
                index = number(saved.get("index", -1), -1, max(-1, len(items) - 1), True)
                position = number(saved.get("position", 0), 0, 864000)
                volume = number(saved.get("volume", 75), 0, 100)
                muted = boolean(saved.get("muted", False))
                repeat = choice(saved.get("repeat", "NONE"), ("NONE", "ALL", "ONE"))
                playing = boolean(saved.get("playing", False))
                # Adopt a session only after all fields have passed validation.
                self.items, self.index, self.position = items, index, position
                self.volume, self.muted, self.repeat = volume, muted, repeat
                self.resume_playing = playing
        except (OSError, ValueError, KeyError, TypeError, Error):
            pass

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    @property
    def current(self):
        return self.items[self.index] if 0 <= self.index < len(self.items) else {}

    def snapshot(self):
        return dict(self.current, position=self.position, duration=self.duration or self.current.get("duration", 0),
                    playing=self.playing, playbackStatus="Playing" if self.playing else "Paused" if self.loaded else "Stopped",
                    buffering=self.buffering, volume=self.volume, muted=self.muted,
                    repeat=self.repeat, at=int(time.time() * 1000))

    def engine(self):
        return {"status": self.status, "error": self.error, "wantRunning": self.want_running,
                "signedIn": self.account["signedIn"], "backend": "navidrome", "mpris": bool(self.mpris and self.mpris.available)}

    def hello(self):
        return {"version": VERSION, "launchKey": os.environ.get("WAX_LAUNCH_KEY", ""), "engine": self.engine(),
                "account": self.account, "player": self.snapshot(), "queueVersion": self.queue_version}

    def write(self, writer, msg):
        if writer.is_closing():
            return
        if writer.transport.get_write_buffer_size() > 2 << 20:
            writer.close()
            return
        writer.write((json.dumps(msg, separators=(",", ":")) + "\n").encode())

    def emit(self, event, data):
        for writer in tuple(self.clients):
            self.write(writer, {"event": event, "data": data})
        if event == "player" and self.mpris:
            self.mpris.update(data)

    def publish(self):
        self.emit("player", self.snapshot())

    def changed_queue(self):
        self.queue_version += 1
        self.emit("queue", {"version": self.queue_version})
        self.save()
        self.publish()

    def save(self):
        if not self.api:
            return
        private_json(self.state_path, {"identity": self.api.config["url"] + "\0" + self.api.config["username"],
                     "items": self.items, "index": self.index, "position": self.position, "playing": self.playing,
                     "volume": self.volume, "muted": self.muted, "repeat": self.repeat})
        self.saved_at = time.monotonic()

    def player_event(self, event):
        now = time.monotonic()
        if self.playing and not self.buffering:
            self.listened += max(0, min(5, now - self.listen_at))
        self.listen_at = now
        if event["event"] == "property-change":
            name, value = event.get("name"), event.get("data")
            if value is None:
                return
            if name == "time-pos":
                self.position = max(0, value)
            elif name == "duration":
                self.duration = max(0, value)
            elif name == "pause":
                self.playing = not value and self.loaded
            elif name == "volume":
                self.volume = value
            elif name == "mute":
                self.muted = value
            elif name == "paused-for-cache":
                self.buffering = value
            elif name == "idle-active" and value:
                self.playing = False
            threshold = min(240, self.duration / 2) if self.duration > 0 else 240
            if self.current and self.listened >= threshold and not self.scrobbled:
                self.scrobbled = True
                self.spawn(self.scrobble(self.api, self.current["trackId"], True))
            self.publish()
            if now - self.saved_at > 10:
                self.save()
        elif event["event"] == "end-file":
            if event.get("reason") == "eof" and self.loaded:
                self.spawn(self.finished(self.epoch))
            elif event.get("reason") == "error":
                self.playing = False
                self.loaded = False
                self.error = "playback-error"
                self.emit("engine", self.engine())
                self.publish()
        elif event["event"] == "disconnected":
            self.playing = False
            self.loaded = False
            self.status = "stopped"
            self.want_running = False
            self.error = "player-closed"
            self.emit("engine", self.engine())
            self.publish()

    async def scrobble(self, api, ident, submission):
        if api:
            with contextlib.suppress(Error):
                await api.request("scrobble", id=ident, submission="true" if submission else "false")

    async def finished(self, epoch):
        async with self.lock:
            if epoch != self.epoch or not self.loaded:
                return
            try:
                if self.repeat == "ONE":
                    await self.load(self.index)
                else:
                    await self.advance(1, eof=True)
            except Error as e:
                self.error = e.code
                self.playing = False
                self.emit("engine", self.engine())
                self.publish()

    async def start(self):
        self.want_running = True
        self.error = ""
        if not self.api:
            self.status = "unconfigured"
            self.emit("engine", self.engine())
            return
        self.status = "starting"
        self.emit("engine", self.engine())
        try:
            await self.api.request("ping")
            self.account["signedIn"] = True
            volume, muted = self.start_volume if self.start_volume is not None else self.volume, self.muted
            await self.player.start()
            self.status = "ready"
            await self.player.command("set_property", "volume", volume)
            await self.player.command("set_property", "mute", muted)
            await self.apply_eq(self.eq)
            if self.current and not self.loaded:
                await self.load(self.index, paused=self.start_paused or not self.resume_playing, position=self.position)
        except Error as e:
            self.error = e.code
            self.status = "stopped"
            self.want_running = False
            with contextlib.suppress(Error):
                await self.player.stop()
            self.loaded = self.playing = self.buffering = False
            self.publish()
            if e.code == "auth-failed":
                self.account["signedIn"] = False
            raise
        finally:
            self.emit("account", self.account)
            self.emit("engine", self.engine())

    async def stop(self):
        self.resume_playing = self.playing
        self.save()
        self.epoch += 1
        await self.player.stop()
        self.loaded = False
        self.playing = False
        self.want_running = False
        self.status = "stopped"
        self.emit("engine", self.engine())
        self.publish()

    async def load(self, index, paused=False, position=0):
        index = number(index, 0, len(self.items) - 1, True)
        if self.status != "ready":
            raise Error("engine-stopped")
        self.epoch += 1
        self.index = index
        self.loaded = False
        self.playing = False
        self.position = position
        self.duration = self.current.get("duration", 0)
        self.listened = 0
        self.scrobbled = False
        self.listen_at = time.monotonic()
        self.buffering = False
        try:
            thumb = self.current.get("thumb", "")
            if not thumb or (thumb.startswith("file:") and not Path(urllib.parse.unquote(urllib.parse.urlsplit(thumb).path)).is_file()):
                self.current["thumb"] = await self.api.cover(self.current.get("coverId"))
            await self.player.command("set_property", "pause", paused)
            # Per-file start option avoids racing a seek against file-loaded.
            await self.player.load(self.api.url("stream", id=self.current["trackId"]), position)
        except Error as e:
            # A rejected loadfile command can leave the previous song playing.
            with contextlib.suppress(Error):
                await self.player.command("stop")
            self.loaded = self.playing = self.buffering = False
            self.error = e.code
            self.changed_queue()
            self.emit("engine", self.engine())
            raise
        self.loaded = True
        self.playing = not paused
        self.error = ""
        self.changed_queue()
        self.spawn(self.scrobble(self.api, self.current["trackId"], False))

    async def advance(self, direction, eof=False):
        if not self.items:
            return
        if direction < 0 and self.position > 5 and not eof:
            await self.player.command("seek", 0, "absolute+exact")
            return
        index = self.index + direction
        if self.repeat == "ALL":
            index %= len(self.items)
        if not 0 <= index < len(self.items):
            if direction < 0:
                index = 0
            else:
                self.playing = False
                self.loaded = False
                await self.player.command("stop")
                self.save()
                self.publish()
                return
        await self.load(index, paused=not self.playing and not eof)

    async def apply_eq(self, args):
        bands = args.get("bands")
        if not isinstance(bands, list) or len(bands) != 10:
            raise Error("bad-args")
        bands = [number(v, -12, 12) for v in bands]
        preamp = number(args.get("preamp", 0), -12, 0)
        loudness = boolean(args.get("loudness", False))
        self.eq = {"bands": bands, "preamp": preamp, "loudness": loudness}
        filters = []
        if preamp:
            filters.append(f"volume={preamp}dB")
        for frequency, gain in zip((32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000), bands):
            if gain:
                filters.append(f"equalizer=f={frequency}:t=o:w=1:g={gain}")
        if loudness:
            filters.append("acompressor=threshold=0.125:ratio=3:attack=20:release=250:makeup=2")
        if self.status == "ready":
            await self.player.command("set_property", "af", "lavfi=[" + ",".join(filters) + "]" if filters else "")
        return {"applied": True}

    async def dispatch(self, op, args):
        if not isinstance(op, str) or not isinstance(args, dict):
            raise Error("bad-args")
        if op == "app.quit":
            self.emit("quit", {})
            self.done.set()
            return {"done": True}
        # Queries may run concurrently with transport, so an API request cannot stall pause.
        if op == "hello":
            return self.hello()
        if op == "state":
            return {"player": self.snapshot(), "account": self.account, "queueVersion": self.queue_version}
        if op == "queue":
            return {"items": self.items, "index": self.index, "automix": []}
        if op == "account.info":
            return dict(self.account, name=self.account.get("username", ""), email="", avatar="")
        if op == "engine.version":
            return {"product": "mpv / Navidrome", "version": VERSION}
        if op == "art":
            if not self.api:
                raise Error("signin-required")
            uri = await self.api.cover(text(args.get("coverId")))
            return {"path": urllib.parse.unquote(urllib.parse.urlsplit(uri).path), "uri": uri}
        if op in ("search", "browse", "home", "library", "lyrics", "suggest"):
            api = self.api
            if not api or not self.account["signedIn"]:
                raise Error("signin-required")
            if op == "search":
                offset = args.get("continuation", "0") or "0"
                if not isinstance(offset, str) or not offset.isdecimal() or len(offset) > 7:
                    raise Error("bad-args")
                group = choice(args.get("filter", ""), ("", "songs", "albums", "artists", "playlists"))
                return await api.search(text(args.get("q"), 200), group, int(offset))
            if op == "browse":
                offset = args.get("continuation", "0") or "0"
                if not isinstance(offset, str) or not offset.isdecimal() or len(offset) > 7:
                    raise Error("bad-args")
                return await api.browse(text(args.get("id")), int(offset))
            if op == "home":
                return await api.home()
            if op == "library":
                return await api.library(args.get("section"))
            if op == "lyrics":
                return await api.lyrics(text(args.get("trackId")))
            return {"suggestions": []}
        if op in ("like", "playlist.create", "playlist.add"):
            async with self.library_lock:
                return await self.library_mutate(op, args)
        async with self.lock:
            return await self.mutate(op, args)

    async def library_mutate(self, op, args):
        api = self.api
        if not api or not self.account["signedIn"]:
            raise Error("signin-required")
        if op == "like":
            ident = text(args.get("trackId"))
            status = choice(args.get("status"), ("LIKE", "INDIFFERENT"))
            await api.request("star" if status == "LIKE" else "unstar", id=ident)
            if api is not self.api:
                raise Error("account-changed")
            for item in self.items:
                if item["trackId"] == ident:
                    item["like"] = status
            self.changed_queue()
            if api is self.api:
                self.emit("library", {})
            return {"like": status}
        if op == "playlist.create":
            ids = args.get("trackIds", [])
            if not isinstance(ids, list) or len(ids) > MAX_QUEUE:
                raise Error("bad-args")
            result = await api.request("createPlaylist", name=text(args.get("title"), 150), songId=[text(i) for i in ids])
            if api is self.api:
                self.emit("library", {})
            return {"playlistId": "playlist:" + result["playlist"]["id"]}
        if op == "playlist.add":
            ident = text(args.get("playlistId"))
            if not ident.startswith("playlist:"):
                raise Error("bad-args")
            await api.request("updatePlaylist", playlistId=ident.split(":", 1)[1], songIdToAdd=text(args.get("trackId")))
            if api is self.api:
                self.emit("library", {})
            return {"added": True}

    async def mutate(self, op, args):
        if op == "connection.save":
            if not args.get("password") and self.api and args.get("url", "").rstrip("/") == self.api.config["url"] and args.get("username") == self.api.config["username"]:
                config = self.api.config
            else:
                config = credentials(args.get("url"), args.get("username"), args.get("password"))
            api = Client(config, self.cache)
            await api.request("ping")  # Failed sign-in leaves the old account intact.
            await self.stop()
            changed = not self.api or (self.api.config["url"], self.api.config["username"]) != (config["url"], config["username"])
            private_json(self.config_path, config)
            self.api = api
            if changed:
                self.items, self.index, self.position = [], -1, 0
                self.resume_playing = False
            self.account = {"signedIn": True, "url": config["url"], "username": config["username"]}
            self.changed_queue()
            await self.start()
            return {"connected": True}
        if op == "signout":
            await self.stop()
            self.config_path.unlink(missing_ok=True)
            self.state_path.unlink(missing_ok=True)
            self.api = None
            self.items, self.index, self.position = [], -1, 0
            self.account = {"signedIn": False, "url": "", "username": ""}
            self.status = "unconfigured"
            self.emit("account", self.account)
            self.emit("engine", self.engine())
            self.changed_queue()
            return {"done": True}
        if op == "engine.start":
            await self.start()
            return self.engine()
        if op in ("engine.stop", "engine.restart"):
            await self.stop()
            if op == "engine.restart":
                await self.start()
            return self.engine()
        if op == "bridge.quit":
            self.done.set()
            return {"done": True}
        if op == "start.set":
            self.start_paused = boolean(args.get("paused", False))
            self.start_volume = None if args.get("volume") is None else number(args["volume"], 0, 100)
            return {"done": True}
        if op == "eq.set":
            return await self.apply_eq(args)
        if op == "cache.clear":
            for p in self.cache.glob("*/*.img"):
                p.unlink(missing_ok=True)
            return {"done": True}
        if not self.api or not self.account["signedIn"]:
            raise Error("signin-required")
        if self.status != "ready":
            raise Error("engine-stopped")
        if op == "play":
            ident = args.get("trackId")
            collection = args.get("playlistId")
            if collection:
                items = await self.api.playlist_tracks(text(collection))
            elif ident:
                items = [await self.api.song(text(ident))]
            else:
                raise Error("bad-args")
            if not items:
                raise Error("not-found")
            if len(items) > MAX_QUEUE:
                raise Error("queue-too-large")
            if boolean(args.get("shuffle", False)):
                random.shuffle(items)
            index = next((i for i, item in enumerate(items) if item["trackId"] == ident), 0)
            self.items = items
            await self.load(index)
            return {"done": True}
        if op == "radio":
            ident = text(args.get("trackId"))
            seed = await self.api.song(ident)
            try:
                rows = (await self.api.request("getSimilarSongs2", id=ident, count=50)).get("similarSongs2", {}).get("song", [])
            except Error as e:
                if e.code not in ("api-error", "not-found"):
                    raise
                rows = []
            if not rows:
                rows = (await self.api.request("getRandomSongs", size=50)).get("randomSongs", {}).get("song", [])
            self.items = [seed] + await self.api.items([r for r in rows if r["id"] != ident], art=False)
            await self.load(0)
            return {"done": True}
        if op == "transport":
            action = choice(args.get("action"), ("play", "pause", "toggle", "next", "previous", "stop"))
            if action == "stop":
                self.playing = False
                self.loaded = False
                self.position = 0
                self.epoch += 1
                await self.player.command("stop")
                self.save()
                self.publish()
            elif self.index < 0 and self.items and action in ("play", "toggle"):
                await self.load(0)
            elif action in ("next", "previous"):
                await self.advance(1 if action == "next" else -1)
            elif self.current:
                paused = action == "pause" or (action == "toggle" and self.playing)
                if not self.loaded:
                    if not paused:
                        await self.load(self.index)
                else:
                    await self.player.command("set_property", "pause", paused)
                    self.playing = not paused
                    self.save()
                    self.publish()
            return {"done": True}
        if op == "seek":
            value = number(args.get("seconds"), 0, 864000)
            value = min(value, self.duration) if self.duration else value
            await self.player.command("seek", value, "absolute+exact")
            if self.mpris:
                self.mpris.seeked(value)
            return {"done": True}
        if op == "volume":
            value = number(args.get("level"), 0, 100)
            await self.player.command("set_property", "volume", value)
            if value > 0:
                await self.player.command("set_property", "mute", False)
            return {"done": True}
        if op == "mute":
            await self.player.command("set_property", "mute", boolean(args.get("muted")))
            return {"done": True}
        if op == "repeat":
            self.repeat = choice(args.get("mode"), ("NONE", "ALL", "ONE"))
        elif op == "shuffle":
            # Keep the current occurrence and already-played prefix stable.
            tail = self.items[self.index + 1:]
            random.shuffle(tail)
            self.items[self.index + 1:] = tail
        elif op == "queue.add":
            ids = args.get("trackIds")
            if not isinstance(ids, list) or not 1 <= len(ids) <= 50:
                raise Error("bad-args")
            if len(self.items) + len(ids) > MAX_QUEUE:
                raise Error("queue-too-large")
            at = self.index + 1 if boolean(args.get("next", False)) else len(self.items)
            tracks = [await self.api.song(text(i)) for i in ids]
            self.items[at:at] = tracks
        elif op == "queue.move":
            source = number(args.get("from"), 0, len(self.items) - 1, True)
            target = number(args.get("to"), 0, len(self.items) - 1, True)
            item = self.items.pop(source)
            self.items.insert(target, item)
            if self.index == source:
                self.index = target
            elif source < self.index <= target:
                self.index -= 1
            elif target <= self.index < source:
                self.index += 1
        elif op in ("queue.remove", "queue.jump"):
            index = number(args.get("index"), 0, len(self.items) - 1, True)
            if op == "queue.jump":
                await self.load(index)
                return {"done": True}
            if index == self.index:
                raise Error("playing")
            self.items.pop(index)
            if index < self.index:
                self.index -= 1
        else:
            raise Error("bad-args")
        self.changed_queue()
        return {"done": True}

    async def client(self, reader, writer):
        self.clients.add(writer)
        for name, data in (("engine", self.engine()), ("account", self.account), ("player", self.snapshot())):
            self.write(writer, {"event": name, "data": data})
        requests = set()
        async def handle(req):
            ident = req.get("id") if isinstance(req, dict) else None
            try:
                if type(ident) is not int:
                    raise Error("bad-args")
                if self.done.is_set():
                    raise Error("engine-stopped")
                if req.get("op") == "ui.attach":
                    self.ui.add(writer)
                    result = {"attached": True}
                else:
                    result = await self.dispatch(req.get("op"), req.get("args", {}))
                self.write(writer, {"id": ident, "ok": True, "data": result})
            except Error as e:
                self.write(writer, {"id": ident, "ok": False, "error": e.code})
            except (KeyError, ValueError, TypeError):
                self.write(writer, {"id": ident, "ok": False, "error": "bad-args"})
            except Exception:
                # Never log request arguments or URLs: they may carry credentials.
                self.write(writer, {"id": ident, "ok": False, "error": "internal-error"})
        try:
            while line := await reader.readline():
                if len(requests) >= 32:
                    break
                try:
                    req = json.loads(line)
                except ValueError:
                    self.write(writer, {"id": None, "ok": False, "error": "bad-json"})
                    continue
                task = self.spawn(handle(req))
                requests.add(task)
                task.add_done_callback(requests.discard)
        except (OSError, ValueError):
            pass
        finally:
            # Buffered requests may attach the UI after EOF was already read.
            # Finish them before removing the connection from the lease set.
            try:
                await asyncio.gather(*requests, return_exceptions=True)
            finally:
                self.clients.discard(writer)
                if writer in self.ui:
                    self.ui.remove(writer)
                    self.ui_last = time.monotonic()
                writer.close()

    async def lease(self):
        seconds = float(os.environ.get("WAX_ORPHAN_SECONDS", "0"))
        while not self.done.is_set():
            await asyncio.sleep(5)
            if seconds > 0 and not self.ui and time.monotonic() - self.ui_last > seconds:
                self.done.set()
            # Bound artwork cache without deleting connection or library data.
            files = sorted(self.cache.glob("*/*.img"), key=lambda p: p.stat().st_mtime)
            for p in files[:-1000]:
                p.unlink(missing_ok=True)

    async def run(self):
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.runtime.chmod(0o700)
        with (self.runtime / "bridge.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            path = self.runtime / "bridge.sock"
            path.unlink(missing_ok=True)
            server = await asyncio.start_unix_server(self.client, path=path, limit=1 << 20)
            path.chmod(0o600)
            from .mpris import Mpris
            self.mpris = Mpris(self)
            self.mpris.start()
            for sig in (signal.SIGINT, signal.SIGTERM):
                asyncio.get_running_loop().add_signal_handler(sig, self.done.set)
            lease = self.spawn(self.lease())
            try:
                if self.want_running:
                    with contextlib.suppress(Error):
                        async with self.lock:
                            await self.start()
                await self.done.wait()
            finally:
                server.close()
                # Cancel requests before stopping mpv: an in-flight start or
                # connection change must not restart it during shutdown.
                pending = tuple(self.tasks)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for writer in tuple(self.clients):
                    writer.close()
                # Recent Python versions also wait for active client sockets.
                await server.wait_closed()
                await self.stop()
                self.mpris.stop()
                path.unlink(missing_ok=True)


def main():
    os.umask(0o077)
    asyncio.run(Bridge().run())
