from __future__ import annotations

import base64
import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from dataclasses import dataclass

from plainpod.repository import Repository
from plainpod.sync_server import SyncServerConfig, create_handler


def _server(repo: Repository, config: SyncServerConfig | None = None, fetch_feed_fn=None):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(repo, config, fetch_feed_fn=fetch_feed_fn))
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _request(httpd, method: str, path: str, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
    payload = None if body is None else json.dumps(body)
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    conn.request(method, path, body=payload, headers=request_headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    parsed = json.loads(data.decode("utf-8")) if data else None
    return response.status, parsed


def _repo(tmp_path: Path) -> Repository:
    return Repository(tmp_path / "sync-server.db")


@dataclass
class _Feed:
    title: str
    site_url: str | None
    description: str | None
    artwork_url: str | None
    episodes: list[dict]


def test_sync_server_allows_unauthenticated_requests_when_password_unset(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    httpd, thread = _server(repo, SyncServerConfig(password=None))
    try:
        status, body = _request(httpd, "GET", "/health")
    finally:
        httpd.shutdown()
        thread.join(timeout=2)

    assert status == 200
    assert body == {"ok": True, "local_only": True}


def test_sync_server_requires_basic_auth_when_password_configured(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    httpd, thread = _server(repo, SyncServerConfig(password="secret"))
    try:
        status, _ = _request(httpd, "GET", "/health")
        wrong_user = base64.b64encode(b"wrong:secret").decode("ascii")
        wrong_pass = base64.b64encode(b"plainpod:wrong").decode("ascii")
        token = base64.b64encode(b"plainpod:secret").decode("ascii")
        wrong_user_status, _ = _request(httpd, "GET", "/health", headers={"Authorization": f"Basic {wrong_user}"})
        wrong_pass_status, _ = _request(httpd, "GET", "/health", headers={"Authorization": f"Basic {wrong_pass}"})
        ok_status, body = _request(httpd, "GET", "/health", headers={"Authorization": f"Basic {token}"})
    finally:
        httpd.shutdown()
        thread.join(timeout=2)

    assert status == 401
    assert wrong_user_status == 401
    assert wrong_pass_status == 401
    assert ok_status == 200
    assert body == {"ok": True, "local_only": True}


def test_sync_server_handles_devices_and_subscription_deltas(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    httpd, thread = _server(repo)
    try:
        status, body = _request(
            httpd,
            "POST",
            "/api/2/devices/plainpod/phone.json",
            {"caption": "AntennaPod", "type": "mobile"},
        )
        get_status, devices = _request(httpd, "GET", "/api/2/devices/plainpod.json")
        sub_status, sub_body = _request(
            httpd,
            "POST",
            "/api/2/subscriptions/plainpod/phone.json",
            {"add": ["https://example.com/feed.xml"], "remove": []},
        )
        # The posting device should not get its own event echoed back.
        own_status, own_delta = _request(httpd, "GET", "/api/2/subscriptions/plainpod/phone.json?since=0")
        other_status, other_delta = _request(httpd, "GET", "/api/2/subscriptions/plainpod/desktop.json?since=0")
    finally:
        httpd.shutdown()
        thread.join(timeout=2)

    assert status == 200
    assert body["update_urls"] == []
    assert get_status == 200
    assert devices == [{"id": "phone", "caption": "AntennaPod", "type": "mobile"}]
    assert sub_status == 200
    assert sub_body["timestamp"] >= 1
    assert own_status == 200
    assert own_delta["add"] == []
    assert other_status == 200
    assert other_delta["add"] == ["https://example.com/feed.xml"]
    assert repo.get_podcast_by_feed_url("https://example.com/feed.xml") is not None


def test_sync_server_fetches_feed_metadata_for_synced_subscription(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    def fetch_feed(url: str) -> _Feed:
        assert url == "https://example.com/feed.xml"
        return _Feed(
            title="Synced Podcast",
            site_url="https://example.com",
            description="Known good metadata",
            artwork_url="https://example.com/art.png",
            episodes=[{"guid": "ep-1", "title": "Episode", "media_url": "https://cdn.example.com/ep.mp3"}],
        )

    httpd, thread = _server(repo, fetch_feed_fn=fetch_feed)
    try:
        status, body = _request(
            httpd,
            "POST",
            "/api/2/subscriptions/plainpod/phone.json",
            {"add": ["https://example.com/feed.xml"], "remove": []},
        )
    finally:
        httpd.shutdown()
        thread.join(timeout=2)

    podcast = repo.get_podcast_by_feed_url("https://example.com/feed.xml")
    episodes = repo.episodes_for_podcast(podcast.id)
    assert status == 200
    assert body["timestamp"] >= 1
    assert podcast.title == "Synced Podcast"
    assert podcast.site_url == "https://example.com"
    assert podcast.description == "Known good metadata"
    assert podcast.artwork_url == "https://example.com/art.png"
    assert episodes[0].media_url == "https://cdn.example.com/ep.mp3"


def test_sync_server_applies_and_lists_episode_actions(tmp_path: Path) -> None:
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
        [{"guid": "ep-1", "title": "Episode", "media_url": "https://cdn.example.com/ep.mp3"}],
    )
    httpd, thread = _server(repo)
    try:
        status, body = _request(
            httpd,
            "POST",
            "/api/2/episodes/plainpod.json",
            [
                {
                    "podcast": "https://example.com/feed.xml",
                    "episode": "https://cdn.example.com/ep.mp3",
                    "device": "phone",
                    "action": "play",
                    "position": 95,
                    "total": 100,
                }
            ],
        )
        get_status, actions = _request(httpd, "GET", "/api/2/episodes/plainpod.json?since=0")
    finally:
        httpd.shutdown()
        thread.join(timeout=2)

    episode = repo.get_episode_by_media_url(podcast_id, "https://cdn.example.com/ep.mp3")
    assert status == 200
    assert body["timestamp"] >= 1
    assert episode.progress_seconds == 95
    assert episode.played == 1
    assert get_status == 200
    assert actions["actions"][0]["episode"] == "https://cdn.example.com/ep.mp3"
    assert actions["actions"][0]["position"] == 95


def test_local_sync_server_starts_when_enabled(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    from plainpod.sync_server import LocalSyncServer

    server = LocalSyncServer(repo, SyncServerConfig(host="127.0.0.1", port=0, enabled=True))
    try:
        server.start()
        assert server._httpd is not None
        status, body = _request(server._httpd, "GET", "/health")
    finally:
        server.stop()

    assert status == 200
    assert body == {"ok": True, "local_only": True}
