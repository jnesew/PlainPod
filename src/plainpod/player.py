from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


class PlayerController(QObject):
    position_changed = Signal(int)
    duration_changed = Signal(int)
    playing_changed = Signal(bool)
    playback_finished = Signal()
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.audio = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio)
        self.skip_back_seconds = 15
        self.skip_forward_seconds = 30
        self._pending_start_position_ms: int | None = None

        self.player.positionChanged.connect(self.position_changed.emit)
        self.player.durationChanged.connect(self.duration_changed.emit)
        self.player.playbackStateChanged.connect(lambda _: self.playing_changed.emit(self.is_playing))
        self.player.errorOccurred.connect(
            lambda err, err_string: self.logger.error("Playback error (%s): %s", err, err_string or "<no message>")
        )
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

    @property
    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def play_url(self, url: str, start_position_ms: int = 0) -> None:
        self.logger.info("Play URL requested: %s", url)
        self._pending_start_position_ms = max(0, int(start_position_ms))
        self.player.setSource(QUrl(url))
        self.player.play()
        if self._pending_start_position_ms == 0:
            self.player.setPosition(0)

    def play_file(self, path: str, start_position_ms: int = 0) -> None:
        self.logger.info("Play file requested: %s", path)
        self._pending_start_position_ms = max(0, int(start_position_ms))
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        if self._pending_start_position_ms == 0:
            self.player.setPosition(0)

    def _apply_pending_start_position(self, status: QMediaPlayer.MediaStatus) -> None:
        if self._pending_start_position_ms is None:
            return

        if status not in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            return

        self.player.setPosition(self._pending_start_position_ms)
        self._pending_start_position_ms = None

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        self._apply_pending_start_position(status)
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.playback_finished.emit()

    def pause(self) -> None:
        self.player.pause()

    def resume(self) -> None:
        self.player.play()

    def toggle(self) -> None:
        if self.is_playing:
            self.pause()
        else:
            self.resume()

    def seek(self, ms: int) -> None:
        self.player.setPosition(ms)

    def position(self) -> int:
        return int(self.player.position())

    def duration(self) -> int:
        return int(self.player.duration())

    def set_speed(self, speed: float) -> None:
        self.player.setPlaybackRate(speed)

    def playback_speed(self) -> float:
        return float(self.player.playbackRate())

    def set_volume(self, value: float) -> None:
        self.audio.setVolume(max(0.0, min(float(value), 1.0)))

    def volume(self) -> float:
        return float(self.audio.volume())

    def set_skip_intervals(self, back_seconds: int, forward_seconds: int) -> None:
        self.skip_back_seconds = max(1, int(back_seconds))
        self.skip_forward_seconds = max(1, int(forward_seconds))

    def skip_back(self) -> None:
        self.seek(max(0, self.player.position() - (self.skip_back_seconds * 1000)))

    def skip_forward(self) -> None:
        target = self.player.position() + (self.skip_forward_seconds * 1000)
        duration = self.player.duration()
        if duration > 0:
            target = min(target, duration)
        self.seek(target)
