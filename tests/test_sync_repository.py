from pathlib import Path

from plainpod.repository import Repository


def _repo(tmp_path: Path) -> Repository:
    return Repository(tmp_path / "sync.db")


def test_sync_defaults_are_local_only() -> None:
    from plainpod.sync_server import SyncServerConfig

    config = SyncServerConfig()

    assert config.host == "127.0.0.1"
    assert config.enabled is True
    assert config.is_local_only_default is True


def test_sync_config_reads_environment(monkeypatch) -> None:
    from plainpod.sync_server import SyncServerConfig

    monkeypatch.setenv("PLAINPOD_SYNC_HOST", "127.0.0.1")
    monkeypatch.setenv("PLAINPOD_SYNC_PORT", "9999")
    monkeypatch.setenv("PLAINPOD_SYNC_USERNAME", "local")
    monkeypatch.setenv("PLAINPOD_SYNC_PASSWORD", "secret")
    monkeypatch.setenv("PLAINPOD_SYNC_ENABLED", "false")

    config = SyncServerConfig.from_env()

    assert config.host == "127.0.0.1"
    assert config.port == 9999
    assert config.username == "local"
    assert config.password == "secret"
    assert config.enabled is False


def test_sync_repository_records_devices_and_events(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    repo.upsert_sync_device("plainpod", "phone", caption="Phone", device_type="mobile")
    repo.record_subscription_event("plainpod", "plainpod", "https://example.com/feed.xml", "add")
    repo.record_episode_action(
        "plainpod",
        "plainpod",
        "https://example.com/feed.xml",
        "https://cdn.example.com/ep.mp3",
        "play",
        position=42,
        total=100,
    )

    devices = repo.list_sync_devices("plainpod")
    subscriptions = repo.list_subscription_events_since("plainpod", 0)
    actions = repo.list_episode_actions_since("plainpod", 0)

    assert devices[0].device_id == "phone"
    assert subscriptions[0].feed_url == "https://example.com/feed.xml"
    assert subscriptions[0].action == "add"
    assert actions[0].episode_url == "https://cdn.example.com/ep.mp3"
    assert actions[0].position == 42
    assert repo.current_sync_sequence() == 2


def test_sync_repository_finds_podcast_and_episode_by_protocol_keys(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    podcast_id = repo.add_podcast(
        title="Sync Pod",
        feed_url="https://example.com/feed.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [{"guid": "guid-1", "title": "Episode", "media_url": "https://cdn.example.com/ep.mp3"}],
    )

    podcast = repo.get_podcast_by_feed_url("https://example.com/feed.xml")
    episode = repo.get_episode_by_media_url(podcast_id, "https://cdn.example.com/ep.mp3")

    assert podcast is not None
    assert podcast.id == podcast_id
    assert episode is not None
    assert episode.title == "Episode"

    updated = repo.update_episode_progress_by_media_url(
        "https://example.com/feed.xml",
        "https://cdn.example.com/ep.mp3",
        55,
        played=True,
    )

    assert updated is True
    assert repo.get_episode(episode.id).progress_seconds == 55
    assert repo.get_episode(episode.id).played == 1


def test_add_podcast_refreshes_placeholder_metadata(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    podcast_id = repo.add_podcast(
        title="https://example.com/feed.xml",
        feed_url="https://example.com/feed.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    updated_id = repo.add_podcast(
        title="Real Podcast",
        feed_url="https://example.com/feed.xml",
        site_url="https://example.com",
        description="Description",
        artwork_url="https://example.com/art.png",
    )

    podcast = repo.get_podcast(podcast_id)
    assert updated_id == podcast_id
    assert podcast.title == "Real Podcast"
    assert podcast.site_url == "https://example.com"
    assert podcast.description == "Description"
    assert podcast.artwork_url == "https://example.com/art.png"


def test_playback_and_subscription_services_emit_plainpod_sync_events(tmp_path: Path) -> None:
    from dataclasses import dataclass

    from plainpod.services.playback_state import PlaybackStateService
    from plainpod.services.subscriptions import SubscriptionService

    @dataclass
    class Feed:
        title: str = "Feed"
        site_url: str | None = None
        description: str | None = None
        artwork_url: str | None = None
        episodes: list[dict] | None = None

    class Player:
        pass

    repo = _repo(tmp_path)
    feed = Feed(episodes=[{"guid": "ep", "title": "Episode", "media_url": "https://cdn.example.com/ep.mp3"}])
    subs = SubscriptionService(repo, lambda _url: feed, lambda _episode_id: None)
    result = subs.add_feed("https://example.com/feed.xml", "off")
    episode = repo.episodes_for_podcast(result.podcast_id)[0]
    playback = PlaybackStateService(repo, Player())

    playback.persist_playback_progress(episode.id, position_ms=12_000, duration_ms=100_000)

    sub_events = repo.list_subscription_events_since("plainpod", 0)
    episode_events = repo.list_episode_actions_since("plainpod", 0)
    assert sub_events[0].action == "add"
    assert sub_events[0].device_id == "plainpod"
    assert episode_events[0].action == "play"
    assert episode_events[0].position == 12
