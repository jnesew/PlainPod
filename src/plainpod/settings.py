from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings

from .paths import downloads_dir


@dataclass(frozen=True)
class AppSettings:
    startup_behavior: bool
    notifications_enabled: bool
    default_speed: float
    skip_back_seconds: int
    skip_forward_seconds: int
    download_directory: str
    auto_download_policy: str


class SettingsStore:
    ORG_NAME = "PodKDE"
    APP_NAME = "PodKDE"

    KEY_STARTUP_BEHAVIOR = "general/startup_behavior"
    KEY_NOTIFICATIONS_ENABLED = "general/notifications_enabled"
    KEY_DEFAULT_SPEED = "playback/default_speed"
    KEY_SKIP_BACK_SECONDS = "playback/skip_back_seconds"
    KEY_SKIP_FORWARD_SECONDS = "playback/skip_forward_seconds"
    KEY_DOWNLOAD_DIRECTORY = "downloads/directory"
    KEY_AUTO_DOWNLOAD_POLICY = "downloads/auto_download_policy"

    AUTO_DOWNLOAD_POLICIES = {"off", "new_episodes", "all_episodes"}

    def __init__(self) -> None:
        self._settings = QSettings(self.ORG_NAME, self.APP_NAME)

    def load(self) -> AppSettings:
        default_download_dir = str(downloads_dir())
        startup_behavior = self._as_bool(self._settings.value(self.KEY_STARTUP_BEHAVIOR, False), default=False)
        notifications_enabled = self._as_bool(
            self._settings.value(self.KEY_NOTIFICATIONS_ENABLED, True),
            default=True,
        )
        default_speed = self._clamp_float(self._settings.value(self.KEY_DEFAULT_SPEED, 1.0), minimum=0.5, maximum=3.0)
        skip_back_seconds = self._clamp_int(self._settings.value(self.KEY_SKIP_BACK_SECONDS, 15), minimum=5, maximum=120)
        skip_forward_seconds = self._clamp_int(
            self._settings.value(self.KEY_SKIP_FORWARD_SECONDS, 30),
            minimum=5,
            maximum=300,
        )
        download_directory = str(self._settings.value(self.KEY_DOWNLOAD_DIRECTORY, default_download_dir) or default_download_dir)
        auto_download_policy = str(self._settings.value(self.KEY_AUTO_DOWNLOAD_POLICY, "off") or "off")
        if auto_download_policy not in self.AUTO_DOWNLOAD_POLICIES:
            auto_download_policy = "off"

        return AppSettings(
            startup_behavior=startup_behavior,
            notifications_enabled=notifications_enabled,
            default_speed=default_speed,
            skip_back_seconds=skip_back_seconds,
            skip_forward_seconds=skip_forward_seconds,
            download_directory=download_directory,
            auto_download_policy=auto_download_policy,
        )

    def set_startup_behavior(self, enabled: bool) -> None:
        self._settings.setValue(self.KEY_STARTUP_BEHAVIOR, bool(enabled))

    def set_notifications_enabled(self, enabled: bool) -> None:
        self._settings.setValue(self.KEY_NOTIFICATIONS_ENABLED, bool(enabled))

    def set_default_speed(self, speed: float) -> float:
        value = self._clamp_float(speed, minimum=0.5, maximum=3.0)
        self._settings.setValue(self.KEY_DEFAULT_SPEED, value)
        return value

    def set_skip_back_seconds(self, seconds: int) -> int:
        value = self._clamp_int(seconds, minimum=5, maximum=120)
        self._settings.setValue(self.KEY_SKIP_BACK_SECONDS, value)
        return value

    def set_skip_forward_seconds(self, seconds: int) -> int:
        value = self._clamp_int(seconds, minimum=5, maximum=300)
        self._settings.setValue(self.KEY_SKIP_FORWARD_SECONDS, value)
        return value

    def set_download_directory(self, path: str) -> str:
        chosen = str(Path(path).expanduser())
        Path(chosen).mkdir(parents=True, exist_ok=True)
        self._settings.setValue(self.KEY_DOWNLOAD_DIRECTORY, chosen)
        return chosen

    def set_auto_download_policy(self, policy: str) -> str:
        chosen = policy if policy in self.AUTO_DOWNLOAD_POLICIES else "off"
        self._settings.setValue(self.KEY_AUTO_DOWNLOAD_POLICY, chosen)
        return chosen

    @staticmethod
    def _as_bool(value: object, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        if isinstance(value, int):
            return bool(value)
        return default

    @staticmethod
    def _clamp_int(value: object, *, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = minimum
        return max(minimum, min(parsed, maximum))

    @staticmethod
    def _clamp_float(value: object, *, minimum: float, maximum: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = minimum
        return max(minimum, min(parsed, maximum))
