from __future__ import annotations

from pathlib import Path

from plainpod.repository import Repository
from plainpod.services.downloads_state import DownloadsStateService


def _repo_with_episode(tmp_path: Path) -> tuple[Repository, int]:
    repo = Repository(tmp_path / "downloads-service.db")
    podcast_id = repo.add_podcast(
        title="Downloads",
        feed_url="https://example.com/d.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [{"guid": "ep-1", "title": "Episode 1", "media_url": "https://cdn.example.com/1.mp3"}],
    )
    return repo, repo.episodes_for_podcast(podcast_id)[0].id


def test_progress_updates_fields_and_percent(tmp_path: Path) -> None:
    repo, episode_id = _repo_with_episode(tmp_path)
    service = DownloadsStateService(repo)

    service.on_download_progress(episode_id, bytes_received=50, bytes_total=100, speed_bps=1024)

    item = service.downloads_by_episode[episode_id]
    assert item["progress_percent"] == 50
    assert item["status"] == "downloading"


def test_finish_marks_repo_and_sets_completed_state(tmp_path: Path) -> None:
    repo, episode_id = _repo_with_episode(tmp_path)
    service = DownloadsStateService(repo)
    service.on_download_progress(episode_id, bytes_received=100, bytes_total=100, speed_bps=0)

    service.on_download_finished(episode_id, "/tmp/file.mp3")

    assert repo.get_episode(episode_id).local_path == "/tmp/file.mp3"
    assert service.downloads_by_episode[episode_id]["status"] == "completed"


def test_model_items_filters_by_status(tmp_path: Path) -> None:
    repo, episode_id = _repo_with_episode(tmp_path)
    service = DownloadsStateService(repo)
    service.on_download_failed(episode_id, "network")

    items = service.model_items("failed")

    assert len(items) == 1
    assert items[0]["episode_id"] == episode_id


def test_model_items_returns_all_items_for_empty_filter(tmp_path: Path) -> None:
    repo, episode_id = _repo_with_episode(tmp_path)
    service = DownloadsStateService(repo)
    service.on_download_failed(episode_id, "network")

    items = service.model_items("   ")

    assert len(items) == 1
    assert items[0]["episode_id"] == episode_id


def test_ensure_download_item_includes_podcast_title(tmp_path: Path) -> None:
    repo, episode_id = _repo_with_episode(tmp_path)
    service = DownloadsStateService(repo)

    item = service.ensure_download_item(episode_id)

    assert item["podcast_title"] == "Downloads"


def test_load_downloads_from_library_includes_podcast_title(tmp_path: Path) -> None:
    repo, episode_id = _repo_with_episode(tmp_path)
    download_path = tmp_path / "episode.mp3"
    download_path.write_bytes(b"audio")
    repo.mark_downloaded(episode_id, str(download_path))
    service = DownloadsStateService(repo)

    service.load_downloads_from_library()

    assert service.downloads_by_episode[episode_id]["podcast_title"] == "Downloads"


def test_model_items_filters_by_podcast_title(tmp_path: Path) -> None:
    repo, episode_id = _repo_with_episode(tmp_path)
    service = DownloadsStateService(repo)
    service.ensure_download_item(episode_id)

    items = service.model_items("downloads")

    assert len(items) == 1
    assert items[0]["episode_id"] == episode_id
