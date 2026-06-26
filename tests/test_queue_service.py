from __future__ import annotations

from pathlib import Path

from plainpod.repository import Repository
from plainpod.services.queue_service import QueueService


def _repo_with_queue(tmp_path: Path) -> tuple[Repository, dict[str, int]]:
    repo = Repository(tmp_path / "queue-service.db")
    podcast_id = repo.add_podcast(
        title="Queue Pod",
        feed_url="https://example.com/queue.xml",
        site_url=None,
        description=None,
        artwork_url="https://example.com/art.png",
    )
    repo.upsert_episodes(
        podcast_id,
        [
            {"guid": "ep-1", "title": "Episode 1", "media_url": "https://cdn.example.com/1.mp3"},
            {"guid": "ep-2", "title": "Episode 2", "media_url": "https://cdn.example.com/2.mp3"},
        ],
    )
    episodes = {episode.guid: episode.id for episode in repo.episodes_for_podcast(podcast_id)}
    repo.enqueue(episodes["ep-1"])
    repo.enqueue(episodes["ep-2"])
    return repo, episodes


def test_enqueue_and_move_queue_item(tmp_path: Path) -> None:
    repo, episodes = _repo_with_queue(tmp_path)
    service = QueueService(repo, lambda _s: "01:00")

    service.move_queue_item(episodes["ep-2"], 0)

    assert repo.list_queue() == [episodes["ep-2"], episodes["ep-1"]]


def test_refresh_queue_marks_now_playing_and_supports_filter(tmp_path: Path) -> None:
    repo, episodes = _repo_with_queue(tmp_path)
    service = QueueService(repo, lambda _s: "01:00")

    items = service.refresh_queue(episodes["ep-1"], "Episode 1")

    assert len(items) == 1
    assert items[0]["now_playing"] is True


def test_enqueue_returns_none_for_missing_episode(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "queue-empty.db")
    service = QueueService(repo, lambda _s: "--:--")

    assert service.enqueue_episode(1234) is None


def test_apply_filter_returns_all_items_for_empty_text(tmp_path: Path) -> None:
    repo, episodes = _repo_with_queue(tmp_path)
    service = QueueService(repo, lambda _s: "01:00")

    all_items = service.refresh_queue(episodes["ep-1"])
    filtered_items = service.apply_filter(all_items, "   ")

    assert filtered_items == all_items
