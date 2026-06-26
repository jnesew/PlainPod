from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QObject, Signal

from plainpod.feed import FeedData
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
        self.queued: list[tuple[int, str]] = []

    def set_target_dir(self, _target_dir: Path) -> None:
        return None

    def set_auto_download_policy(self, _policy: str) -> None:
        return None

    def set_notifications_enabled(self, _enabled: bool) -> None:
        return None

    def queue(self, _episode_id: int, _url: str) -> None:
        self.queued.append((_episode_id, _url))

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
        self.queued: list[tuple[int, str]] = []

    def volume(self) -> float:
        return 1.0

    def playback_speed(self) -> float:
        return 1.0

    def set_speed(self, _speed: float) -> None:
        return None

    def set_skip_intervals(self, _back_seconds: int, _forward_seconds: int) -> None:
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


def _wait_for(predicate, timeout: float = 1.5) -> bool:
    app = QCoreApplication.instance() or QCoreApplication([])
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        app.processEvents()
        time.sleep(0.01)
    return predicate()


def _make_viewmodel_with_queue(tmp_path: Path) -> AppViewModel:
    repo = Repository(tmp_path / "viewmodel-async.db")
    podcast_id = repo.add_podcast(
        title="Async Pod",
        feed_url="https://example.com/feed.xml",
        site_url="https://example.com",
        description="A podcast",
        artwork_url="https://example.com/cover.png",
    )
    repo.upsert_episodes(
        podcast_id,
        [{"guid": "ep-1", "title": "Episode 1", "media_url": "https://cdn.example.com/1.mp3"}],
    )
    episode_id = repo.episodes_for_podcast(podcast_id)[0].id
    repo.enqueue(episode_id)
    return AppViewModel(repo, _FakeDownloads(), _FakePlayer(), _FakeSettings())


def test_refresh_podcasts_is_non_blocking_while_artwork_caches(monkeypatch, tmp_path: Path) -> None:
    def _slow_cache(url: str | None) -> str:
        time.sleep(0.2)
        return f"cached:{url}"

    monkeypatch.setattr("plainpod.viewmodel.cache_podcast_artwork", _slow_cache)
    vm = _make_viewmodel_with_queue(tmp_path)

    start = time.perf_counter()
    vm.refresh_podcasts()
    elapsed = time.perf_counter() - start

    first_item = vm._podcast_items_all[0]
    assert elapsed < 0.1
    assert first_item["artwork_source"] == "https://example.com/cover.png"
    assert _wait_for(lambda: vm._podcast_items_all[0]["artwork_source"] == "cached:https://example.com/cover.png")


def test_refresh_queue_is_non_blocking_while_artwork_caches(monkeypatch, tmp_path: Path) -> None:
    def _slow_cache(url: str | None) -> str:
        time.sleep(0.2)
        return f"cached:{url}"

    monkeypatch.setattr("plainpod.viewmodel.cache_podcast_artwork", _slow_cache)
    vm = _make_viewmodel_with_queue(tmp_path)

    start = time.perf_counter()
    vm.refresh_queue()
    elapsed = time.perf_counter() - start

    first_item = vm._queue_items_all[0]
    assert elapsed < 0.1
    assert first_item["podcast_artwork_source"] == "https://example.com/cover.png"
    assert _wait_for(lambda: vm._queue_items_all[0]["podcast_artwork_source"] == "cached:https://example.com/cover.png")


def test_setting_latest_n_policy_downloads_existing_latest_episodes(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "policy.db")
    podcast_id = repo.add_podcast(
        title="Policy Pod",
        feed_url="https://example.com/policy.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [
            {"guid": "ep-1", "title": "One", "published_at": "2024-01-01T00:00:00+00:00", "media_url": "https://cdn.example.com/1.mp3"},
            {"guid": "ep-2", "title": "Two", "published_at": "2024-02-01T00:00:00+00:00", "media_url": "https://cdn.example.com/2.mp3"},
            {"guid": "ep-3", "title": "Three", "published_at": "2024-03-01T00:00:00+00:00", "media_url": "https://cdn.example.com/3.mp3"},
            {"guid": "ep-4", "title": "Four", "published_at": "2024-04-01T00:00:00+00:00", "media_url": "https://cdn.example.com/4.mp3"},
        ],
    )
    downloads = _FakeDownloads()
    vm = AppViewModel(repo, downloads, _FakePlayer(), _FakeSettings())

    vm.select_podcast(podcast_id)
    vm.selected_podcast_download_policy = "latest_3"

    assert repo.get_podcast(podcast_id).download_policy == "latest_3"
    queued_ids = [episode_id for episode_id, _url in downloads.queued]
    expected_ids = [episode.id for episode in repo.episodes_for_podcast(podcast_id)[:3]]
    assert queued_ids == expected_ids


def test_selected_podcast_download_policy_persists_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "policy-persist.db"
    repo = Repository(db_path)
    podcast_id = repo.add_podcast(
        title="Policy Persist Pod",
        feed_url="https://example.com/policy-persist.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    vm = AppViewModel(repo, _FakeDownloads(), _FakePlayer(), _FakeSettings())
    vm.select_podcast(podcast_id)

    vm.selected_podcast_download_policy = "latest_5"
    repo.close()

    restarted_repo = Repository(db_path)
    restarted_vm = AppViewModel(restarted_repo, _FakeDownloads(), _FakePlayer(), _FakeSettings())
    restarted_vm.select_podcast(podcast_id)

    assert restarted_vm.selected_podcast_download_policy == "latest_5"
    assert restarted_repo.get_podcast(podcast_id).download_policy == "latest_5"
    restarted_repo.close()


def test_download_episode_skips_already_downloaded_episode(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "downloaded.db")
    podcast_id = repo.add_podcast(
        title="Downloaded Pod",
        feed_url="https://example.com/downloaded.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [{"guid": "ep-1", "title": "Downloaded", "media_url": "https://cdn.example.com/1.mp3"}],
    )
    episode = repo.episodes_for_podcast(podcast_id)[0]
    downloaded_path = tmp_path / "downloaded.mp3"
    downloaded_path.write_bytes(b"downloaded")
    repo.mark_downloaded(episode.id, str(downloaded_path))
    downloads = _FakeDownloads()
    vm = AppViewModel(repo, downloads, _FakePlayer(), _FakeSettings())
    downloads.queued.clear()
    infos: list[str] = []
    vm.info.connect(infos.append)

    vm.download_episode(episode.id)

    assert downloads.queued == []
    assert infos == ["Already downloaded: Downloaded"]


def test_episode_model_uses_empty_local_path_for_not_downloaded_episode(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "episode-model.db")
    podcast_id = repo.add_podcast(
        title="Model Pod",
        feed_url="https://example.com/model.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [{"guid": "ep-1", "title": "Not Downloaded", "media_url": "https://cdn.example.com/1.mp3"}],
    )
    vm = AppViewModel(repo, _FakeDownloads(), _FakePlayer(), _FakeSettings())

    vm.select_podcast(podcast_id)

    assert vm._episode_items_all[0]["local_path"] == ""


def test_download_episode_allows_blank_local_path(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "blank-path.db")
    podcast_id = repo.add_podcast(
        title="Blank Pod",
        feed_url="https://example.com/blank.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [{"guid": "ep-1", "title": "Blank Path", "media_url": "https://cdn.example.com/1.mp3"}],
    )
    episode = repo.episodes_for_podcast(podcast_id)[0]
    repo.mark_downloaded(episode.id, "   ")
    downloads = _FakeDownloads()
    vm = AppViewModel(repo, downloads, _FakePlayer(), _FakeSettings())
    downloads.queued.clear()

    vm.download_episode(episode.id)

    assert downloads.queued == [(episode.id, "https://cdn.example.com/1.mp3")]


def test_download_episode_batches_initial_download_state_sync(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "batched-download.db")
    podcast_id = repo.add_podcast(
        title="Batched Pod",
        feed_url="https://example.com/batched.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [{"guid": "ep-1", "title": "Batched Episode", "media_url": "https://cdn.example.com/1.mp3"}],
    )
    episode = repo.episodes_for_podcast(podcast_id)[0]
    downloads = _FakeDownloads()
    vm = AppViewModel(repo, downloads, _FakePlayer(), _FakeSettings())
    sync_calls = 0
    original_sync = vm._sync_download_model

    def _counted_sync() -> None:
        nonlocal sync_calls
        sync_calls += 1
        original_sync()

    vm._sync_download_model = _counted_sync

    vm.download_episode(episode.id)

    assert downloads.queued == [(episode.id, "https://cdn.example.com/1.mp3")]
    assert sync_calls == 0
    assert _wait_for(lambda: sync_calls == 1)
    assert vm._download_model.rowCount() == 1
    assert vm._download_model.item(0)["status"] == "downloading"


def test_download_progress_syncs_are_coalesced(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "coalesced-progress.db")
    podcast_id = repo.add_podcast(
        title="Progress Pod",
        feed_url="https://example.com/progress.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [{"guid": "ep-1", "title": "Progress Episode", "media_url": "https://cdn.example.com/1.mp3"}],
    )
    episode = repo.episodes_for_podcast(podcast_id)[0]
    downloads = _FakeDownloads()
    vm = AppViewModel(repo, downloads, _FakePlayer(), _FakeSettings())
    vm.download_episode(episode.id)
    assert _wait_for(lambda: vm._download_model.rowCount() == 1)

    sync_calls = 0
    original_sync = vm._sync_download_model

    def _counted_sync() -> None:
        nonlocal sync_calls
        sync_calls += 1
        original_sync()

    vm._sync_download_model = _counted_sync

    downloads.download_progress.emit(episode.id, 10, 100, 1)
    downloads.download_progress.emit(episode.id, 20, 100, 2)
    downloads.download_progress.emit(episode.id, 30, 100, 3)

    assert sync_calls == 0
    assert _wait_for(lambda: sync_calls == 1)
    assert vm._download_model.item(0)["bytes_received"] == 30


def _model_titles(model) -> list[str]:
    return [model.item(row)["title"] for row in range(model.rowCount())]


def test_refresh_selected_updates_podcast_and_episode_models(monkeypatch, tmp_path: Path) -> None:
    repo = Repository(tmp_path / "refresh-selected.db")
    podcast_id = repo.add_podcast(
        title="Old Title",
        feed_url="https://example.com/refresh-selected.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [{"guid": "old", "title": "Old Episode", "published_at": "2024-01-01T00:00:00+00:00", "media_url": "https://cdn.example.com/old.mp3"}],
    )
    vm = AppViewModel(repo, _FakeDownloads(), _FakePlayer(), _FakeSettings())
    vm.select_podcast(podcast_id)

    def _updated_feed(_url: str) -> FeedData:
        return FeedData(
            title="Updated Title",
            site_url=None,
            description=None,
            artwork_url=None,
            episodes=[
                {"guid": "new", "title": "New Episode", "published_at": "2024-02-01T00:00:00+00:00", "media_url": "https://cdn.example.com/new.mp3"},
                {"guid": "old", "title": "Old Episode Updated", "published_at": "2024-01-01T00:00:00+00:00", "media_url": "https://cdn.example.com/old.mp3"},
            ],
        )

    monkeypatch.setattr("plainpod.viewmodel.fetch_feed", _updated_feed)

    vm.refresh_selected()

    assert _wait_for(lambda: "New Episode" in _model_titles(vm.episode_model))
    assert _model_titles(vm.podcast_model) == ["Old Title"]
    assert vm.podcast_model.item(0)["latest_episode_display"] == "2024-02-01"
    assert _model_titles(vm.episode_model) == ["New Episode", "Old Episode Updated"]


def test_refresh_all_updates_selected_episode_model(monkeypatch, tmp_path: Path) -> None:
    repo = Repository(tmp_path / "refresh-all.db")
    first_id = repo.add_podcast(
        title="First Pod",
        feed_url="https://example.com/first.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    second_id = repo.add_podcast(
        title="Second Pod",
        feed_url="https://example.com/second.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(first_id, [{"guid": "first-old", "title": "First Old", "published_at": "2024-01-01T00:00:00+00:00", "media_url": "https://cdn.example.com/first-old.mp3"}])
    repo.upsert_episodes(second_id, [{"guid": "second-old", "title": "Second Old", "published_at": "2024-01-01T00:00:00+00:00", "media_url": "https://cdn.example.com/second-old.mp3"}])
    vm = AppViewModel(repo, _FakeDownloads(), _FakePlayer(), _FakeSettings())
    vm.select_podcast(second_id)

    def _updated_feed(url: str) -> FeedData:
        suffix = "first" if "first" in url else "second"
        return FeedData(
            title=f"{suffix.title()} Pod",
            site_url=None,
            description=None,
            artwork_url=None,
            episodes=[{"guid": f"{suffix}-new", "title": f"{suffix.title()} New", "published_at": "2024-03-01T00:00:00+00:00", "media_url": f"https://cdn.example.com/{suffix}-new.mp3"}],
        )

    monkeypatch.setattr("plainpod.viewmodel.fetch_feed", _updated_feed)

    vm.refresh_all_podcasts()

    assert _wait_for(lambda: "Second New" in _model_titles(vm.episode_model))
    assert _model_titles(vm.podcast_model) == ["First Pod", "Second Pod"]
    assert [vm.podcast_model.item(row)["latest_episode_display"] for row in range(vm.podcast_model.rowCount())] == ["2024-03-01", "2024-03-01"]
    assert _model_titles(vm.episode_model) == ["Second New", "Second Old"]
