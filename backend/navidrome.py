"""Navidrome/OpenSubsonic client. No credentials are exposed in UI models."""
import asyncio
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import urllib.error
import urllib.parse
import urllib.request


class Error(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def text(value, maximum=512):
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise Error("bad-args")
    return value


def base_url(value):
    value = text(value, 2048).strip().rstrip("/")
    try:
        p = urllib.parse.urlsplit(value)
        if p.scheme not in ("https", "http") or not p.hostname or p.username is not None or p.password is not None or p.query or p.fragment:
            raise Error("bad-server-url")
        p.port
    except ValueError:
        raise Error("bad-server-url") from None
    if p.scheme == "http":
        # Numeric loopback only: LAN addresses still cross the network, and
        # hostnames can resolve somewhere else. Never send tokens to those.
        try:
            address = ipaddress.ip_address(p.hostname)
            loopback = address.is_loopback and (address.version == 4 or address == ipaddress.IPv6Address("::1"))
        except ValueError:
            loopback = False
        if not loopback or "%" in p.hostname:
            raise Error("https-required")
    return value


def private_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def credentials(url, username, password):
    salt = secrets.token_hex(16)
    return {"url": base_url(url), "username": text(username), "salt": salt,
            "token": hashlib.md5((text(password, 4096) + salt).encode()).hexdigest()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward bearer-equivalent query credentials to another endpoint.
        raise Error("server-redirect")


class Client:
    def __init__(self, config, cache):
        self.config = dict(config)
        self.config["url"] = base_url(config["url"])
        for key in ("username", "salt", "token"):
            text(config[key])
        self.cache = Path(cache) / hashlib.sha256((config["url"] + "\0" + config["username"]).encode()).hexdigest()[:20]
        self.slots = asyncio.Semaphore(6)
        self.art_tasks = {}

    def url(self, endpoint, **params):
        q = dict(u=self.config["username"], t=self.config["token"], s=self.config["salt"],
                 v="1.16.1", c="wax-player", f="json")
        q.update(params)
        return self.config["url"] + "/rest/" + endpoint + ".view?" + urllib.parse.urlencode(q, doseq=True)

    def _get(self, endpoint, params, binary):
        try:
            req = urllib.request.Request(self.url(endpoint, **params), headers={"User-Agent": "Wax-Player/2"})
            with urllib.request.build_opener(NoRedirect).open(req, timeout=15) as r:
                data = r.read((8 if binary else 32) * 1024 * 1024 + 1)
                if len(data) > (8 if binary else 32) * 1024 * 1024:
                    raise Error("response-too-large")
                if binary:
                    if not r.headers.get_content_type().startswith("image/"):
                        raise Error("art-failed")
                    return data
            result = json.loads(data)["subsonic-response"]
            if result.get("status") != "ok":
                code = result.get("error", {}).get("code")
                raise Error({40: "auth-failed", 41: "auth-failed", 50: "forbidden", 70: "not-found"}.get(code, "api-error"))
            return result
        except urllib.error.HTTPError as e:
            raise Error("auth-failed" if e.code in (401, 403) else "http-" + str(e.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Error("server-unreachable") from None
        except (ValueError, KeyError, TypeError):
            raise Error("invalid-response") from None

    async def request(self, endpoint, **params):
        async with self.slots:
            return await asyncio.to_thread(self._get, endpoint, params, False)

    async def cover(self, cover_id):
        if not cover_id:
            return ""
        key = hashlib.sha256(str(cover_id).encode()).hexdigest()
        path = self.cache / (key + ".img")
        if path.exists():
            return path.as_uri()
        async def fetch():
            try:
                async with self.slots:
                    data = await asyncio.to_thread(self._get, "getCoverArt", {"id": cover_id, "size": 320}, True)
                self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.write_bytes(data)
                path.chmod(0o600)
                return path.as_uri()
            except Error:
                return ""
        if key not in self.art_tasks:
            self.art_tasks[key] = asyncio.create_task(fetch())
        try:
            return await self.art_tasks[key]
        finally:
            self.art_tasks.pop(key, None)

    async def item(self, raw, kind="song", art=True):
        ident = str(raw["id"])
        artists = [{"id": "artist:" + str(a["id"]), "name": a.get("name", "")} for a in raw.get("artists", []) if a.get("id")]
        if not artists and raw.get("artist"):
            artists = [{"id": "artist:" + str(raw["artistId"]) if raw.get("artistId") else "", "name": raw["artist"]}]
        item = {"kind": kind, "title": raw.get("title") or raw.get("name") or "Untitled", "artists": artists,
                "coverId": raw.get("coverArt", ""), "thumb": await self.cover(raw.get("coverArt")) if art else "",
                "subtitle": raw.get("artist", "")}
        if kind == "song":
            item.update(trackId=ident, duration=raw.get("duration", 0), like="LIKE" if raw.get("starred") else "INDIFFERENT",
                        album={"id": "album:" + str(raw["albumId"]) if raw.get("albumId") else "", "name": raw.get("album", "")})
        else:
            item["browseId"] = kind + ":" + ident
            if kind in ("album", "playlist"):
                item["playlistId"] = item["browseId"]
        return item

    async def items(self, rows, kind="song", art=True):
        return await asyncio.gather(*(self.item(r, kind, art) for r in rows))

    async def song(self, ident):
        return await self.item((await self.request("getSong", id=text(ident)))["song"])

    async def search(self, query, group="", offset=0):
        size = 50
        result = (await self.request("search3", query=query, songCount=size, albumCount=size, artistCount=size,
                                     songOffset=offset, albumOffset=offset, artistOffset=offset)).get("searchResult3", {})
        out = {}
        for plural, singular in (("songs", "song"), ("albums", "album"), ("artists", "artist")):
            rows = result.get(singular, [])
            out[plural] = await self.items(rows, singular, art=False)
        if group in ("", "playlists"):
            rows = (await self.request("getPlaylists")).get("playlists", {}).get("playlist", [])
            matches = [r for r in rows if query.casefold() in r.get("name", "").casefold()]
            out["playlists"] = await self.items(matches[offset:offset + size], "playlist", art=False)
        out["continuation"] = str(offset + size) if group and len(out.get(group, [])) == size else ""
        return out

    async def browse(self, ident, offset=0):
        kind, sep, key = text(ident).partition(":")
        if not sep:
            raise Error("bad-args")
        if kind in ("album", "playlist"):
            raw = (await self.request("getAlbum" if kind == "album" else "getPlaylist", id=key))[kind]
            result = await self.item(raw, kind)
            rows = raw.get("song" if kind == "album" else "entry", [])
            result.update(tracks=await self.items(rows, art=False), subtitle=str(len(rows)) + " songs")
            return result
        if kind == "artist":
            raw = (await self.request("getArtist", id=key))["artist"]
            result = await self.item(raw, kind)
            result.update(sections=[{"title": "Albums", "items": await self.items(raw.get("album", []), "album", art=False)}],
                          shuffle={"playlistId": ident}, radio={"playlistId": ident})
            return result
        if kind == "albums" and key in ("newest", "recent", "random", "alphabeticalByName"):
            rows = (await self.request("getAlbumList2", type=key, size=100, offset=offset)).get("albumList2", {}).get("album", [])
            return {"kind": "collection", "title": "Albums", "sections": [{"items": await self.items(rows, "album", art=False)}],
                    "continuation": str(offset + 100) if len(rows) == 100 else ""}
        raise Error("not-found")

    async def home(self):
        sections = []
        for title, kind in (("Recently added", "newest"), ("Recently played", "recent"), ("Random albums", "random")):
            rows = (await self.request("getAlbumList2", type=kind, size=8)).get("albumList2", {}).get("album", [])
            sections.append({"title": title, "items": await self.items(rows, "album"),
                             "more": {"browseId": "albums:" + kind} if kind != "random" else None})
        return {"sections": sections}

    async def library(self, section):
        if section == "songs":
            rows = (await self.request("getStarred2")).get("starred2", {}).get("song", [])
            return {"tracks": await self.items(rows, art=False)}
        if section == "albums":
            result = await self.browse("albums:alphabeticalByName")
            result["sections"][0].update(title="Albums", more={"browseId": "albums:alphabeticalByName"})
            return result
        if section == "artists":
            groups = (await self.request("getArtists")).get("artists", {}).get("index", [])
            rows = [a for g in groups for a in g.get("artist", [])]
            return {"sections": [{"items": await self.items(rows, "artist", art=False)}]}
        if section == "playlists":
            rows = (await self.request("getPlaylists")).get("playlists", {}).get("playlist", [])
            return {"sections": [{"items": await self.items(rows, "playlist", art=False)}]}
        raise Error("bad-args")

    async def playlist_tracks(self, ident):
        page = await self.browse(ident)
        if page["kind"] == "artist":
            tracks = []
            for album in page["sections"][0]["items"]:
                tracks.extend((await self.browse(album["browseId"]))["tracks"])
            return tracks
        return page.get("tracks", [])

    async def lyrics(self, ident):
        try:
            result = await self.request("getLyricsBySongId", id=ident)
            versions = result.get("lyricsList", {}).get("structuredLyrics", [])
            if versions:
                lyric = next((v for v in versions if v.get("synced")), versions[0])
                lines = lyric.get("line", [])
                if lyric.get("synced"):
                    offset = lyric.get("offset", 0)
                    return {"kind": "timed", "lines": sorted([{"t": max(0, r.get("start", 0) - offset), "text": r.get("value", "")} for r in lines], key=lambda r: r["t"]), "source": "Library"}
                return {"kind": "plain", "text": "\n".join(r.get("value", "") for r in lines), "source": "Library"}
        except Error as e:
            if e.code not in ("api-error", "http-404", "not-found"):
                raise
        song = await self.song(ident)
        result = (await self.request("getLyrics", artist=", ".join(a["name"] for a in song["artists"]), title=song["title"])).get("lyrics", {})
        value = result.get("value", "")
        return {"kind": "plain" if value else "none", "text": value, "lines": [], "source": "Library" if value else ""}
