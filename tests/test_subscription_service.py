from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from plainpod.repository import Repository
from plainpod.services.subscriptions import SubscriptionService


@dataclass
class _Feed:
    title: str
    site_url: str | None
    description: str | None
    artwork_url: str | None
    episodes: list[dict]


def _build_repo(tmp_path: Path) -> tuple[Repository, int, list[int]]:
    repo = Repository(tmp_path / "subscriptions.db")
    podcast_id = repo.add_podcast(
        title="Demo",
        feed_url="https://example.com/feed.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [
            {"guid": "ep-1", "title": "Episode 1", "published_at": "2024-01-01T00:00:00+00:00", "media_url": "https://cdn.example.com/1.mp3"},
            {"guid": "ep-2", "title": "Episode 2", "published_at": "2024-02-01T00:00:00+00:00", "media_url": "https://cdn.example.com/2.mp3"},
        ],
    )
    ids = [ep.id for ep in repo.episodes_for_podcast(podcast_id)]
    return repo, podcast_id, ids


def test_apply_download_policy_for_new_episodes_only_downloads_new(tmp_path: Path) -> None:
    repo, podcast_id, _ = _build_repo(tmp_path)
    downloaded: list[int] = []
    service = SubscriptionService(repo, lambda _url: None, downloaded.append)

    episodes = {ep.guid: ep.id for ep in repo.episodes_for_podcast(podcast_id)}
    service.apply_download_policy(podcast_id, {"ep-1"}, "new_episodes")

    assert downloaded == [episodes["ep-2"]]


def test_latest_n_policy_downloads_only_newest_new_episode(tmp_path: Path) -> None:
    repo, podcast_id, _ = _build_repo(tmp_path)
    downloaded: list[int] = []
    service = SubscriptionService(repo, lambda _url: None, downloaded.append)

    episodes = {ep.guid: ep.id for ep in repo.episodes_for_podcast(podcast_id)}
    service.apply_download_policy(podcast_id, set(), "latest_1")

    assert downloaded == [episodes["ep-2"]]


def test_all_episodes_policy_is_treated_as_unsupported(tmp_path: Path) -> None:
    repo, podcast_id, _ = _build_repo(tmp_path)
    downloaded: list[int] = []
    service = SubscriptionService(repo, lambda _url: None, downloaded.append)

    assert service.normalize_download_policy("all_episodes") == "ask"

    service.apply_download_policy(podcast_id, set(), "all_episodes")

    assert downloaded == []


def test_add_feed_inserts_podcast_and_applies_policy(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "add-feed.db")
    downloaded: list[int] = []

    def _fetch(_url: str) -> _Feed:
        return _Feed(
            title="Fresh Podcast",
            site_url="https://example.com",
            description="desc",
            artwork_url=None,
            episodes=[{"guid": "n-1", "title": "New", "media_url": "https://cdn.example.com/new.mp3"}],
        )

    service = SubscriptionService(repo, _fetch, downloaded.append)
    result = service.add_feed("https://example.com/new.xml", "latest_1")

    assert result.title == "Fresh Podcast"
    assert downloaded
    assert repo.episodes_for_podcast(result.podcast_id)[0].guid == "n-1"


def test_refresh_selected_returns_none_when_missing_podcast(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "refresh.db")
    fetch_calls: list[str] = []

    def _fetch(url: str) -> None:
        fetch_calls.append(url)
        return None

    service = SubscriptionService(repo, _fetch, lambda _episode_id: None)

    assert service.refresh_selected(999, "off") is None
    assert fetch_calls == []


def test_refresh_selected_with_feed_returns_none_when_missing_podcast(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "refresh-with-feed.db")
    service = SubscriptionService(repo, lambda _url: None, lambda _episode_id: None)

    assert service.refresh_selected_with_feed(999, _Feed("X", None, None, None, []), "off") is None


def test_podcast_episode_summary_counts_discovered_since_launch_boundary(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "drawer-new.db")
    podcast_id = repo.add_podcast(
        title="Drawer New",
        feed_url="https://example.com/drawer.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [
            {
                "guid": "old-unplayed",
                "title": "Old Unplayed",
                "published_at": "2024-01-01T00:00:00+00:00",
                "media_url": "https://cdn.example.com/old.mp3",
            },
            {
                "guid": "at-boundary",
                "title": "At Boundary",
                "published_at": "2024-01-02T00:00:00+00:00",
                "media_url": "https://cdn.example.com/boundary.mp3",
            },
            {
                "guid": "after-boundary",
                "title": "After Boundary",
                "published_at": "2024-01-03T00:00:00+00:00",
                "media_url": "https://cdn.example.com/after.mp3",
            },
        ],
    )
    repo.conn.execute(
        """
        UPDATE episodes
        SET discovered_at = CASE guid
            WHEN 'old-unplayed' THEN '2024-01-01T00:00:00+00:00'
            WHEN 'at-boundary' THEN '2024-02-01T00:00:00+00:00'
            WHEN 'after-boundary' THEN '2024-02-01T00:00:01+00:00'
        END
        WHERE podcast_id = ?
        """,
        (podcast_id,),
    )
    repo.conn.commit()

    summary = repo.podcast_episode_summary(podcast_id, new_since_at="2024-02-01T00:00:00+00:00")

    assert summary["episode_count"] == 3
    assert summary["new_count"] == 1


def _build_latest_window_repo(tmp_path: Path) -> tuple[Repository, int, dict[str, int]]:
    repo = Repository(tmp_path / "latest-window.db")
    podcast_id = repo.add_podcast(
        title="Latest Window",
        feed_url="https://example.com/latest.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [
            {
                "guid": "ep-1",
                "title": "Episode 1",
                "published_at": "2024-04-01T00:00:00+00:00",
                "media_url": "https://cdn.example.com/1.mp3",
            },
            {
                "guid": "ep-2",
                "title": "Episode 2",
                "published_at": "2024-03-01T00:00:00+00:00",
                "media_url": "https://cdn.example.com/2.mp3",
            },
            {
                "guid": "ep-3",
                "title": "Episode 3",
                "published_at": "2024-02-01T00:00:00+00:00",
                "media_url": "https://cdn.example.com/3.mp3",
            },
            {
                "guid": "ep-4",
                "title": "Episode 4",
                "published_at": "2024-01-01T00:00:00+00:00",
                "media_url": "https://cdn.example.com/4.mp3",
            },
        ],
    )
    episodes = {ep.guid: ep.id for ep in repo.episodes_for_podcast(podcast_id)}
    return repo, podcast_id, episodes


def test_latest_n_policy_does_not_fill_downloaded_slots_past_latest_window(
    tmp_path: Path,
) -> None:
    repo, podcast_id, episodes = _build_latest_window_repo(tmp_path)
    repo.mark_downloaded(episodes["ep-1"], "/tmp/ep-1.mp3")
    downloaded: list[int] = []
    service = SubscriptionService(repo, lambda _url: None, downloaded.append)

    service.apply_download_policy(podcast_id, set(), "latest_3")

    assert downloaded == [episodes["ep-2"], episodes["ep-3"]]


def test_latest_n_policy_repeated_application_does_not_progressively_download_older_episodes(
    tmp_path: Path,
) -> None:
    repo, podcast_id, episodes = _build_latest_window_repo(tmp_path)
    repo.mark_downloaded(episodes["ep-1"], "/tmp/ep-1.mp3")
    downloaded: list[int] = []
    service = SubscriptionService(repo, lambda _url: None, downloaded.append)

    service.apply_download_policy(podcast_id, set(), "latest_3")
    first_downloaded = list(downloaded)
    for episode_id in first_downloaded:
        repo.mark_downloaded(episode_id, f"/tmp/{episode_id}.mp3")
    downloaded.clear()

    service.apply_download_policy(podcast_id, {"ep-1", "ep-2", "ep-3", "ep-4"}, "latest_3")

    assert first_downloaded == [episodes["ep-2"], episodes["ep-3"]]
    assert downloaded == []
