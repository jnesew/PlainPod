from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Signal

from plainpod.repository import Repository
from plainpod.settings import AppSettings
from plainpod.viewmodel import AppViewModel


class _FakeDownloads(QObject):
    download_progress = Signal(int, int, int, int)
    download_status = Signal(int, str)
    download_finished = Signal(int, str)
    download_failed = Signal(int, str)
    download_canceled = Signal(int)

    def __init__(self) -> None:
        super().__init__()

    def set_target_dir(self, _target_dir: Path) -> None:
        return None

    def set_auto_download_policy(self, _policy: str) -> None:
        return None

    def set_notifications_enabled(self, _enabled: bool) -> None:
        return None

    def queue(self, _episode_id: int, _url: str) -> None:
        return None

    def pause(self, _episode_id: int) -> None:
        return None

    def resume(self, _episode_id: int) -> None:
        return None

    def cancel(self, _episode_id: int) -> None:
        return None


class _FakePlayer(QObject):
    position_changed = Signal(int)
    duration_changed = Signal(int)
    playing_changed = Signal(bool)
    playback_finished = Signal()

    def __init__(self) -> None:
        super().__init__()

    def volume(self) -> float:
        return 1.0

    def playback_speed(self) -> float:
        return 1.0

    def set_speed(self, _speed: float) -> None:
        return None

    def set_skip_intervals(self, _back_seconds: int, _forward_seconds: int) -> None:
        return None

    def play_file(self, _path: str, *, start_position_ms: int = 0) -> None:
        return None

    def play_url(self, _url: str, *, start_position_ms: int = 0) -> None:
        return None


class _FakeSettings:
    def set_refresh_feeds_on_startup(self, _enabled: bool) -> None:
        return None

    def set_sync_server_enabled(self, _enabled: bool) -> None:
        return None

    def set_max_concurrent_downloads(self, _count: int) -> int:
        return int(_count)

    def set_auto_download_policy(self, policy: str) -> str:
        return policy

    def load(self) -> AppSettings:
        return AppSettings(
            startup_behavior=False,
            notifications_enabled=False,
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
            database_path="/tmp/plainpod-test.db",
            last_launch_at=None,
            previous_launch_at=None,
        )


def _build_vm(tmp_path: Path) -> tuple[AppViewModel, Repository, int, int]:
    repo = Repository(tmp_path / "viewmodel-playback.db")
    podcast_id = repo.add_podcast(
        title="Playback Pod",
        feed_url="https://example.com/feed.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [
            {
                "guid": "ep-old",
                "title": "Old Episode",
                "media_url": "https://cdn.example.com/old.mp3",
                "duration_seconds": 100,
            },
            {
                "guid": "ep-new",
                "title": "New Episode",
                "media_url": "https://cdn.example.com/new.mp3",
                "duration_seconds": 100,
            },
        ],
    )
    episodes_by_guid = {episode.guid: episode.id for episode in repo.episodes_for_podcast(podcast_id)}
    repo.enqueue(episodes_by_guid["ep-new"])
    vm = AppViewModel(repo, _FakeDownloads(), _FakePlayer(), _FakeSettings())
    return vm, repo, episodes_by_guid["ep-old"], episodes_by_guid["ep-new"]


@pytest.mark.parametrize(
    ("mode", "expected_info"),
    [
        ("stream", "Playing New Episode"),
        ("download", "Playing New Episode"),
    ],
)
def test_play_slots_have_matching_queue_and_progress_side_effects(
    tmp_path: Path,
    mode: str,
    expected_info: str,
) -> None:
    vm, repo, old_episode_id, new_episode_id = _build_vm(tmp_path / mode)
    repo.mark_downloaded(new_episode_id, f"/tmp/{mode}-new.mp3")
    info_messages: list[str] = []
    vm.info.connect(info_messages.append)

    vm._now_playing_episode_id = old_episode_id
    vm._playback_position_ms = 26_000
    vm._playback_duration_ms = 100_000

    if mode == "stream":
        vm.play_episode(new_episode_id)
    else:
        vm.play_download(new_episode_id)

    assert repo.get_episode(old_episode_id).progress_seconds == 26
    assert new_episode_id not in repo.list_queue()
    assert vm.now_playing_episode_id == new_episode_id
    assert info_messages[-1] == expected_info


def test_persist_playback_progress_updates_selected_episode_progress_fields(tmp_path: Path) -> None:
    vm, repo, old_episode_id, _new_episode_id = _build_vm(tmp_path / "partial-progress")
    selected_id = repo.list_podcasts()[0].id
    vm.select_podcast(selected_id)
    vm._now_playing_episode_id = old_episode_id
    vm._playback_position_ms = 12_000
    vm._playback_duration_ms = 100_000

    vm._persist_playback_progress()

    item = next(item for item in vm._episode_model._items if item["episode_id"] == old_episode_id)
    assert repo.get_episode(old_episode_id).progress_seconds == 12
    assert repo.get_episode(old_episode_id).played == 0
    assert item["progress_seconds"] == 12
    assert item["played"] == 0
    assert item["has_progress"] is True
    assert item["progress_display"] == "00:12 / 01:40"
    assert item["progress_percent"] == 12


def test_persist_playback_progress_hides_progress_for_completed_episode(tmp_path: Path) -> None:
    vm, repo, old_episode_id, _new_episode_id = _build_vm(tmp_path / "completed-progress")
    selected_id = repo.list_podcasts()[0].id
    vm.select_podcast(selected_id)
    vm._now_playing_episode_id = old_episode_id
    vm._playback_position_ms = 95_000
    vm._playback_duration_ms = 100_000

    vm._persist_playback_progress()

    item = next(item for item in vm._episode_model._items if item["episode_id"] == old_episode_id)
    assert repo.get_episode(old_episode_id).played == 1
    assert item["played"] == 1
    assert item["has_progress"] is False
    assert item["progress_display"] == ""
    assert item["progress_percent"] == 0


def test_play_download_requires_local_file_and_preserves_error_message(tmp_path: Path) -> None:
    vm, repo, old_episode_id, new_episode_id = _build_vm(tmp_path / "missing-local")
    errors: list[str] = []
    vm.error.connect(errors.append)

    vm._now_playing_episode_id = old_episode_id
    vm._playback_position_ms = 11_000
    vm._playback_duration_ms = 100_000
    vm.play_download(new_episode_id)

    assert errors == ["Downloaded file is not available"]
    assert repo.get_episode(old_episode_id).progress_seconds == 11
    assert new_episode_id in repo.list_queue()
    assert vm.now_playing_episode_id == -1


def test_refresh_selected_returns_early_when_selected_podcast_is_missing(tmp_path: Path, monkeypatch) -> None:
    vm, repo, _old_episode_id, _new_episode_id = _build_vm(tmp_path / "missing-podcast")
    errors: list[str] = []
    infos: list[str] = []
    vm.error.connect(errors.append)
    vm.info.connect(infos.append)
    vm.selected_podcast_id = repo.list_podcasts()[0].id
    repo.remove_podcast(vm.selected_podcast_id)

    def _unexpected_fetch(_url: str) -> None:
        raise AssertionError("fetch_feed should not be called for missing podcast")

    monkeypatch.setattr("plainpod.viewmodel.fetch_feed", _unexpected_fetch)

    vm.refresh_selected()

    assert errors == []
    assert infos == []


def test_download_finished_handler_keeps_selection_and_info_side_effects(tmp_path: Path, monkeypatch) -> None:
    vm, repo, _old_episode_id, new_episode_id = _build_vm(tmp_path / "download-finished")
    selected_id = repo.list_podcasts()[0].id
    vm.selected_podcast_id = selected_id
    selected: list[int] = []
    infos: list[str] = []
    vm.info.connect(infos.append)
    monkeypatch.setattr(vm, "select_podcast", lambda podcast_id: selected.append(podcast_id))

    vm._on_download_finished(new_episode_id, "/tmp/new.mp3")

    assert selected == [selected_id]
    assert infos == ["Download complete"]
    assert vm._downloads_by_episode[new_episode_id]["status"] == "completed"


def test_download_failed_handler_keeps_error_signal_and_failed_state(tmp_path: Path) -> None:
    vm, _repo, _old_episode_id, new_episode_id = _build_vm(tmp_path / "download-failed")
    errors: list[str] = []
    vm.error.connect(errors.append)

    vm._on_download_failed(new_episode_id, "network")

    assert errors == [f"Download failed for {new_episode_id}: network"]
    assert vm._downloads_by_episode[new_episode_id]["status"] == "failed"
    assert vm._downloads_by_episode[new_episode_id]["error_reason"] == "network"


def test_download_canceled_handler_keeps_selection_without_info_or_error(tmp_path: Path, monkeypatch) -> None:
    vm, repo, _old_episode_id, new_episode_id = _build_vm(tmp_path / "download-canceled")
    selected_id = repo.list_podcasts()[0].id
    vm.selected_podcast_id = selected_id
    selected: list[int] = []
    infos: list[str] = []
    errors: list[str] = []
    vm.info.connect(infos.append)
    vm.error.connect(errors.append)
    monkeypatch.setattr(vm, "select_podcast", lambda podcast_id: selected.append(podcast_id))

    vm._on_download_canceled(new_episode_id)

    assert selected == [selected_id]
    assert infos == []
    assert errors == []
    assert vm._downloads_by_episode[new_episode_id]["status"] == "canceled"


def test_episode_model_badges_played_new_since_launch_and_older_unplayed(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "episode-badges.db")
    podcast_id = repo.add_podcast(
        title="Badge Pod",
        feed_url="https://example.com/badges.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [
            {
                "guid": "played",
                "title": "Played Episode",
                "published_at": "2024-01-03T00:00:00+00:00",
                "media_url": "https://cdn.example.com/played.mp3",
            },
            {
                "guid": "progress",
                "title": "In Progress Episode",
                "published_at": "2024-01-04T00:00:00+00:00",
                "duration_seconds": 300,
                "media_url": "https://cdn.example.com/progress.mp3",
            },
            {
                "guid": "new",
                "title": "New Episode",
                "published_at": "2024-01-02T00:00:00+00:00",
                "media_url": "https://cdn.example.com/new.mp3",
            },
            {
                "guid": "old",
                "title": "Old Episode",
                "published_at": "2024-01-01T00:00:00+00:00",
                "media_url": "https://cdn.example.com/old.mp3",
            },
        ],
    )
    repo.conn.execute(
        """
        UPDATE episodes
        SET discovered_at = CASE guid
            WHEN 'played' THEN '2024-01-03T00:00:00+00:00'
            WHEN 'progress' THEN '2024-01-03T00:00:00+00:00'
            WHEN 'new' THEN '2024-01-03T00:00:00+00:00'
            WHEN 'old' THEN '2024-01-01T00:00:00+00:00'
        END
        WHERE podcast_id = ?
        """,
        (podcast_id,),
    )
    repo.conn.commit()
    episodes = repo.episodes_for_podcast(podcast_id)
    played_id = next(episode.id for episode in episodes if episode.guid == "played")
    progress_id = next(episode.id for episode in episodes if episode.guid == "progress")
    repo.set_played(played_id, True)
    repo.update_episode_progress(progress_id, 120, played=False)
    vm = AppViewModel(repo, _FakeDownloads(), _FakePlayer(), _FakeSettings())
    vm._new_since_at = "2024-01-02T00:00:00+00:00"

    vm.select_podcast(podcast_id)

    items_by_title = {item["title"]: item for item in vm._episode_model._items}
    assert set(vm._episode_model.roleNames().values()) >= {
        b"is_new_since_launch",
        b"is_unplayed",
        b"is_in_progress",
        b"episode_badge_label",
    }
    assert items_by_title["Played Episode"]["played"] == 1
    assert items_by_title["Played Episode"]["is_new_since_launch"] is True
    assert items_by_title["Played Episode"]["is_unplayed"] is False
    assert items_by_title["Played Episode"]["episode_badge_label"] == "Played"
    assert items_by_title["In Progress Episode"]["played"] == 0
    assert items_by_title["In Progress Episode"]["is_new_since_launch"] is True
    assert items_by_title["In Progress Episode"]["is_unplayed"] is True
    assert items_by_title["In Progress Episode"]["is_in_progress"] is True
    assert items_by_title["In Progress Episode"]["episode_badge_label"] == "In progress"
    assert items_by_title["New Episode"]["played"] == 0
    assert items_by_title["New Episode"]["is_new_since_launch"] is True
    assert items_by_title["New Episode"]["is_unplayed"] is True
    assert items_by_title["New Episode"]["is_in_progress"] is False
    assert items_by_title["New Episode"]["episode_badge_label"] == "New"
    assert items_by_title["Old Episode"]["played"] == 0
    assert items_by_title["Old Episode"]["is_new_since_launch"] is False
    assert items_by_title["Old Episode"]["is_unplayed"] is True
    assert items_by_title["Old Episode"]["is_in_progress"] is False
    assert items_by_title["Old Episode"]["episode_badge_label"] == "Unplayed"
