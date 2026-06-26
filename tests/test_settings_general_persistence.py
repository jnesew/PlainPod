from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Signal

from plainpod.repository import Repository
from plainpod.settings import SettingsStore
from plainpod.viewmodel import AppViewModel


class _FakeDownloads(QObject):
    download_progress = Signal(int, int, int, int)
    download_status = Signal(int, str)
    download_finished = Signal(int, str)
    download_failed = Signal(int, str)
    download_canceled = Signal(int)

    def set_target_dir(self, _target_dir: Path) -> None:
        return None

    def set_auto_download_policy(self, _policy: str) -> None:
        return None

    def set_notifications_enabled(self, _enabled: bool) -> None:
        return None

    def set_max_concurrent_downloads(self, _count: int) -> None:
        return None


class _FakePlayer(QObject):
    position_changed = Signal(int)
    duration_changed = Signal(int)
    playing_changed = Signal(bool)
    playback_finished = Signal()

    def volume(self) -> float:
        return 1.0

    def playback_speed(self) -> float:
        return 1.0

    def set_speed(self, _speed: float) -> None:
        return None

    def set_skip_intervals(self, _back_seconds: int, _forward_seconds: int) -> None:
        return None


@pytest.fixture
def isolated_settings_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> type[SettingsStore]:
    monkeypatch.setattr(SettingsStore, "ORG_NAME", "PlainPodTests")
    monkeypatch.setattr(SettingsStore, "APP_NAME", f"GeneralCheckboxes-{tmp_path.name}")
    store = SettingsStore()
    store._settings.clear()
    store._settings.sync()
    return SettingsStore


def test_settings_store_persists_general_checkbox_values(isolated_settings_store: type[SettingsStore]) -> None:
    first = isolated_settings_store()

    first.set_startup_behavior(True)
    first.set_notifications_enabled(False)
    first.set_refresh_feeds_on_startup(True)
    first.set_sync_server_enabled(True)

    loaded = isolated_settings_store().load()

    assert loaded.startup_behavior is True
    assert loaded.notifications_enabled is False
    assert loaded.refresh_feeds_on_startup is True
    assert loaded.sync_server_enabled is True


def test_viewmodel_persists_general_checkbox_values(isolated_settings_store: type[SettingsStore], tmp_path: Path) -> None:
    repo = Repository(tmp_path / "viewmodel-settings.db")
    settings = isolated_settings_store()
    vm = AppViewModel(repo, _FakeDownloads(), _FakePlayer(), settings)

    vm.startup_behavior = True
    vm.notifications_enabled = False
    vm.refresh_feeds_on_startup = True
    vm.sync_server_enabled = True

    reloaded = AppViewModel(repo, _FakeDownloads(), _FakePlayer(), isolated_settings_store())

    assert reloaded.startup_behavior is True
    assert reloaded.notifications_enabled is False
    assert reloaded.refresh_feeds_on_startup is True
    assert reloaded.sync_server_enabled is True
