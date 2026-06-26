from __future__ import annotations

from pathlib import Path

from plainpod.repository import Repository
from plainpod.services.playback_state import PlaybackStateService


class _FakePlayer:
    def __init__(self) -> None:
        self.last_call: tuple[str, str, int] | None = None

    def play_file(self, path: str, start_position_ms: int = 0) -> None:
        self.last_call = ("file", path, start_position_ms)

    def play_url(self, url: str, start_position_ms: int = 0) -> None:
        self.last_call = ("url", url, start_position_ms)


def _repo_with_episode(tmp_path: Path, local_path: str | None = None) -> tuple[Repository, int]:
    repo = Repository(tmp_path / "playback.db")
    podcast_id = repo.add_podcast(
        title="Playback",
        feed_url="https://example.com/playback.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [{"guid": "ep-1", "title": "Episode", "media_url": "https://cdn.example.com/e1.mp3"}],
    )
    episode = repo.episodes_for_podcast(podcast_id)[0]
    if local_path:
        repo.mark_downloaded(episode.id, local_path)
    return repo, episode.id


def test_play_episode_uses_downloaded_file_when_present(tmp_path: Path) -> None:
    repo, episode_id = _repo_with_episode(tmp_path, "/tmp/episode.mp3")
    fake_player = _FakePlayer()
    service = PlaybackStateService(repo, fake_player)

    result = service.play_episode(episode_id)

    assert result is not None
    assert fake_player.last_call == ("file", "/tmp/episode.mp3", 0)


def test_persist_progress_keeps_partial_episode_unplayed(tmp_path: Path) -> None:
    repo, episode_id = _repo_with_episode(tmp_path)
    service = PlaybackStateService(repo, _FakePlayer())

    persisted = service.persist_playback_progress(episode_id, position_ms=12_000, duration_ms=100_000)
    episode = repo.get_episode(episode_id)

    assert persisted == (12, False)
    assert episode.progress_seconds == 12
    assert episode.played == 0


def test_persist_progress_marks_played_near_completion(tmp_path: Path) -> None:
    repo, episode_id = _repo_with_episode(tmp_path)
    service = PlaybackStateService(repo, _FakePlayer())

    persisted = service.persist_playback_progress(episode_id, position_ms=95_000, duration_ms=100_000)
    episode = repo.get_episode(episode_id)

    assert persisted == (95, True)
    assert episode.progress_seconds == 95
    assert episode.played == 1


def test_on_player_finished_dequeues_next_episode(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "queue-finish.db")
    podcast_id = repo.add_podcast(
        title="Queue",
        feed_url="https://example.com/queue.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [
            {"guid": "ep-1", "title": "Episode 1", "media_url": "https://cdn.example.com/1.mp3"},
            {"guid": "ep-2", "title": "Episode 2", "media_url": "https://cdn.example.com/2.mp3"},
        ],
    )
    episodes = {episode.guid: episode.id for episode in repo.episodes_for_podcast(podcast_id)}
    repo.enqueue(episodes["ep-2"])

    service = PlaybackStateService(repo, _FakePlayer())
    finished = service.on_player_finished(episodes["ep-1"], position_ms=80_000, duration_ms=100_000)

    assert finished.next_episode_id == episodes["ep-2"]
    assert repo.get_episode(episodes["ep-1"]).played == 1
