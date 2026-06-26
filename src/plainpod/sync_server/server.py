from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from plainpod.repository import Repository
from plainpod.services.playback_state import PlaybackStateService

logger = logging.getLogger(__name__)

_DEVICE_RE = re.compile(r"^/api/2/devices/([^/]+)(?:/([^/]+)\.json|\.json)$")
_SUBS_RE = re.compile(r"^/api/2/subscriptions/([^/]+)/([^/]+)\.json$")
_EPISODES_RE = re.compile(r"^/api/2/episodes/([^/]+)\.json$")
_AUTH_RE = re.compile(r"^/api/2/auth/([^/]+)/(login|logout)\.json$")


@dataclass(frozen=True)
class SyncServerConfig:
    host: str = "127.0.0.1"
    port: int = 8989
    username: str = "plainpod"
    password: str | None = None
    enabled: bool = True

    @property
    def is_local_only_default(self) -> bool:
        return self.enabled and self.host in {"0.0.0.0", "127.0.0.1", "localhost"} and self.port == 8989

    @classmethod
    def from_env(cls) -> "SyncServerConfig":
        return cls(
            host=os.environ.get("PLAINPOD_SYNC_HOST", cls.host),
            port=_env_int("PLAINPOD_SYNC_PORT", cls.port),
            username=os.environ.get("PLAINPOD_SYNC_USERNAME", cls.username),
            password=os.environ.get("PLAINPOD_SYNC_PASSWORD") or None,
            enabled=_env_bool("PLAINPOD_SYNC_ENABLED", cls.enabled),
        )


class LocalSyncServer:
    def __init__(self, repo: Repository, config: SyncServerConfig | None = None, fetch_feed_fn: Callable[[str], object] | None = None):
        self.repo = repo
        self.config = config or SyncServerConfig()
        self.fetch_feed_fn = fetch_feed_fn
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.config.enabled:
            raise RuntimeError("Local sync server is disabled")
        handler = create_handler(self.repo, self.config, fetch_feed_fn=self.fetch_feed_fn)
        self._httpd = ThreadingHTTPServer((self.config.host, self.config.port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._httpd = None
        self._thread = None


def create_handler(
    repo: Repository,
    config: SyncServerConfig | None = None,
    *,
    fetch_feed_fn: Callable[[str], object] | None = None,
) -> type[BaseHTTPRequestHandler]:
    cfg = config or SyncServerConfig()

    class SyncHandler(BaseHTTPRequestHandler):
        server_version = "PlainPodLocalSync/0.1"

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._write_json({"ok": True, "local_only": True})
                return
            if match := _DEVICE_RE.match(parsed.path):
                username, device_id = match.groups()
                if device_id is not None:
                    self._write_json({"error": "device lookup is not implemented"}, status=404)
                    return
                devices = [
                    {"id": device.device_id, "caption": device.caption, "type": device.type}
                    for device in repo.list_sync_devices(username)
                ]
                self._write_json(devices)
                return
            if match := _SUBS_RE.match(parsed.path):
                username, device_id = match.groups()
                since = _since(parsed.query)
                events = repo.list_subscription_events_since(username, since, exclude_device_id=device_id)
                self._write_json(
                    {
                        "add": [event.feed_url for event in events if event.action == "add"],
                        "remove": [event.feed_url for event in events if event.action == "remove"],
                        "timestamp": repo.current_sync_sequence(),
                    }
                )
                return
            if match := _EPISODES_RE.match(parsed.path):
                username = match.group(1)
                since = _since(parsed.query)
                exclude_device = parse_qs(parsed.query).get("device", [None])[0]
                events = repo.list_episode_actions_since(username, since, exclude_device_id=exclude_device)
                self._write_json(
                    {
                        "actions": [_episode_action_to_json(event) for event in events],
                        "timestamp": repo.current_sync_sequence(),
                    }
                )
                return
            if match := _AUTH_RE.match(parsed.path):
                self._write_json({"username": match.group(1)})
                return
            self._write_json({"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            parsed = urlparse(self.path)
            if match := _DEVICE_RE.match(parsed.path):
                username, device_id = match.groups()
                if device_id is None:
                    self._write_json({"error": "device id required"}, status=400)
                    return
                body = self._read_json(default={})
                repo.upsert_sync_device(
                    username,
                    device_id,
                    caption=body.get("caption") or body.get("title"),
                    device_type=body.get("type"),
                )
                self._write_json({"timestamp": repo.current_sync_sequence(), "update_urls": []})
                return
            if match := _SUBS_RE.match(parsed.path):
                username, device_id = match.groups()
                body = self._read_json(default={})
                for feed_url in body.get("add", []):
                    self._subscribe_feed(feed_url)
                    repo.record_subscription_event(username, device_id, feed_url, "add")
                for feed_url in body.get("remove", []):
                    podcast = repo.get_podcast_by_feed_url(feed_url)
                    if podcast is not None:
                        repo.remove_podcast(podcast.id)
                    repo.record_subscription_event(username, device_id, feed_url, "remove")
                self._write_json({"timestamp": repo.current_sync_sequence(), "update_urls": []})
                return
            if match := _EPISODES_RE.match(parsed.path):
                username = match.group(1)
                actions = self._read_json(default=[])
                if isinstance(actions, dict):
                    actions = actions.get("actions", actions.get("episode_actions", []))
                for action in actions:
                    self._apply_episode_action(username, action)
                self._write_json({"timestamp": repo.current_sync_sequence(), "update_urls": []})
                return
            if match := _AUTH_RE.match(parsed.path):
                self._write_json({"username": match.group(1)})
                return
            self._write_json({"error": "not found"}, status=404)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _authorized(self) -> bool:
            if cfg.password is None:
                return True
            header = self.headers.get("Authorization", "")
            prefix = "Basic "
            if not header.startswith(prefix):
                self._auth_required()
                return False
            try:
                decoded = base64.b64decode(header[len(prefix):]).decode("utf-8")
            except Exception:
                self._auth_required()
                return False
            username, _, password = decoded.partition(":")
            if username != cfg.username or password != cfg.password:
                self._auth_required()
                return False
            return True

        def _auth_required(self) -> None:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="PlainPod Local Sync"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _read_json(self, default: Any) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return default
            raw = self.rfile.read(length)
            if not raw:
                return default
            return json.loads(raw.decode("utf-8"))

        def _write_json(self, payload: Any, status: int = 200) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _apply_episode_action(self, username: str, action: dict[str, Any]) -> None:
            action_name = action.get("action") or "play"
            podcast_url = action.get("podcast") or action.get("podcast_url")
            episode_url = action.get("episode") or action.get("episode_url")
            device_id = action.get("device")
            if not podcast_url or not episode_url or action_name not in {"play", "new", "download", "delete"}:
                return
            position = _optional_int(action.get("position"))
            total = _optional_int(action.get("total"))
            if action_name == "play" and position is not None:
                played = PlaybackStateService.is_near_completion(position * 1000, (total or 0) * 1000)
                repo.update_episode_progress_by_media_url(podcast_url, episode_url, position, played)
            elif action_name == "new":
                repo.update_episode_progress_by_media_url(podcast_url, episode_url, 0, False)
            repo.record_episode_action(
                username,
                device_id,
                podcast_url,
                episode_url,
                action_name,
                started=_optional_int(action.get("started")),
                position=position,
                total=total,
            )

        def _subscribe_feed(self, feed_url: str) -> None:
            if fetch_feed_fn is None:
                if repo.get_podcast_by_feed_url(feed_url) is None:
                    repo.add_podcast(title=feed_url, feed_url=feed_url, site_url=None, description=None, artwork_url=None)
                return
            try:
                feed = fetch_feed_fn(feed_url)
            except Exception as exc:
                logger.warning("Failed to fetch synced subscription feed metadata for %s: %s", feed_url, exc)
                if repo.get_podcast_by_feed_url(feed_url) is None:
                    repo.add_podcast(title=feed_url, feed_url=feed_url, site_url=None, description=None, artwork_url=None)
                return
            podcast_id = repo.add_podcast(
                title=feed.title,
                feed_url=feed_url,
                site_url=feed.site_url,
                description=feed.description,
                artwork_url=feed.artwork_url,
            )
            repo.upsert_episodes(podcast_id, feed.episodes)

    return SyncHandler


def _since(query: str) -> int:
    value = parse_qs(query).get("since", ["0"])[0]
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _episode_action_to_json(event: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "podcast": event.podcast_url,
        "episode": event.episode_url,
        "device": event.device_id,
        "action": event.action,
        "timestamp": event.sequence,
    }
    if event.started is not None:
        payload["started"] = event.started
    if event.position is not None:
        payload["position"] = event.position
    if event.total is not None:
        payload["total"] = event.total
    return payload
