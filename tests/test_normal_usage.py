from __future__ import annotations

from pathlib import Path
import logging

import pytest

from plainpod.artwork_cache import cache_podcast_artwork
from plainpod.download_manager import DownloadManager, DownloadRequest, _DownloadTask
from plainpod.feed import fetch_feed
from plainpod.repository import Repository
from plainpod.settings import SettingsStore


class _FakeResponse:
    def __init__(self, payload: bytes, total: int | None = None):
        self._payload = payload
        self._offset = 0
        self.headers = {"Content-Length": str(total if total is not None else len(payload))}

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._payload):
            return b""
        if size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _SignalRecorder:
    def __init__(self):
        self.calls: list[tuple] = []

    def emit(self, *args) -> None:
        self.calls.append(args)


class _FakeManager:
    def __init__(self):
        self.download_progress = _SignalRecorder()
        self.download_status = _SignalRecorder()
        self.download_finished = _SignalRecorder()
        self.download_failed = _SignalRecorder()
        self.persisted: list[tuple[int, str]] = []

    def mark_downloaded(self, episode_id: int, path: str) -> None:
        self.persisted.append((episode_id, path))



def test_settings_store_persists_refresh_startup_and_download_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(SettingsStore, "ORG_NAME", "PlainPodTests")
    monkeypatch.setattr(SettingsStore, "APP_NAME", f"Settings-{tmp_path.name}")
    first = SettingsStore()

    first.set_refresh_feeds_on_startup(True)
    first.set_sync_server_enabled(True)
    first.set_sync_server_host("127.0.0.1")
    first.set_sync_server_port(8989)
    first.set_sync_server_username("plainpod")
    first.set_sync_server_require_auth(True)
    first.set_auto_download_policy("latest_3")
    first.set_max_concurrent_downloads(4)

    loaded = SettingsStore().load()

    assert loaded.refresh_feeds_on_startup is True
    assert loaded.sync_server_enabled is True
    assert loaded.sync_server_host == "127.0.0.1"
    assert loaded.sync_server_port == 8989
    assert loaded.sync_server_username == "plainpod"
    assert loaded.sync_server_require_auth is True
    assert loaded.auto_download_policy == "latest_3"
    assert loaded.max_concurrent_downloads == 4

def test_fetch_feed_and_repository_insert_with_mocked_feed(monkeypatch, tmp_path: Path) -> None:
    xml = b"""<?xml version='1.0'?>
<rss version='2.0' xmlns:itunes='http://www.itunes.com/dtds/podcast-1.0.dtd'>
  <channel>
    <title>Demo Podcast</title>
    <link>https://example.com</link>
    <description>Demo Description</description>
    <itunes:image href='https://example.com/itunes-cover.png'/>
    <image>
      <url>https://example.com/rss-cover.png</url>
    </image>
    <item>
      <guid>ep-1</guid>
      <title>Episode One</title>
      <pubDate>Tue, 01 Apr 2025 00:00:00 GMT</pubDate>
      <description>Normal episode</description>
      <enclosure url='https://cdn.example.com/ep1.mp3' type='audio/mpeg'/>
    </item>
  </channel>
</rss>
"""

    monkeypatch.setattr("plainpod.feed.urlopen", lambda *_args, **_kwargs: _FakeResponse(xml))

    feed = fetch_feed("https://example.com/feed.xml")
    assert feed.title == "Demo Podcast"
    assert feed.artwork_url == "https://example.com/itunes-cover.png"
    assert len(feed.episodes) == 1

    repo = Repository(tmp_path / "podcasts.db")
    pid = repo.add_podcast(
        title=feed.title,
        feed_url="https://example.com/feed.xml",
        site_url=feed.site_url,
        description=feed.description,
        artwork_url=feed.artwork_url,
    )
    inserted = repo.upsert_episodes(pid, feed.episodes)
    episodes = repo.episodes_for_podcast(pid)

    assert inserted == 1
    assert len(episodes) == 1
    assert episodes[0].guid == "ep-1"
    assert episodes[0].media_url == "https://cdn.example.com/ep1.mp3"

    repo.close()


def test_fetch_feed_falls_back_to_rss_image_url_for_artwork(monkeypatch) -> None:
    xml = b"""<?xml version='1.0'?>
<rss version='2.0'>
  <channel>
    <title>Demo Podcast</title>
    <image>
      <title>Demo Podcast</title>
      <url>https://example.com/rss-cover.png</url>
      <link>https://example.com</link>
    </image>
    <item>
      <guid>ep-1</guid>
      <title>Episode One</title>
      <enclosure url='https://cdn.example.com/ep1.mp3' type='audio/mpeg'/>
    </item>
  </channel>
</rss>
"""

    monkeypatch.setattr("plainpod.feed.urlopen", lambda *_args, **_kwargs: _FakeResponse(xml))
    feed = fetch_feed("https://example.com/feed.xml")
    assert feed.artwork_url == "https://example.com/rss-cover.png"


def test_cache_podcast_artwork_stores_local_file_and_reuses_it(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    payload = b"fake-image-bytes"
    calls = {"count": 0}

    def _fake_urlopen(*_args, **_kwargs):
        calls["count"] += 1
        return _FakeResponse(payload)

    monkeypatch.setattr("plainpod.artwork_cache.urlopen", _fake_urlopen)

    first = cache_podcast_artwork("https://example.com/cover.png")
    second = cache_podcast_artwork("https://example.com/cover.png")

    assert first.startswith("file://")
    assert first == second
    assert calls["count"] == 1



class _ImmediatePool:
    def __init__(self):
        self.started = []

    def start(self, task) -> None:
        self.started.append(task)


def test_download_manager_limits_concurrent_downloads(tmp_path: Path) -> None:
    manager = DownloadManager(tmp_path)
    manager.pool = _ImmediatePool()
    manager.set_max_concurrent_downloads(2)

    manager.queue(1, "https://cdn.example.com/1.mp3")
    manager.queue(2, "https://cdn.example.com/2.mp3")
    manager.queue(3, "https://cdn.example.com/3.mp3")

    assert len(manager.pool.started) == 2
    assert len(manager._pending) == 1

    manager._task_finished(1)

    assert len(manager.pool.started) == 3
    assert len(manager._pending) == 0


def test_download_manager_can_cancel_pending_download(tmp_path: Path) -> None:
    manager = DownloadManager(tmp_path)
    manager.pool = _ImmediatePool()
    manager.set_max_concurrent_downloads(1)

    manager.queue(1, "https://cdn.example.com/1.mp3")
    manager.queue(2, "https://cdn.example.com/2.mp3")
    manager.cancel(2)

    assert len(manager.pool.started) == 1
    assert len(manager._pending) == 0
    assert 2 not in manager._controls

def test_mocked_download_writes_file_and_emits_signals(monkeypatch, tmp_path: Path) -> None:
    media_bytes = b"a" * 8192
    manager = _FakeManager()

    monkeypatch.setattr("plainpod.download_manager.urlopen", lambda *_args, **_kwargs: _FakeResponse(media_bytes))

    req = DownloadRequest(episode_id=42, url="https://media.example.com/episode-42.mp3")
    task = _DownloadTask(manager=manager, req=req, target_dir=tmp_path)
    task.run()

    out_file = tmp_path / "42-episode-42.mp3"
    assert out_file.exists()
    assert out_file.read_bytes() == media_bytes
    assert manager.download_failed.calls == []
    assert manager.download_finished.calls == [(42, str(out_file))]
    assert manager.persisted == []
    assert manager.download_progress.calls


def test_download_progress_is_throttled_and_status_changes_only(monkeypatch, tmp_path: Path) -> None:
    media_bytes = b"a" * (1024 * 64 * 4)
    manager = _FakeManager()
    timestamps = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])

    monkeypatch.setattr("plainpod.download_manager.urlopen", lambda *_args, **_kwargs: _FakeResponse(media_bytes))
    monkeypatch.setattr("plainpod.download_manager.time.monotonic", lambda: next(timestamps))

    req = DownloadRequest(episode_id=43, url="https://media.example.com/episode-43.mp3")
    task = _DownloadTask(manager=manager, req=req, target_dir=tmp_path)
    task.run()

    assert manager.download_progress.calls == [
        (43, 1024 * 64 * 3, len(media_bytes), 655360),
        (43, len(media_bytes), len(media_bytes), 327680),
    ]
    assert manager.download_status.calls == [
        (43, "downloading"),
        (43, "completed"),
    ]


def test_download_tasks_with_shared_basename_use_distinct_output_paths(monkeypatch, tmp_path: Path) -> None:
    media_one = b"episode-one"
    media_two = b"episode-two"
    responses = [_FakeResponse(media_one), _FakeResponse(media_two)]
    manager = _FakeManager()

    def _fake_urlopen(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr("plainpod.download_manager.urlopen", _fake_urlopen)

    task_one = _DownloadTask(
        manager=manager,
        req=DownloadRequest(episode_id=10, url="https://cdn.example.com/audio/shared-name.mp3"),
        target_dir=tmp_path,
    )
    task_two = _DownloadTask(
        manager=manager,
        req=DownloadRequest(episode_id=11, url="https://another.example.com/media/shared-name.mp3"),
        target_dir=tmp_path,
    )

    task_one.run()
    task_two.run()

    out_file_one = tmp_path / "10-shared-name.mp3"
    out_file_two = tmp_path / "11-shared-name.mp3"
    assert out_file_one.exists()
    assert out_file_two.exists()
    assert out_file_one.read_bytes() == media_one
    assert out_file_two.read_bytes() == media_two
    assert manager.download_finished.calls == [
        (10, str(out_file_one)),
        (11, str(out_file_two)),
    ]
    assert manager.persisted == [
        (10, str(out_file_one)),
        (11, str(out_file_two)),
    ]


def test_unsupported_rss_enclosure_schemes_are_filtered_out(monkeypatch, caplog) -> None:

    xml = b"""<?xml version='1.0'?>
<rss version='2.0'>
  <channel>
    <title>Spoof Demo</title>
    <item>
      <guid>spoof-1</guid>
      <title>Suspicious Episode</title>
      <enclosure url='javascript:alert(1)' type='audio/mpeg'/>
    </item>
    <item>
      <guid>spoof-2</guid>
      <title>FTP Episode</title>
      <enclosure url='ftp://attacker.example.com/ep.mp3' type='audio/mpeg'/>
    </item>
    <item>
      <guid>ok-1</guid>
      <title>Safe Episode</title>
      <enclosure url='https://cdn.example.com/ok.mp3' type='audio/mpeg'/>
    </item>
  </channel>
</rss>
"""

    monkeypatch.setattr("plainpod.feed.urlopen", lambda *_args, **_kwargs: _FakeResponse(xml))

    caplog.set_level(logging.WARNING, logger="plainpod.feed")
    feed = fetch_feed("https://attacker.example.com/feed.xml")
    assert [ep["guid"] for ep in feed.episodes] == ["ok-1"]
    assert [ep["media_url"] for ep in feed.episodes] == ["https://cdn.example.com/ok.mp3"]
    assert len(caplog.records) == 2
    assert {record.feed_url for record in caplog.records} == {"https://attacker.example.com/feed.xml"}
    assert {record.guid for record in caplog.records} == {"spoof-1", "spoof-2"}


def test_repository_rejects_unsupported_episode_media_url_scheme(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "spoof.db")
    podcast_id = repo.add_podcast(
        title="Spoof Demo",
        feed_url="https://attacker.example.com/feed.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )

    with pytest.raises(ValueError, match="Unsupported media_url scheme"):
        repo.upsert_episodes(
            podcast_id,
            [{"guid": "spoof-1", "title": "Suspicious Episode", "media_url": "javascript:alert(1)"}],
        )

    assert repo.episodes_for_podcast(podcast_id) == []
    repo.close()


def test_queue_api_supports_remove_clear_reorder_and_dequeue(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "queue.db")
    podcast_id = repo.add_podcast(
        title="Queue Podcast",
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
            {"guid": "ep-3", "title": "Episode 3", "media_url": "https://cdn.example.com/3.mp3"},
        ],
    )
    episodes = repo.episodes_for_podcast(podcast_id)
    e1, e2, e3 = [ep.id for ep in sorted(episodes, key=lambda ep: ep.title)]

    repo.enqueue(e1)
    repo.enqueue(e2)
    repo.enqueue(e3)
    assert repo.list_queue() == [e1, e2, e3]

    repo.reorder_queue(e3, 0)
    assert repo.list_queue() == [e3, e1, e2]

    repo.remove_from_queue(e1)
    assert repo.list_queue() == [e3, e2]

    first = repo.dequeue_next()
    assert first == e3
    assert repo.list_queue() == [e2]

    repo.replace_queue_order([e1, e3, e2])
    assert repo.list_queue() == [e1, e3, e2]

    repo.clear_queue()
    assert repo.list_queue() == []

    repo.close()


def test_list_downloaded_episodes_returns_only_rows_with_paths(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "downloads.db")
    podcast_id = repo.add_podcast(
        title="Download Podcast",
        feed_url="https://example.com/downloads.xml",
        site_url=None,
        description=None,
        artwork_url=None,
    )
    repo.upsert_episodes(
        podcast_id,
        [
            {"guid": "ep-1", "title": "Downloaded Episode", "media_url": "https://cdn.example.com/1.mp3"},
            {"guid": "ep-2", "title": "Not Downloaded", "media_url": "https://cdn.example.com/2.mp3"},
        ],
    )
    episodes = {episode.guid: episode for episode in repo.episodes_for_podcast(podcast_id)}
    repo.mark_downloaded(episodes["ep-1"].id, "/tmp/example-1.mp3")
    repo.mark_downloaded(episodes["ep-2"].id, "")

    downloaded = repo.list_downloaded_episodes()
    assert len(downloaded) == 1
    assert downloaded[0].guid == "ep-1"
    assert downloaded[0].local_path == "/tmp/example-1.mp3"

    repo.close()
