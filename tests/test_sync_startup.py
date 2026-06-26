from __future__ import annotations

from plainpod.sync_config import SyncServerConfigurationError, build_sync_server_config
from types import SimpleNamespace


class _Credentials:
    def __init__(self, password: str | None = None) -> None:
        self.password = password
        self.reads: list[str] = []

    def get_password(self, account: str) -> str | None:
        self.reads.append(account)
        return self.password


def _settings(**overrides) -> SimpleNamespace:
    values = dict(
        startup_behavior=False,
        notifications_enabled=True,
        refresh_feeds_on_startup=False,
        sync_server_enabled=False,
        sync_server_host="127.0.0.1",
        sync_server_port=8989,
        sync_server_username="plainpod",
        sync_server_require_auth=False,
        default_speed=1.0,
        skip_back_seconds=15,
        skip_forward_seconds=30,
        download_directory="/tmp",
        auto_download_policy="ask",
        max_concurrent_downloads=3,
        database_path="/tmp/plainpod.db",
        last_launch_at=None,
        previous_launch_at=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_disabled_local_sync_does_not_read_credentials() -> None:
    credentials = _Credentials(password="secret")

    config = build_sync_server_config(_settings(sync_server_enabled=False), credentials)

    assert config.enabled is False
    assert credentials.reads == []


def test_enabled_local_sync_without_auth_starts_without_credentials() -> None:
    credentials = _Credentials(password="secret")

    config = build_sync_server_config(_settings(sync_server_enabled=True, sync_server_require_auth=False), credentials)

    assert config.enabled is True
    assert config.password is None
    assert credentials.reads == []


def test_enabled_local_sync_with_auth_missing_password_fails_safely(monkeypatch) -> None:
    monkeypatch.delenv("PLAINPOD_SYNC_PASSWORD", raising=False)

    try:
        build_sync_server_config(_settings(sync_server_enabled=True, sync_server_require_auth=True), _Credentials())
    except SyncServerConfigurationError as exc:
        assert "no password" in str(exc)
    else:
        raise AssertionError("expected missing credential configuration error")


def test_enabled_local_sync_with_stored_password_uses_basic_auth(monkeypatch) -> None:
    monkeypatch.delenv("PLAINPOD_SYNC_PASSWORD", raising=False)

    config = build_sync_server_config(
        _settings(sync_server_enabled=True, sync_server_require_auth=True, sync_server_username="alice"),
        _Credentials(password="secret"),
    )

    assert config.enabled is True
    assert config.username == "alice"
    assert config.password == "secret"
