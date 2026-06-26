from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings

from .paths import downloads_dir


@dataclass(frozen=True)
class AppSettings:
    startup_behavior: bool
    notifications_enabled: bool
    refresh_feeds_on_startup: bool
    sync_server_enabled: bool
    sync_server_host: str
    sync_server_port: int
    sync_server_username: str
    sync_server_require_auth: bool
    default_speed: float
    skip_back_seconds: int
    skip_forward_seconds: int
    download_directory: str
    auto_download_policy: str
    max_concurrent_downloads: int
    database_path: str
    last_launch_at: str | None
    previous_launch_at: str | None


class SettingsStore:
    ORG_NAME = "PlainPod"
    APP_NAME = "PlainPod"

    KEY_STARTUP_BEHAVIOR = "general/startup_behavior"
    KEY_NOTIFICATIONS_ENABLED = "general/notifications_enabled"
    KEY_REFRESH_FEEDS_ON_STARTUP = "general/refresh_feeds_on_startup"
    KEY_SYNC_SERVER_ENABLED = "sync/server_enabled"
    KEY_SYNC_SERVER_HOST = "sync/server_host"
    KEY_SYNC_SERVER_PORT = "sync/server_port"
    KEY_SYNC_SERVER_USERNAME = "sync/server_username"
    KEY_SYNC_SERVER_REQUIRE_AUTH = "sync/server_require_auth"
    KEY_DEFAULT_SPEED = "playback/default_speed"
    KEY_SKIP_BACK_SECONDS = "playback/skip_back_seconds"
    KEY_SKIP_FORWARD_SECONDS = "playback/skip_forward_seconds"
    KEY_DOWNLOAD_DIRECTORY = "downloads/directory"
    KEY_AUTO_DOWNLOAD_POLICY = "downloads/default_auto_download_policy"
    KEY_MAX_CONCURRENT_DOWNLOADS = "downloads/max_concurrent_downloads"
    KEY_DATABASE_PATH = "general/database_path"
    KEY_LAST_LAUNCH_AT = "app_state/last_launch_at"
    KEY_PREVIOUS_LAUNCH_AT = "app_state/previous_launch_at"

    AUTO_DOWNLOAD_POLICIES = {"ask", "off", "new_episodes", "latest_1", "latest_3", "latest_5", "latest_10"}

    def __init__(self) -> None:
        self._settings = QSettings(self.ORG_NAME, self.APP_NAME)

    def load(self) -> AppSettings:
        from .paths import db_path
        default_download_dir = str(downloads_dir())
        default_db_path = str(db_path())
        startup_behavior = self._as_bool(self._settings.value(self.KEY_STARTUP_BEHAVIOR, False), default=False)
        notifications_enabled = self._as_bool(
            self._settings.value(self.KEY_NOTIFICATIONS_ENABLED, True),
            default=True,
        )
        refresh_feeds_on_startup = self._as_bool(
            self._settings.value(self.KEY_REFRESH_FEEDS_ON_STARTUP, False),
            default=False,
        )
        sync_server_enabled = self._as_bool(
            self._settings.value(self.KEY_SYNC_SERVER_ENABLED, False),
            default=False,
        )
        sync_server_host = str(self._settings.value(self.KEY_SYNC_SERVER_HOST, "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
        sync_server_port = self._clamp_int(self._settings.value(self.KEY_SYNC_SERVER_PORT, 8989), minimum=1, maximum=65535)
        sync_server_username = str(self._settings.value(self.KEY_SYNC_SERVER_USERNAME, "plainpod") or "plainpod").strip() or "plainpod"
        sync_server_require_auth = self._as_bool(
            self._settings.value(self.KEY_SYNC_SERVER_REQUIRE_AUTH, False),
            default=False,
        )
        default_speed = self._clamp_float(self._settings.value(self.KEY_DEFAULT_SPEED, 1.0), minimum=0.5, maximum=3.0)
        skip_back_seconds = self._clamp_int(self._settings.value(self.KEY_SKIP_BACK_SECONDS, 15), minimum=5, maximum=120)
        skip_forward_seconds = self._clamp_int(
            self._settings.value(self.KEY_SKIP_FORWARD_SECONDS, 30),
            minimum=5,
            maximum=300,
        )
        download_directory = str(self._settings.value(self.KEY_DOWNLOAD_DIRECTORY, default_download_dir) or default_download_dir)
        auto_download_policy = str(self._settings.value(self.KEY_AUTO_DOWNLOAD_POLICY, "ask") or "ask")
        if auto_download_policy not in self.AUTO_DOWNLOAD_POLICIES:
            auto_download_policy = "ask"
        max_concurrent_downloads = self._clamp_int(
            self._settings.value(self.KEY_MAX_CONCURRENT_DOWNLOADS, 3),
            minimum=1,
            maximum=10,
        )
        database_path = str(self._settings.value(self.KEY_DATABASE_PATH, default_db_path) or default_db_path)
        last_launch_at = self._optional_str(self._settings.value(self.KEY_LAST_LAUNCH_AT, None))
        previous_launch_at = self._optional_str(self._settings.value(self.KEY_PREVIOUS_LAUNCH_AT, None))
        return AppSettings(
            startup_behavior=startup_behavior,
            notifications_enabled=notifications_enabled,
            refresh_feeds_on_startup=refresh_feeds_on_startup,
            sync_server_enabled=sync_server_enabled,
            sync_server_host=sync_server_host,
            sync_server_port=sync_server_port,
            sync_server_username=sync_server_username,
            sync_server_require_auth=sync_server_require_auth,
            default_speed=default_speed,
            skip_back_seconds=skip_back_seconds,
            skip_forward_seconds=skip_forward_seconds,
            download_directory=download_directory,
            auto_download_policy=auto_download_policy,
            max_concurrent_downloads=max_concurrent_downloads,
            database_path=database_path,
            last_launch_at=last_launch_at,
            previous_launch_at=previous_launch_at,
        )

    def _set_value(self, key: str, value: object) -> None:
        self._settings.setValue(key, value)
        self._settings.sync()

    def set_startup_behavior(self, enabled: bool) -> None:
        self._set_value(self.KEY_STARTUP_BEHAVIOR, bool(enabled))

    def set_notifications_enabled(self, enabled: bool) -> None:
        self._set_value(self.KEY_NOTIFICATIONS_ENABLED, bool(enabled))

    def set_refresh_feeds_on_startup(self, enabled: bool) -> None:
        self._set_value(self.KEY_REFRESH_FEEDS_ON_STARTUP, bool(enabled))

    def set_sync_server_enabled(self, enabled: bool) -> None:
        self._set_value(self.KEY_SYNC_SERVER_ENABLED, bool(enabled))

    def set_sync_server_host(self, host: str) -> str:
        chosen = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self._set_value(self.KEY_SYNC_SERVER_HOST, chosen)
        return chosen

    def set_sync_server_port(self, port: int) -> int:
        chosen = self._clamp_int(port, minimum=1, maximum=65535)
        self._set_value(self.KEY_SYNC_SERVER_PORT, chosen)
        return chosen

    def set_sync_server_username(self, username: str) -> str:
        chosen = str(username or "plainpod").strip() or "plainpod"
        self._set_value(self.KEY_SYNC_SERVER_USERNAME, chosen)
        return chosen

    def set_sync_server_require_auth(self, enabled: bool) -> None:
        self._set_value(self.KEY_SYNC_SERVER_REQUIRE_AUTH, bool(enabled))

    def set_default_speed(self, speed: float) -> float:
        value = self._clamp_float(speed, minimum=0.5, maximum=3.0)
        self._set_value(self.KEY_DEFAULT_SPEED, value)
        return value

    def set_skip_back_seconds(self, seconds: int) -> int:
        value = self._clamp_int(seconds, minimum=5, maximum=120)
        self._set_value(self.KEY_SKIP_BACK_SECONDS, value)
        return value

    def set_skip_forward_seconds(self, seconds: int) -> int:
        value = self._clamp_int(seconds, minimum=5, maximum=300)
        self._set_value(self.KEY_SKIP_FORWARD_SECONDS, value)
        return value

    def set_download_directory(self, path: str) -> str:
        chosen = str(Path(path).expanduser())
        Path(chosen).mkdir(parents=True, exist_ok=True)
        self._set_value(self.KEY_DOWNLOAD_DIRECTORY, chosen)
        return chosen

    def set_auto_download_policy(self, policy: str) -> str:
        chosen = policy if policy in self.AUTO_DOWNLOAD_POLICIES else "ask"
        self._set_value(self.KEY_AUTO_DOWNLOAD_POLICY, chosen)
        return chosen

    def set_max_concurrent_downloads(self, count: int) -> int:
        chosen = self._clamp_int(count, minimum=1, maximum=10)
        self._set_value(self.KEY_MAX_CONCURRENT_DOWNLOADS, chosen)
        return chosen

    def set_database_path(self, path: str) -> str:
        chosen = str(Path(path).expanduser())
        self._set_value(self.KEY_DATABASE_PATH, chosen)
        return chosen

    def record_launch(self, launched_at: str) -> str | None:
        previous_launch_at = self._optional_str(self._settings.value(self.KEY_LAST_LAUNCH_AT, None))
        self._settings.setValue(self.KEY_PREVIOUS_LAUNCH_AT, previous_launch_at or "")
        self._settings.setValue(self.KEY_LAST_LAUNCH_AT, launched_at)
        self._settings.sync()
        return previous_launch_at

    @staticmethod
    def _optional_str(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
    
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
