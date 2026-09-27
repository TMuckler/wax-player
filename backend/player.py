"""Event-driven mpv child with a private inherited IPC socket, no window."""
import asyncio
import contextlib
import json
import os
import socket
from .navidrome import Error


class Player:
    def __init__(self, on_event, executable="/usr/bin/mpv", extra_args=()):
        self.on_event = on_event
        self.executable = executable
        self.extra_args = extra_args
        self.process = None
        self.writer = None
        self.pending = {}
        self.serial = 0
        self.reader_task = None
        self.closing = False
        self.loading = None

    async def start(self):
        if self.process and self.process.returncode is None:
            return
        self.closing = False
        parent, child = socket.socketpair()
        try:
            self.process = await asyncio.create_subprocess_exec(
                self.executable, "--no-config", "--no-terminal", "--idle=yes", "--vid=no", "--audio-display=no",
                "--input-default-bindings=no", "--input-vo-keyboard=no", "--ytdl=no", "--network-timeout=15", "--tls-verify=yes",
                "--input-ipc-client=fd://" + str(child.fileno()), *self.extra_args,
                pass_fds=(child.fileno(),), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            parent.close()
            raise Error("mpv-missing") from None
        finally:
            child.close()
        reader, self.writer = await asyncio.open_connection(sock=parent, limit=1 << 20)
        self.reader_task = asyncio.create_task(self.read(reader))
        for i, prop in enumerate(("time-pos", "duration", "pause", "volume", "mute", "paused-for-cache", "idle-active")):
            await self.command("observe_property", i, prop)

    async def read(self, reader):
        try:
            while line := await reader.readline():
                msg = json.loads(line)
                future = self.pending.pop(msg.get("request_id"), None)
                if future and not future.done():
                    if msg.get("error") == "success":
                        future.set_result(msg.get("data"))
                    else:
                        future.set_exception(Error("playback-error"))
                elif "event" in msg:
                    if self.loading and not self.loading.done():
                        if msg["event"] == "file-loaded":
                            self.loading.set_result(None)
                        elif msg["event"] == "end-file" and msg.get("reason") == "error":
                            self.loading.set_exception(Error("playback-error"))
                    self.on_event(msg)
        except (OSError, ValueError):
            pass
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(Error("player-closed"))
            self.pending.clear()
            if self.loading and not self.loading.done():
                self.loading.set_exception(Error("player-closed"))
            if not self.closing:
                self.on_event({"event": "disconnected"})

    async def command(self, *args):
        if not self.writer or self.writer.is_closing():
            raise Error("player-closed")
        self.serial += 1
        ident = self.serial
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        try:
            self.writer.write((json.dumps({"command": args, "request_id": ident}) + "\n").encode())
            await self.writer.drain()
            return await asyncio.wait_for(future, 8)
        except (ConnectionError, TimeoutError):
            raise Error("playback-error") from None
        finally:
            self.pending.pop(ident, None)

    async def load(self, url, position=0):
        self.loading = asyncio.get_running_loop().create_future()
        try:
            await self.command("loadfile", url, "replace", -1, {"start": str(position)})
            await asyncio.wait_for(self.loading, 20)
        except TimeoutError:
            await self.command("stop")
            raise Error("playback-error") from None
        finally:
            if self.loading.done() and not self.loading.cancelled():
                self.loading.exception()
            self.loading = None

    async def stop(self):
        self.closing = True
        # Closing the inherited IPC connection also tells mpv to exit.
        if self.writer:
            self.writer.close()
            with contextlib.suppress(OSError):
                await self.writer.wait_closed()
        if self.process and self.process.returncode is None:
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader_task:
            await self.reader_task
        self.writer = None
