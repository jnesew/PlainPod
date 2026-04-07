from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import TYPE_CHECKING, Any

from dbus_next import PropertyAccess, Variant
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, dbus_property, method, signal
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from .player import PlayerController
    from .viewmodel import AppViewModel

logger = logging.getLogger(__name__)

MPRIS_SERVICE = "org.mpris.MediaPlayer2.io.github.jnesew.PlainPod"
MPRIS_PATH = "/org/mpris/MediaPlayer2"
IFACE_ROOT = "org.mpris.MediaPlayer2"
IFACE_PLAYER = "org.mpris.MediaPlayer2.Player"


def _track_path(episode_id: int) -> str:
    return f"/org/plainpod/track/{episode_id}"


def _desktop_entry() -> str:
    return (
        os.environ.get("FLATPAK_ID")
        or os.environ.get("G_APPLICATION_ID")
        or "io.github.jnesew.PlainPod"
    )


class _RootInterface(ServiceInterface):
    def __init__(self, svc: "MprisService") -> None:
        super().__init__(IFACE_ROOT)
        self._svc = svc

    @method(name="Raise")
    def raise_(self):
        self._svc.raise_requested.emit()

    @method(name="Quit")
    def quit_(self):
        self._svc.quit_requested.emit()

    @dbus_property(access=PropertyAccess.READ, name="CanQuit")
    def can_quit(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ, name="CanRaise")
    def can_raise(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ, name="HasTrackList")
    def has_track_list(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ, name="Identity")
    def identity(self) -> "s":
        return "PlainPod"

    @dbus_property(access=PropertyAccess.READ, name="DesktopEntry")
    def desktop_entry(self) -> "s":
        return _desktop_entry()

    @dbus_property(access=PropertyAccess.READ, name="SupportedUriSchemes")
    def supported_uri_schemes(self) -> "as":
        return ["http", "https", "file"]

    @dbus_property(access=PropertyAccess.READ, name="SupportedMimeTypes")
    def supported_mime_types(self) -> "as":
        return [
            "audio/mpeg",
            "audio/mp4",
            "audio/x-m4a",
            "audio/aac",
            "audio/opus",
            "audio/ogg",
            "application/ogg",
        ]


class _PlayerInterface(ServiceInterface):
    def __init__(self, svc: "MprisService") -> None:
        super().__init__(IFACE_PLAYER)
        self._svc = svc

    @method(name="Play")
    def play(self):
        self._svc.play_requested.emit()

    @method(name="Pause")
    def pause(self):
        self._svc.pause_requested.emit()

    @method(name="PlayPause")
    def play_pause(self):
        self._svc.play_pause_requested.emit()

    @method(name="Stop")
    def stop(self):
        self._svc.stop_requested.emit()

    @method(name="Next")
    def next_(self):
        pass

    @method(name="Previous")
    def previous(self):
        pass

    @method(name="Seek")
    def seek(self, offset_us: "x"):
        self._svc.seek_requested.emit(int(offset_us))

    @method(name="SetPosition")
    def set_position(self, track_id: "o", position_us: "x"):
        self._svc.set_position_requested.emit(str(track_id), int(position_us))

    @method(name="OpenUri")
    def open_uri(self, uri: "s"):
        pass

    @signal(name="Seeked")
    def seeked(self) -> "x":
        return int(self._svc.position_us)

    @dbus_property(access=PropertyAccess.READ, name="PlaybackStatus")
    def playback_status(self) -> "s":
        return self._svc.snapshot("playback_status")

    @dbus_property(access=PropertyAccess.READ, name="LoopStatus")
    def loop_status(self) -> "s":
        return "None"

    @dbus_property(access=PropertyAccess.READWRITE, name="Rate")
    def rate(self) -> "d":
        return float(self._svc.snapshot("playback_speed"))

    @rate.setter
    def rate(self, value: "d") -> None:
        self._svc.playback_speed_requested.emit(float(value))

    @dbus_property(access=PropertyAccess.READ, name="Shuffle")
    def shuffle(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ, name="Metadata")
    def metadata(self) -> "a{sv}":
        return self._svc.snapshot("metadata")

    @dbus_property(access=PropertyAccess.READWRITE, name="Volume")
    def volume(self) -> "d":
        return float(self._svc.snapshot("volume"))

    @volume.setter
    def volume(self, value: "d") -> None:
        self._svc.volume_requested.emit(float(value))

    @dbus_property(access=PropertyAccess.READ, name="Position")
    def position(self) -> "x":
        return int(self._svc.snapshot("position_us"))

    @dbus_property(access=PropertyAccess.READ, name="MinimumRate")
    def minimum_rate(self) -> "d":
        return 0.5

    @dbus_property(access=PropertyAccess.READ, name="MaximumRate")
    def maximum_rate(self) -> "d":
        return 3.0

    @dbus_property(access=PropertyAccess.READ, name="CanGoNext")
    def can_go_next(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ, name="CanGoPrevious")
    def can_go_previous(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ, name="CanPlay")
    def can_play(self) -> "b":
        return bool(self._svc.snapshot("has_track"))

    @dbus_property(access=PropertyAccess.READ, name="CanPause")
    def can_pause(self) -> "b":
        return bool(self._svc.snapshot("has_track"))

    @dbus_property(access=PropertyAccess.READ, name="CanSeek")
    def can_seek(self) -> "b":
        return bool(self._svc.snapshot("can_seek"))

    @dbus_property(access=PropertyAccess.READ, name="CanControl")
    def can_control(self) -> "b":
        return True


class MprisService(QObject):
    """Pure-Python MPRIS service using dbus-next."""

    play_requested = Signal()
    pause_requested = Signal()
    play_pause_requested = Signal()
    stop_requested = Signal()
    seek_requested = Signal(int)
    set_position_requested = Signal(str, int)
    volume_requested = Signal(float)
    playback_speed_requested = Signal(float)
    quit_requested = Signal()
    raise_requested = Signal()

    def __init__(self, vm: "AppViewModel", player: "PlayerController") -> None:
        super().__init__()
        self._vm = vm
        self._player = player

        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_error: Exception | None = None

        self._bus: MessageBus | None = None
        self._root_iface: _RootInterface | None = None
        self._player_iface: _PlayerInterface | None = None

        self._snapshot_lock = threading.Lock()
        self._snapshot_data: dict[str, Any] = {
            "playback_status": "Stopped",
            "playback_speed": float(self._vm.playback_speed),
            "volume": float(self._vm.volume),
            "position_us": 0,
            "has_track": False,
            "can_seek": False,
            "metadata": {
                "mpris:trackid": Variant("o", "/org/mpris/MediaPlayer2/TrackList/NoTrack"),
            },
        }
        self._position_us = 0

        self.play_requested.connect(self.play)
        self.pause_requested.connect(self.pause)
        self.play_pause_requested.connect(self.play_pause)
        self.stop_requested.connect(self.stop)
        self.seek_requested.connect(self.seek)
        self.set_position_requested.connect(self.set_position)
        self.volume_requested.connect(self.set_volume)
        self.playback_speed_requested.connect(self.set_playback_speed)
        self.quit_requested.connect(self._quit_app)
        self.raise_requested.connect(self._raise_app)

        vm.now_playing_title_changed.connect(self._sync_from_vm)
        vm.now_playing_podcast_changed.connect(self._sync_from_vm)
        vm.now_playing_episode_id_changed.connect(self._sync_from_vm)
        vm.playback_duration_ms_changed.connect(self._sync_from_vm)
        vm.is_playing_changed.connect(self._sync_from_vm)
        vm.volume_changed.connect(self._sync_from_vm)
        vm.playback_speed_changed.connect(self._sync_from_vm)
        vm.playback_position_ms_changed.connect(self._on_position)

        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(500)
        self._pos_timer.timeout.connect(self._tick_position)
        self._pos_timer.start()

        self._sync_from_vm()

    def register(self) -> bool:
        self._ready.clear()
        self._start_error = None
        self._thread = threading.Thread(target=self._dbus_thread_main, daemon=True)
        self._thread.start()

        if not self._ready.wait(timeout=5.0):
            logger.warning("MPRIS: timed out waiting for service startup")
            return False

        if self._start_error is not None:
            logger.warning("MPRIS: startup failed: %s", self._start_error)
            return False

        logger.info("MPRIS: registered %s", MPRIS_SERVICE)
        return True

    def unregister(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        self._thread = None
        self._loop = None
        self._stop_event = None
        self._bus = None
        self._root_iface = None
        self._player_iface = None
        logger.info("MPRIS: unregistered")

    @Slot()
    def play(self) -> None:
        if not self._vm.is_playing:
            self._vm.toggle_playback()

    @Slot()
    def pause(self) -> None:
        if self._vm.is_playing:
            self._vm.toggle_playback()

    @Slot()
    def play_pause(self) -> None:
        self._vm.toggle_playback()

    @Slot()
    def stop(self) -> None:
        self._player.pause()

    @Slot(int)
    def seek(self, offset_us: int) -> None:
        delta_ms = int(offset_us) // 1000
        target_ms = max(0, self._vm.playback_position_ms + delta_ms)
        self._vm.seek(target_ms)
        with self._snapshot_lock:
            self._snapshot_data["position_us"] = target_ms * 1000
            self._position_us = target_ms * 1000
        self._emit_seeked()

    @Slot(str, int)
    def set_position(self, track_id: str, position_us: int) -> None:
        eid = self._vm.now_playing_episode_id
        if eid < 0 or track_id != _track_path(eid):
            return
        target_ms = max(0, int(position_us) // 1000)
        self._vm.seek(target_ms)
        with self._snapshot_lock:
            self._snapshot_data["position_us"] = target_ms * 1000
            self._position_us = target_ms * 1000
        self._emit_seeked()

    @Slot(float)
    def set_volume(self, value: float) -> None:
        clamped = max(0.0, min(float(value), 1.0))
        self._vm.set_volume(clamped)

    @Slot(float)
    def set_playback_speed(self, value: float) -> None:
        clamped = max(0.5, min(float(value), 3.0))
        self._vm.set_playback_speed(clamped)

    @Slot()
    def _quit_app(self) -> None:
        QApplication.quit()

    @Slot()
    def _raise_app(self) -> None:
        pass

    def snapshot(self, key: str) -> Any:
        with self._snapshot_lock:
            value = self._snapshot_data.get(key)
            return value.copy() if isinstance(value, dict) else value

    def _sync_from_vm(self) -> None:
        vm = self._vm
        eid = vm.now_playing_episode_id
        has_track = eid >= 0 and bool(vm.now_playing_title)
        position_us = int(vm.playback_position_ms) * 1000

        if has_track:
            status = "Playing" if vm.is_playing else "Paused"
            metadata: dict[str, Variant] = {
                "mpris:trackid": Variant("o", _track_path(eid)),
                "xesam:title": Variant("s", vm.now_playing_title or ""),
                "xesam:album": Variant("s", vm.now_playing_podcast or ""),
                "mpris:length": Variant("x", max(0, vm.playback_duration_ms) * 1000),
            }

            episode = vm.repo.get_episode(eid)
            if episode is not None:
                podcast = next(
                    (p for p in vm.repo.list_podcasts() if p.id == episode.podcast_id),
                    None,
                )
                if podcast and podcast.artwork_url:
                    from .artwork_cache import cache_podcast_artwork

                    art = cache_podcast_artwork(podcast.artwork_url)
                    if art:
                        metadata["mpris:artUrl"] = Variant("s", art)
        else:
            status = "Stopped"
            metadata = {
                "mpris:trackid": Variant("o", "/org/mpris/MediaPlayer2/TrackList/NoTrack")
            }

        with self._snapshot_lock:
            self._snapshot_data.update(
                {
                    "playback_status": status,
                    "playback_speed": float(vm.playback_speed),
                    "volume": float(vm.volume),
                    "position_us": position_us,
                    "has_track": has_track,
                    "can_seek": vm.playback_duration_ms > 0,
                    "metadata": metadata,
                }
            )
            self._position_us = position_us

        self._emit_properties_changed()

    def _on_position(self) -> None:
        with self._snapshot_lock:
            pos_us = int(self._vm.playback_position_ms) * 1000
            self._snapshot_data["position_us"] = pos_us
            self._position_us = pos_us

        self._emit_properties_changed(position_only=True)

    def _tick_position(self) -> None:
        if not self._vm.is_playing:
            return
        try:
            pos_ms = int(self._player.position())
        except Exception:
            return

        with self._snapshot_lock:
            pos_us = pos_ms * 1000
            if pos_us == self._snapshot_data.get("position_us"):
                return
            self._snapshot_data["position_us"] = pos_us
            self._position_us = pos_us

        self._emit_properties_changed(position_only=True)

    def _emit_properties_changed(self, position_only: bool = False) -> None:
        if self._loop is None or self._player_iface is None:
            return

        with self._snapshot_lock:
            if position_only:
                changed = {"Position": int(self._snapshot_data["position_us"])}
            else:
                changed = {
                    "PlaybackStatus": self._snapshot_data["playback_status"],
                    "Rate": float(self._snapshot_data["playback_speed"]),
                    "Metadata": self._snapshot_data["metadata"],
                    "Volume": float(self._snapshot_data["volume"]),
                    "Position": int(self._snapshot_data["position_us"]),
                    "CanPlay": bool(self._snapshot_data["has_track"]),
                    "CanPause": bool(self._snapshot_data["has_track"]),
                    "CanSeek": bool(self._snapshot_data["can_seek"]),
                }

        self._loop.call_soon_threadsafe(self._player_iface.emit_properties_changed, changed, [])

    def _emit_seeked(self) -> None:
        if self._loop is None or self._player_iface is None:
            return
        self._loop.call_soon_threadsafe(self._player_iface.seeked)

    def _dbus_thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._stop_event = asyncio.Event()

        async def runner() -> None:
            bus = await MessageBus().connect()
            self._bus = bus
            self._root_iface = _RootInterface(self)
            self._player_iface = _PlayerInterface(self)

            bus.export(MPRIS_PATH, self._root_iface)
            bus.export(MPRIS_PATH, self._player_iface)
            await bus.request_name(MPRIS_SERVICE)

            self._ready.set()
            await self._stop_event.wait()

            try:
                bus.disconnect()
            except Exception:
                pass

        try:
            loop.run_until_complete(runner())
        except Exception as exc:
            self._start_error = exc
            self._ready.set()
            logger.exception("MPRIS: failed to start dbus service")
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
            except Exception:
                pass
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()