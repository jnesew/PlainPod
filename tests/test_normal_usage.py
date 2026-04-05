from __future__ import annotations

from pathlib import Path

from plainpod.artwork_cache import cache_podcast_artwork
from plainpod.download_manager import DownloadRequest, _DownloadTask
from plainpod.feed import fetch_feed
from plainpod.repository import Repository


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
        self.download_finished = _SignalRecorder()
        self.download_failed = _SignalRecorder()


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


def test_mocked_download_writes_file_and_emits_signals(monkeypatch, tmp_path: Path) -> None:
    media_bytes = b"a" * 8192
    manager = _FakeManager()

    monkeypatch.setattr("plainpod.download_manager.urlopen", lambda *_args, **_kwargs: _FakeResponse(media_bytes))

    req = DownloadRequest(episode_id=42, url="https://media.example.com/episode-42.mp3")
    task = _DownloadTask(manager=manager, req=req, target_dir=tmp_path)
    task.run()

    out_file = tmp_path / "episode-42.mp3"
    assert out_file.exists()
    assert out_file.read_bytes() == media_bytes
    assert manager.download_failed.calls == []
    assert manager.download_finished.calls == [(42, str(out_file))]
    assert manager.download_progress.calls


def test_spoofed_rss_enclosure_url_is_inserted_into_episode_listing(monkeypatch, tmp_path: Path) -> None:
    """Demonstrates current behavior: unsafe schemes from RSS are inserted unchanged."""

    xml = b"""<?xml version='1.0'?>
<rss version='2.0'>
  <channel>
    <title>Spoof Demo</title>
    <item>
      <guid>spoof-1</guid>
      <title>Suspicious Episode</title>
      <enclosure url='javascript:alert(1)' type='audio/mpeg'/>
    </item>
  </channel>
</rss>
"""

    monkeypatch.setattr("plainpod.feed.urlopen", lambda *_args, **_kwargs: _FakeResponse(xml))

    feed = fetch_feed("https://attacker.example.com/feed.xml")
    repo = Repository(tmp_path / "spoof.db")
    pid = repo.add_podcast(
        title=feed.title,
        feed_url="https://attacker.example.com/feed.xml",
        site_url=feed.site_url,
        description=feed.description,
        artwork_url=feed.artwork_url,
    )
    repo.upsert_episodes(pid, feed.episodes)

    listed = repo.episodes_for_podcast(pid)
    assert len(listed) == 1
    assert listed[0].media_url == "javascript:alert(1)"

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
