from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot, ClassInfo
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection, QDBusObjectPath

if TYPE_CHECKING:
    from .player import PlayerController
    from .viewmodel import AppViewModel

logger = logging.getLogger(__name__)

MPRIS_SERVICE = "org.mpris.MediaPlayer2.plainpod"
MPRIS_OBJECT_PATH = "/org/mpris/MediaPlayer2"


def _desktop_entry_basename() -> str:
    return (
        os.environ.get("FLATPAK_ID")
        or os.environ.get("G_APPLICATION_ID")
        or os.environ.get("DESKTOP_FILE_ID")
        or ""
    )


def _track_object_path(episode_id: int) -> str:
    return f"/org/plainpod/track/{episode_id}"


class MprisService(QObject):
    """Owns D-Bus adaptors and syncs state from AppViewModel / PlayerController."""

    def __init__(self, vm: AppViewModel, player: PlayerController):
        super().__init__()
        self._vm = vm
        self._player = player
        self._root = MprisRootAdaptor(self)
        self._player_adaptor = MprisPlayerAdaptor(self)
        self._service_name: str | None = None
        self._registered_path = False

        self._playback_status: str = "Stopped"
        self._metadata: dict = {}
        self._position_us: int = 0
        self._volume: float = float(vm.volume)
        self._rate: float = float(vm.playback_speed)

        vm.now_playing_title_changed.connect(self._sync_from_vm)
        vm.now_playing_podcast_changed.connect(self._sync_from_vm)
        vm.now_playing_episode_id_changed.connect(self._sync_from_vm)
        vm.playback_position_ms_changed.connect(self._on_position_changed)
        vm.playback_duration_ms_changed.connect(self._sync_from_vm)
        vm.is_playing_changed.connect(self._sync_from_vm)
        vm.volume_changed.connect(self._on_volume_changed)
        vm.playback_speed_changed.connect(self._on_rate_changed)

        self._position_timer = QTimer(self)
        self._position_timer.setInterval(500)
        self._position_timer.timeout.connect(self._tick_position)
        self._position_timer.start()

        self._sync_from_vm()

    def _on_volume_changed(self) -> None:
        self._volume = float(self._vm.volume)
        self._player_adaptor.volumeChanged.emit()

    def _on_rate_changed(self) -> None:
        self._rate = float(self._vm.playback_speed)
        self._player_adaptor.rateChanged.emit()

    def _on_position_changed(self) -> None:
        self._position_us = int(self._vm.playback_position_ms) * 1000
        self._player_adaptor.playerPositionChanged.emit()

    def _tick_position(self) -> None:
        if not self._vm.is_playing:
            return
        self._position_us = int(self._player.position()) * 1000
        self._player_adaptor.playerPositionChanged.emit()

    def _build_metadata(self) -> dict:
        vm = self._vm
        eid = vm.now_playing_episode_id
        title = vm.now_playing_title or ""
        if eid < 0 or not title:
            return {}
        album = vm.now_playing_podcast or ""
        length_us = max(0, int(vm.playback_duration_ms)) * 1000
        meta: dict = {
            "mpris:trackid": QDBusObjectPath(_track_object_path(eid)),
            "xesam:title": title,
            "xesam:album": album,
            "mpris:length": length_us,
        }
        episode = vm.repo.get_episode(eid)
        if episode is not None:
            podcast = next((p for p in vm.repo.list_podcasts() if p.id == episode.podcast_id), None)
            if podcast is not None and podcast.artwork_url:
                from .artwork_cache import cache_podcast_artwork

                art = cache_podcast_artwork(podcast.artwork_url)
                if art:
                    meta["mpris:artUrl"] = art
        return meta

    def _compute_playback_status(self) -> str:
        vm = self._vm
        if vm.now_playing_episode_id < 0:
            return "Stopped"
        if not vm.now_playing_title:
            return "Stopped"
        return "Playing" if vm.is_playing else "Paused"

    def _sync_from_vm(self) -> None:
        self._playback_status = self._compute_playback_status()
        self._metadata = self._build_metadata()
        self._position_us = int(self._vm.playback_position_ms) * 1000
        self._volume = float(self._vm.volume)
        self._rate = float(self._vm.playback_speed)
        pa = self._player_adaptor
        pa.playbackStatusChanged.emit()
        pa.metadataChanged.emit()
        pa.playerPositionChanged.emit()
        pa.volumeChanged.emit()
        pa.rateChanged.emit()
        pa.canPlayChanged.emit()
        pa.canPauseChanged.emit()
        pa.canSeekChanged.emit()

    @property
    def playback_status(self) -> str:
        return self._playback_status

    @property
    def metadata(self) -> dict:
        return self._metadata

    @property
    def position_us(self) -> int:
        return self._position_us

    @property
    def volume(self) -> float:
        return self._volume

    @property
    def rate(self) -> float:
        return self._rate

    def register(self) -> bool:
        bus = QDBusConnection.sessionBus()
        if not bus.registerService(MPRIS_SERVICE):
            logger.warning("MPRIS: could not register D-Bus service name %s", MPRIS_SERVICE)
            return False
        self._service_name = MPRIS_SERVICE
        reg = getattr(QDBusConnection, "RegisterOption", QDBusConnection)
        flags = getattr(reg, "ExportAllContents", 0x3F)
        if not bus.registerObject(MPRIS_OBJECT_PATH, self, flags):
            logger.warning("MPRIS: could not register object at %s", MPRIS_OBJECT_PATH)
            bus.unregisterService(MPRIS_SERVICE)
            self._service_name = None
            return False
        self._registered_path = True
        logger.info("MPRIS: registered %s at %s", MPRIS_SERVICE, MPRIS_OBJECT_PATH)
        return True

    def unregister(self) -> None:
        bus = QDBusConnection.sessionBus()
        if self._registered_path:
            bus.unregisterObject(MPRIS_OBJECT_PATH)
            self._registered_path = False
        if self._service_name:
            bus.unregisterService(self._service_name)
            self._service_name = None
            logger.info("MPRIS: unregistered from session bus")

    # --- invoked by MprisPlayerAdaptor slots ---

    def raise_(self) -> None:
        pass

    def quit(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.quit()

    def next_(self) -> None:
        pass

    def previous(self) -> None:
        pass

    def pause(self) -> None:
        if self._vm.is_playing:
            self._vm.toggle_playback()

    def play_pause(self) -> None:
        self._vm.toggle_playback()

    def play(self) -> None:
        if not self._vm.is_playing:
            self._vm.toggle_playback()

    def stop(self) -> None:
        self._player.pause()

    def seek(self, offset_us: int) -> None:
        delta_ms = int(offset_us) // 1000
        self._vm.seek(max(0, self._vm.playback_position_ms + delta_ms))
        self._emit_seeked()

    def set_position(self, track_id: QDBusObjectPath, position_us: int) -> None:
        eid = self._vm.now_playing_episode_id
        if eid < 0:
            return
        if track_id.path() != _track_object_path(eid):
            return
        self._vm.seek(max(0, int(position_us) // 1000))
        self._emit_seeked()

    def open_uri(self, uri: str) -> None:
        logger.debug("MPRIS OpenUri ignored: %s", uri)

    def _emit_seeked(self) -> None:
        self._position_us = int(self._player.position()) * 1000
        self._player_adaptor.seeked.emit(self._position_us)


class MprisRootAdaptor(QDBusAbstractAdaptor):
    ClassInfo({"D-Bus Interface": "org.mpris.MediaPlayer2"})

    def __init__(self, parent: MprisService):
        super().__init__(parent)
        self._s = parent

    @Property(bool)
    def CanQuit(self) -> bool:
        return True

    @Property(bool)
    def CanRaise(self) -> bool:
        return False

    @Property(bool)
    def HasTrackList(self) -> bool:
        return False

    @Property(str)
    def Identity(self) -> str:
        return "PlainPod"

    @Property(str)
    def DesktopEntry(self) -> str:
        return _desktop_entry_basename()

    @Property("QStringList")
    def SupportedUriSchemes(self) -> list[str]:
        return ["http", "https", "file"]

    @Property("QStringList")
    def SupportedMimeTypes(self) -> list[str]:
        return [
            "audio/mpeg",
            "audio/mp4",
            "audio/x-m4a",
            "audio/aac",
            "audio/opus",
            "application/ogg",
            "audio/ogg",
        ]

    @Slot()
    def Raise(self) -> None:
        self._s.raise_()

    @Slot()
    def Quit(self) -> None:
        self._s.quit()


class MprisPlayerAdaptor(QDBusAbstractAdaptor):
    ClassInfo({"D-Bus Interface": "org.mpris.MediaPlayer2.Player"})

    playbackStatusChanged = Signal()
    loopStatusChanged = Signal()
    rateChanged = Signal()
    shuffleChanged = Signal()
    metadataChanged = Signal()
    volumeChanged = Signal()
    playerPositionChanged = Signal()
    canControlChanged = Signal()
    canGoNextChanged = Signal()
    canGoPreviousChanged = Signal()
    canPlayChanged = Signal()
    canPauseChanged = Signal()
    canSeekChanged = Signal()
    minimumRateChanged = Signal()
    maximumRateChanged = Signal()
    seeked = Signal(int)

    def __init__(self, parent: MprisService):
        super().__init__(parent)
        self._s = parent

    @Property(str, notify=playbackStatusChanged)
    def PlaybackStatus(self) -> str:
        return self._s.playback_status

    @Property(str, notify=loopStatusChanged)
    def LoopStatus(self) -> str:
        return "None"

    @LoopStatus.setter
    def LoopStatus(self, value: str) -> None:
        pass

    @Property(float, notify=rateChanged)
    def Rate(self) -> float:
        return self._s.rate

    @Rate.setter
    def Rate(self, value: float) -> None:
        self._s._vm.set_playback_speed(float(value))

    @Property(bool, notify=shuffleChanged)
    def Shuffle(self) -> bool:
        return False

    @Shuffle.setter
    def Shuffle(self, value: bool) -> None:
        pass

    @Property("QVariantMap", notify=metadataChanged)
    def Metadata(self) -> dict:
        return self._s.metadata

    @Property(float, notify=volumeChanged)
    def Volume(self) -> float:
        return self._s.volume

    @Volume.setter
    def Volume(self, value: float) -> None:
        self._s._vm.set_volume(float(value))

    @Property(int, notify=playerPositionChanged)
    def Position(self) -> int:
        return self._s.position_us

    @Property(bool, notify=canControlChanged)
    def CanControl(self) -> bool:
        return True

    @Property(bool, notify=canGoNextChanged)
    def CanGoNext(self) -> bool:
        return False

    @Property(bool, notify=canGoPreviousChanged)
    def CanGoPrevious(self) -> bool:
        return False

    @Property(bool, notify=canPlayChanged)
    def CanPlay(self) -> bool:
        vm = self._s._vm
        return vm.now_playing_episode_id >= 0 and bool(vm.now_playing_title)

    @Property(bool, notify=canPauseChanged)
    def CanPause(self) -> bool:
        vm = self._s._vm
        return vm.now_playing_episode_id >= 0 and bool(vm.now_playing_title)

    @Property(bool, notify=canSeekChanged)
    def CanSeek(self) -> bool:
        return self._s._vm.playback_duration_ms > 0

    @Property(float, notify=minimumRateChanged)
    def MinimumRate(self) -> float:
        return 0.5

    @Property(float, notify=maximumRateChanged)
    def MaximumRate(self) -> float:
        return 3.0

    @Slot()
    def Next(self) -> None:
        self._s.next_()

    @Slot()
    def Previous(self) -> None:
        self._s.previous()

    @Slot()
    def Pause(self) -> None:
        self._s.pause()

    @Slot()
    def PlayPause(self) -> None:
        self._s.play_pause()

    @Slot()
    def Play(self) -> None:
        self._s.play()

    @Slot()
    def Stop(self) -> None:
        self._s.stop()

    @Slot(int)
    def Seek(self, offset: int) -> None:
        self._s.seek(int(offset))

    @Slot(QDBusObjectPath, int)
    def SetPosition(self, track_id: QDBusObjectPath, position: int) -> None:
        self._s.set_position(track_id, int(position))

    @Slot(str)
    def OpenUri(self, uri: str) -> None:
        self._s.open_uri(uri)
