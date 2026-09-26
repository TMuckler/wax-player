"""MPRIS service via PyGObject/GIO; playback continues if D-Bus is absent."""
import asyncio
import hashlib
import threading

PATH = "/org/mpris/MediaPlayer2"
ROOT = "org.mpris.MediaPlayer2"
PLAYER = ROOT + ".Player"
XML = '''<node>
<interface name="org.mpris.MediaPlayer2">
 <method name="Raise"/><method name="Quit"/>
 <property name="CanQuit" type="b" access="read"/>
 <property name="CanRaise" type="b" access="read"/>
 <property name="HasTrackList" type="b" access="read"/>
 <property name="Identity" type="s" access="read"/>
 <property name="SupportedUriSchemes" type="as" access="read"/>
 <property name="SupportedMimeTypes" type="as" access="read"/>
</interface>
<interface name="org.mpris.MediaPlayer2.Player">
 <method name="Next"/><method name="Previous"/><method name="Pause"/>
 <method name="PlayPause"/><method name="Stop"/><method name="Play"/>
 <method name="Seek"><arg name="Offset" type="x" direction="in"/></method>
 <method name="SetPosition"><arg name="TrackId" type="o" direction="in"/><arg name="Position" type="x" direction="in"/></method>
 <method name="OpenUri"><arg name="Uri" type="s" direction="in"/></method>
 <signal name="Seeked"><arg name="Position" type="x"/></signal>
 <property name="PlaybackStatus" type="s" access="read"/>
 <property name="LoopStatus" type="s" access="readwrite"/>
 <property name="Rate" type="d" access="readwrite"/>
 <property name="Metadata" type="a{sv}" access="read"/>
 <property name="Volume" type="d" access="readwrite"/>
 <property name="Position" type="x" access="read"/>
 <property name="MinimumRate" type="d" access="read"/>
 <property name="MaximumRate" type="d" access="read"/>
 <property name="CanGoNext" type="b" access="read"/>
 <property name="CanGoPrevious" type="b" access="read"/>
 <property name="CanPlay" type="b" access="read"/>
 <property name="CanPause" type="b" access="read"/>
 <property name="CanSeek" type="b" access="read"/>
 <property name="CanControl" type="b" access="read"/>
</interface></node>'''


class Mpris:
    def __init__(self, bridge):
        self.bridge = bridge
        self.loop = asyncio.get_running_loop()
        self.available = False
        self.connection = None
        self.owner = None
        self.state = bridge.snapshot()
        self.previous = {}
        self.thread = None
        self.stopping = False

    def start(self):
        try:
            from gi.repository import Gio, GLib
        except ImportError:
            return
        self.Gio, self.GLib = Gio, GLib
        self.main_loop = GLib.MainLoop()
        self.thread = threading.Thread(target=self.run, daemon=True, name="wax-mpris")
        self.thread.start()

    def run(self):
        Gio = self.Gio
        def acquired(connection, name):
            if self.stopping:
                return
            self.connection = connection
            info = Gio.DBusNodeInfo.new_for_xml(XML)
            self.registrations = [connection.register_object(i_path, interface, self.method, self.get_property, self.set_property)
                                  for i_path, interface in [(PATH, i) for i in info.interfaces]]
        def named(connection, name):
            self.available = True
            self.loop.call_soon_threadsafe(self.bridge.emit, "engine", self.bridge.engine())
        def lost(connection, name):
            self.available = False
        self.owner = Gio.bus_own_name(Gio.BusType.SESSION, ROOT + ".wax_player", Gio.BusNameOwnerFlags.NONE,
                                     acquired, named, lost)
        if not self.stopping:
            self.main_loop.run()
        if self.connection:
            for reg in getattr(self, "registrations", []):
                self.connection.unregister_object(reg)
        Gio.bus_unown_name(self.owner)

    def track_path(self):
        ident = self.state.get("trackId")
        return PATH + "/track/" + hashlib.sha256(ident.encode()).hexdigest() if ident else PATH + "/TrackList/NoTrack"

    def properties(self, interface):
        V = self.GLib.Variant
        if interface == ROOT:
            return {"CanQuit": V("b", True), "CanRaise": V("b", False), "HasTrackList": V("b", False),
                    "Identity": V("s", "Wax Player"), "SupportedUriSchemes": V("as", []), "SupportedMimeTypes": V("as", [])}
        s = self.state
        metadata = {"mpris:trackid": V("o", self.track_path())}
        if s.get("trackId"):
            metadata.update({"xesam:title": V("s", s.get("title", "")),
                             "xesam:artist": V("as", [a["name"] for a in s.get("artists", [])]),
                             "xesam:album": V("s", s.get("album", {}).get("name", "")),
                             "mpris:length": V("x", int(s.get("duration", 0) * 1e6)),
                             "mpris:artUrl": V("s", s.get("thumb", ""))})
        out = {"PlaybackStatus": V("s", s.get("playbackStatus", "Stopped")),
               "LoopStatus": V("s", {"NONE": "None", "ONE": "Track", "ALL": "Playlist"}.get(s.get("repeat"), "None")),
               "Metadata": V("a{sv}", metadata), "Volume": V("d", s.get("volume", 0) / 100),
               "Position": V("x", int(s.get("position", 0) * 1e6)),
               "Rate": V("d", 1.0), "MinimumRate": V("d", 1.0), "MaximumRate": V("d", 1.0), "CanControl": V("b", True)}
        out.update({key: V("b", bool(s.get("trackId"))) for key in ("CanGoNext", "CanGoPrevious", "CanPlay", "CanPause", "CanSeek")})
        return out

    def get_property(self, connection, sender, path, interface, name):
        return self.properties(interface).get(name)

    def submit(self, op, args, invocation=None):
        future = asyncio.run_coroutine_threadsafe(self.bridge.dispatch(op, args), self.loop)
        def complete(f):
            try:
                f.result()
                if invocation:
                    self.GLib.idle_add(lambda: invocation.return_value(None))
            except Exception:
                if invocation:
                    self.GLib.idle_add(lambda: invocation.return_dbus_error(ROOT + ".Error", "Playback request failed"))
        future.add_done_callback(complete)

    def method(self, connection, sender, path, interface, name, parameters, invocation):
        values = parameters.unpack()
        actions = {"Next": "next", "Previous": "previous", "Play": "play", "Pause": "pause", "PlayPause": "toggle", "Stop": "stop"}
        if name in actions:
            self.submit("transport", {"action": actions[name]}, invocation)
        elif name == "Quit":
            self.submit("app.quit", {}, invocation)
        elif name == "Seek":
            pos = max(0, self.state.get("position", 0) + values[0] / 1e6)
            if self.state.get("duration", 0) and pos > self.state["duration"]:
                self.submit("transport", {"action": "next"}, invocation)
            else:
                self.submit("seek", {"seconds": pos}, invocation)
        elif name == "SetPosition" and values[0] == self.track_path() and 0 <= values[1] / 1e6 <= self.state.get("duration", 0):
            self.submit("seek", {"seconds": values[1] / 1e6}, invocation)
        else:
            invocation.return_value(None)

    def set_property(self, connection, sender, path, interface, name, value):
        value = value.unpack()
        if name == "Volume":
            self.submit("volume", {"level": max(0, min(100, value * 100))})
        elif name == "LoopStatus" and value in ("None", "Track", "Playlist"):
            self.submit("repeat", {"mode": {"None": "NONE", "Track": "ONE", "Playlist": "ALL"}[value]})
        elif name != "Rate":
            return False
        return True

    def update(self, state):
        self.state = state
        if not self.available:
            return
        def emit():
            if not self.available or self.stopping:
                return False
            props = self.properties(PLAYER)
            props.pop("Position", None)
            changed = {k: v for k, v in props.items() if self.previous.get(k) != v}
            self.previous = props
            if changed:
                self.connection.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                            self.GLib.Variant("(sa{sv}as)", (PLAYER, changed, [])))
            return False
        self.GLib.idle_add(emit)

    def seeked(self, seconds):
        if self.available:
            def emit():
                if not self.stopping:
                    self.connection.emit_signal(None, PATH, PLAYER, "Seeked", self.GLib.Variant("(x)", (int(seconds * 1e6),)))
                return False
            self.GLib.idle_add(emit)

    def stop(self):
        self.stopping = True
        self.available = False
        if self.thread:
            self.GLib.idle_add(self.main_loop.quit)
            self.thread.join(timeout=2)
