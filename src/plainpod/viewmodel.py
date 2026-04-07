from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QAbstractListModel, QModelIndex, QObject, Qt, QTimer, Slot, Signal
from PySide6.QtWidgets import QFileDialog

from .artwork_cache import cache_podcast_artwork
from .download_manager import DownloadManager
from .feed import fetch_feed
from .player import PlayerController
from .repository import Repository
from .settings import AppSettings, SettingsStore


class DictListModel(QAbstractListModel):
    def __init__(self, role_names: list[str]):
        super().__init__()
        self._items: list[dict[str, Any]] = []
        self._roles = {Qt.UserRole + i + 1: name.encode("utf-8") for i, name in enumerate(role_names)}
        self._role_to_name = {rid: name.decode("utf-8") for rid, name in self._roles.items()}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        if role in self._role_to_name:
            key = self._role_to_name[role]
            return self._items[index.row()].get(key)
        return None

    def roleNames(self) -> dict[int, bytes]:
        return self._roles

    def set_items(self, items: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._items = items
        self.endResetModel()

    def item(self, row: int) -> dict[str, Any] | None:
        if row < 0 or row >= len(self._items):
            return None
        return self._items[row]


class AppViewModel(QObject):
    EPISODE_SORT_NEWEST = 0
    EPISODE_SORT_OLDEST = 1
    EPISODE_SORT_DURATION_DESC = 2

    error = Signal(str)
    info = Signal(str)
    selected_podcast_title_changed = Signal()
    selected_podcast_id_changed = Signal()
    selected_podcast_site_url_changed = Signal()
    selected_podcast_description_changed = Signal()
    selected_podcast_artwork_url_changed = Signal()
    startup_behavior_changed = Signal()
    notifications_enabled_changed = Signal()
    default_speed_changed = Signal()
    skip_back_seconds_changed = Signal()
    skip_forward_seconds_changed = Signal()
    download_directory_changed = Signal()
    auto_download_policy_changed = Signal()
    database_path_changed = Signal()
    podcast_model_changed = Signal()
    episode_model_changed = Signal()
    download_model_changed = Signal()
    queue_model_changed = Signal()
    now_playing_title_changed = Signal()
    now_playing_podcast_changed = Signal()
    now_playing_episode_id_changed = Signal()
    playback_position_ms_changed = Signal()
    playback_duration_ms_changed = Signal()
    is_playing_changed = Signal()
    volume_changed = Signal()
    playback_speed_changed = Signal()

    def __init__(self, repo: Repository, downloads: DownloadManager, player: PlayerController, settings: SettingsStore):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.repo = repo
        self.downloads = downloads
        self.player = player
        self.settings = settings
        self._settings = self.settings.load()
        self.selected_podcast_id: int | None = None
        self._subscription_filter = ""
        self._episode_filter = ""
        self._episode_sort_mode = self.EPISODE_SORT_NEWEST
        self._download_filter = ""
        self._queue_filter = ""
        self._selected_podcast_title = ""
        self._selected_podcast_site_url = ""
        self._selected_podcast_description = ""
        self._selected_podcast_artwork_url = ""
        self._podcast_items_all: list[dict[str, Any]] = []
        self._episode_items_all: list[dict[str, Any]] = []
        self._queue_items_all: list[dict[str, Any]] = []
        self._now_playing_title = ""
        self._now_playing_podcast = ""
        self._playback_position_ms = 0
        self._playback_duration_ms = 0
        self._is_playing = False
        self._volume = self.player.volume()
        self._playback_speed = self.player.playback_speed()
        self._last_progress_persisted_ms = -1

        self._podcast_model = DictListModel(["podcast_id", "title", "feed_url", "download_policy", "artwork_source"])
        self._episode_model = DictListModel([
            "episode_id",
            "title",
            "published_at",
            "published_display",
            "duration_seconds",
            "duration",
            "media_url",
            "local_path",
            "played",
            "progress_seconds",
        ])
        self._download_model = DictListModel([
            "episode_id",
            "title",
            "status",
            "bytes_received",
            "bytes_total",
            "progress_percent",
            "speed_bps",
            "file_path",
            "completed_at",
            "error_reason",
            "section",
            "progress_label",
            "speed_label",
        ])
        self._queue_model = DictListModel(["episode_id", "title", "duration", "podcast", "now_playing", "podcast_artwork_source"])
        self._downloads_by_episode: dict[int, dict[str, Any]] = {}
        self._now_playing_episode_id: int | None = None

        self.downloads.download_progress.connect(self._on_download_progress)
        self.downloads.download_status.connect(self._on_download_status)
        self.downloads.download_finished.connect(self._on_download_finished)
        self.downloads.download_failed.connect(self._on_download_failed)
        self.downloads.download_canceled.connect(self._on_download_canceled)
        self.player.position_changed.connect(self._on_player_position_changed)
        self.player.duration_changed.connect(self._on_player_duration_changed)
        self.player.playing_changed.connect(self._on_player_playing_changed)

        self._progress_save_timer = QTimer(self)
        self._progress_save_timer.setInterval(10_000)
        self._progress_save_timer.timeout.connect(self._persist_playback_progress)
        self._progress_save_timer.start()

        self._apply_runtime_settings()
        self._load_downloads_from_library()
        self.refresh_podcasts()
        self.refresh_queue()

    @Property(QObject, notify=podcast_model_changed)
    def podcast_model(self) -> QObject:
        return self._podcast_model

    @Property(QObject, notify=episode_model_changed)
    def episode_model(self) -> QObject:
        return self._episode_model

    @Property(QObject, notify=download_model_changed)
    def download_model(self) -> QObject:
        return self._download_model

    @Property(QObject, notify=queue_model_changed)
    def queue_model(self) -> QObject:
        return self._queue_model

    def _apply_runtime_settings(self) -> None:
        self.player.set_speed(self._settings.default_speed)
        self._playback_speed = self.player.playback_speed()
        self.player.set_skip_intervals(self._settings.skip_back_seconds, self._settings.skip_forward_seconds)
        self.downloads.set_target_dir(Path(self._settings.download_directory))
        self.downloads.set_auto_download_policy(self._settings.auto_download_policy)
        self.downloads.set_notifications_enabled(self._settings.notifications_enabled)

    def refresh_podcasts(self) -> None:
        self._podcast_items_all = []
        for podcast in self.repo.list_podcasts():
            item = asdict(podcast)
            item["podcast_id"] = item["id"]
            item["artwork_source"] = cache_podcast_artwork(item.get("artwork_url"))
            self._podcast_items_all.append(item)
        self._apply_subscription_filter()

    @Slot(str)
    def add_feed(self, url: str) -> None:
        try:
            feed = fetch_feed(url)
            pid = self.repo.add_podcast(
                title=feed.title,
                feed_url=url,
                site_url=feed.site_url,
                description=feed.description,
                artwork_url=feed.artwork_url,
            )
            existing_guids = {e.guid for e in self.repo.episodes_for_podcast(pid)}
            self.repo.upsert_episodes(pid, feed.episodes)
            if self._settings.auto_download_policy == "all_episodes":
                self._apply_download_policy(pid, existing_guids)
            self.refresh_podcasts()
            self.select_podcast(pid)
            self.info.emit(f"Subscribed to {feed.title}")
        except Exception as exc:
            self.logger.exception("Subscription failed for feed URL: %s", url)
            self.error.emit(f"Could not subscribe: {exc}")

    def _set_selected_podcast_fields(
        self,
        *,
        title: str,
        site_url: str,
        description: str,
        artwork_url: str,
    ) -> None:
        if self._selected_podcast_title != title:
            self._selected_podcast_title = title
            self.selected_podcast_title_changed.emit()
        if self._selected_podcast_site_url != site_url:
            self._selected_podcast_site_url = site_url
            self.selected_podcast_site_url_changed.emit()
        if self._selected_podcast_description != description:
            self._selected_podcast_description = description
            self.selected_podcast_description_changed.emit()
        if self._selected_podcast_artwork_url != artwork_url:
            self._selected_podcast_artwork_url = artwork_url
            self.selected_podcast_artwork_url_changed.emit()

    @Property(str, notify=selected_podcast_title_changed)
    def selected_podcast_title(self) -> str:
        return self._selected_podcast_title

    @Property(int, notify=selected_podcast_id_changed)
    def selected_podcast_id_value(self) -> int:
        return self.selected_podcast_id if self.selected_podcast_id is not None else -1

    @Property(str, notify=selected_podcast_site_url_changed)
    def selected_podcast_site_url(self) -> str:
        return self._selected_podcast_site_url

    @Property(str, notify=selected_podcast_description_changed)
    def selected_podcast_description(self) -> str:
        return self._selected_podcast_description

    @Property(str, notify=selected_podcast_artwork_url_changed)
    def selected_podcast_artwork_url(self) -> str:
        return self._selected_podcast_artwork_url

    @Slot(int)
    def select_podcast(self, podcast_id: int) -> None:
        podcast = next((podcast for podcast in self.repo.list_podcasts() if podcast.id == podcast_id), None)
        if podcast is None:
            if self.selected_podcast_id is not None:
                self.selected_podcast_id = None
                self.selected_podcast_id_changed.emit()
            self._set_selected_podcast_fields(title="", site_url="", description="", artwork_url="")
            self._episode_items_all = []
            self._apply_episode_filter_and_sort()
            return

        if self.selected_podcast_id != podcast.id:
            self.selected_podcast_id = podcast.id
            self.selected_podcast_id_changed.emit()
        self._set_selected_podcast_fields(
            title=podcast.title,
            site_url=podcast.site_url or "",
            description=podcast.description or "",
            artwork_url=cache_podcast_artwork(podcast.artwork_url),
        )
        self._episode_items_all = []
        for episode in self.repo.episodes_for_podcast(podcast.id):
            self._episode_items_all.append(self._episode_item_from_row(episode))
        self._apply_episode_filter_and_sort()

    @Slot(int)
    def remove_podcast(self, podcast_id: int) -> None:
        podcast = next((podcast for podcast in self.repo.list_podcasts() if podcast.id == podcast_id), None)
        if podcast is None:
            self.error.emit("Could not unsubscribe: podcast no longer exists")
            return

        was_selected = self.selected_podcast_id == podcast_id
        self.repo.remove_podcast(podcast_id)
        self.refresh_podcasts()
        self.refresh_queue()
        if was_selected:
            self.select_podcast(-1)
        self.info.emit(f"Unsubscribed from {podcast.title}")

    @Slot()
    def refresh_selected(self) -> None:
        if self.selected_podcast_id is None:
            return
        row = next((p for p in self.repo.list_podcasts() if p.id == self.selected_podcast_id), None)
        if row is None:
            return
        try:
            feed = fetch_feed(row.feed_url)
            existing_guids = {e.guid for e in self.repo.episodes_for_podcast(row.id)}
            self.repo.upsert_episodes(row.id, feed.episodes)
            self._apply_download_policy(row.id, existing_guids)
            self.select_podcast(row.id)
            self.info.emit(f"Refreshed {row.title}")
        except Exception as exc:
            self.logger.exception("Refresh failed for podcast id=%s url=%s", row.id, row.feed_url)
            self.error.emit(f"Refresh failed: {exc}")

    @Slot(int)
    def play_episode(self, episode_id: int) -> None:
        episode = self.repo.get_episode(episode_id)
        if not episode:
            self.logger.error("Play requested for missing episode id=%s", episode_id)
            return
        self._persist_playback_progress()
        self._now_playing_episode_id = None
        start_position_ms = self._resume_position_ms_for_episode(episode)
        if episode.local_path:
            self.player.play_file(episode.local_path, start_position_ms=start_position_ms)
        else:
            self.player.play_url(episode.media_url, start_position_ms=start_position_ms)
        self._set_now_playing_episode(episode_id)
        self._playback_position_ms = start_position_ms
        self.playback_position_ms_changed.emit()
        self.refresh_queue()
        self.info.emit(f"Playing {episode.title}")

    def _apply_download_policy(self, podcast_id: int, existing_guids: set[str]) -> None:
        if self._settings.auto_download_policy == "off":
            return
        episodes = self.repo.episodes_for_podcast(podcast_id)
        if self._settings.auto_download_policy == "new_episodes":
            for ep in episodes:
                if ep.guid not in existing_guids and not ep.local_path:
                    self.download_episode(ep.id)
        elif self._settings.auto_download_policy == "all_episodes":
            for ep in episodes:
                if not ep.local_path:
                    self.download_episode(ep.id)

    @Slot(int)
    def download_episode(self, episode_id: int) -> None:
        episode = self.repo.get_episode(episode_id)
        if not episode:
            self.logger.error("Download requested for missing episode id=%s", episode_id)
            return
        self._ensure_download_item(episode_id, title=episode.title)
        self._set_download_fields(episode_id, status="downloading", error_reason=None)
        self.downloads.queue(episode.id, episode.media_url)
        self.info.emit(f"Download queued: {episode.title}")

    @Slot(int)
    def pause_download(self, episode_id: int) -> None:
        self.downloads.pause(episode_id)

    @Slot(int)
    def resume_download(self, episode_id: int) -> None:
        self.downloads.resume(episode_id)

    @Slot(int)
    def cancel_download(self, episode_id: int) -> None:
        self.downloads.cancel(episode_id)

    @Slot(int)
    def delete_download(self, episode_id: int) -> None:
        item = self._downloads_by_episode.get(episode_id)
        if item and item.get("file_path"):
            try:
                from pathlib import Path
                path = Path(item["file_path"])
                if path.exists():
                    path.unlink()
            except Exception:
                self.logger.exception("Could not delete downloaded file for episode_id=%s", episode_id)
        self.repo.mark_downloaded(episode_id, None)
        self._downloads_by_episode.pop(episode_id, None)
        self._sync_download_model()
        if self.selected_podcast_id is not None:
            self.select_podcast(self.selected_podcast_id)

    @Slot(int)
    def play_download(self, episode_id: int) -> None:
        episode = self.repo.get_episode(episode_id)
        if episode is None or not episode.local_path:
            self.error.emit("Downloaded file is not available")
            return
        self._persist_playback_progress()
        self._now_playing_episode_id = None
        start_position_ms = self._resume_position_ms_for_episode(episode)
        self.player.play_file(episode.local_path, start_position_ms=start_position_ms)
        self._set_now_playing_episode(episode_id)
        self._playback_position_ms = start_position_ms
        self.playback_position_ms_changed.emit()
        self.refresh_queue()
        self.info.emit(f"Playing {episode.title}")

    def _resume_position_ms_for_episode(self, episode: Any) -> int:
        if bool(episode.played):
            return 0
        return max(0, int(episode.progress_seconds or 0) * 1000)

    def _set_now_playing_episode(self, episode_id: int | None) -> None:
        if self._now_playing_episode_id != episode_id:
            self._now_playing_episode_id = episode_id
            self.now_playing_episode_id_changed.emit()
        title = ""
        podcast = ""
        if episode_id is not None:
            episode = self.repo.get_episode(episode_id)
            if episode is not None:
                title = episode.title
                podcast_row = next((row for row in self.repo.list_podcasts() if row.id == episode.podcast_id), None)
                podcast = podcast_row.title if podcast_row else ""
        if self._now_playing_title != title:
            self._now_playing_title = title
            self.now_playing_title_changed.emit()
        if self._now_playing_podcast != podcast:
            self._now_playing_podcast = podcast
            self.now_playing_podcast_changed.emit()

    def _is_near_completion(self, position_ms: int, duration_ms: int) -> bool:
        if duration_ms <= 0:
            return False
        return position_ms >= max(duration_ms - 15_000, int(duration_ms * 0.95))

    def _persist_playback_progress(self) -> None:
        if self._now_playing_episode_id is None:
            return
        duration_ms = self._playback_duration_ms
        position_ms = self._playback_position_ms
        if position_ms <= 0 and duration_ms <= 0:
            return
        position_seconds = max(0, position_ms // 1000)
        played = self._is_near_completion(position_ms, duration_ms)
        self.repo.update_episode_progress(self._now_playing_episode_id, position_seconds, played=played)
        self._last_progress_persisted_ms = position_ms
        if self.selected_podcast_id is not None:
            selected_episode = next(
                (item for item in self._episode_items_all if item.get("id") == self._now_playing_episode_id),
                None,
            )
            if selected_episode is not None:
                selected_episode["progress_seconds"] = position_seconds
                selected_episode["played"] = int(played)
                self._apply_episode_filter_and_sort()

    def _on_player_position_changed(self, position_ms: int) -> None:
        if self._playback_position_ms != position_ms:
            self._playback_position_ms = position_ms
            self.playback_position_ms_changed.emit()
        if self._last_progress_persisted_ms < 0 or abs(position_ms - self._last_progress_persisted_ms) >= 10_000:
            self._persist_playback_progress()

    def _on_player_duration_changed(self, duration_ms: int) -> None:
        if self._playback_duration_ms != duration_ms:
            self._playback_duration_ms = duration_ms
            self.playback_duration_ms_changed.emit()

    def _on_player_playing_changed(self, is_playing: bool) -> None:
        if self._is_playing != is_playing:
            self._is_playing = is_playing
            self.is_playing_changed.emit()
        if not is_playing:
            self._persist_playback_progress()

    def refresh_queue(self) -> None:
        podcasts = list(self.repo.list_podcasts())
        podcast_titles = {podcast.id: podcast.title for podcast in podcasts}
        podcast_artwork_sources = {podcast.id: cache_podcast_artwork(podcast.artwork_url) for podcast in podcasts}
        queue_items: list[dict[str, Any]] = []
        for episode_id in self.repo.list_queue():
            episode = self.repo.get_episode(episode_id)
            if episode is None:
                continue
            queue_items.append(
                {
                    "episode_id": episode.id,
                    "title": episode.title,
                    "duration": self._format_duration(episode.duration_seconds),
                    "podcast": podcast_titles.get(episode.podcast_id, "Unknown podcast"),
                    "now_playing": episode.id == self._now_playing_episode_id,
                    "podcast_artwork_source": podcast_artwork_sources.get(episode.podcast_id, ""),
                }
            )
        self._queue_items_all = queue_items
        self._apply_queue_filter()

    def _load_downloads_from_library(self) -> None:
        for episode in self.repo.list_downloaded_episodes():
            local_path = episode.local_path or ""
            if not local_path:
                continue
            path = Path(local_path)
            if not path.exists():
                self.logger.warning(
                    "Downloaded file missing on disk; clearing stale path for episode_id=%s path=%s",
                    episode.id,
                    local_path,
                )
                self.repo.mark_downloaded(episode.id, None)
                continue
            completed_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
            self._downloads_by_episode[episode.id] = {
                "episode_id": episode.id,
                "title": episode.title,
                "status": "completed",
                "bytes_received": path.stat().st_size,
                "bytes_total": path.stat().st_size,
                "progress_percent": 100,
                "speed_bps": 0,
                "file_path": str(path),
                "completed_at": completed_at,
                "error_reason": "",
                "section": "Completed",
                "progress_label": self._format_progress(path.stat().st_size, path.stat().st_size),
                "speed_label": f"Downloaded on {completed_at}",
            }
        self._sync_download_model()

    @Slot(int)
    def enqueue_episode(self, episode_id: int) -> None:
        episode = self.repo.get_episode(episode_id)
        if episode is None:
            self.logger.error("Queue requested for missing episode id=%s", episode_id)
            return
        self.repo.enqueue(episode_id)
        self.refresh_queue()
        self.info.emit(f"Queued {episode.title}")

    @Slot(int)
    def remove_queue_item(self, episode_id: int) -> None:
        self.repo.remove_from_queue(episode_id)
        self.refresh_queue()

    @Slot()
    def clear_queue(self) -> None:
        self.repo.clear_queue()
        self.refresh_queue()

    @Slot(int, int)
    def move_queue_item(self, episode_id: int, new_position: int) -> None:
        self.repo.reorder_queue(episode_id, new_position)
        self.refresh_queue()

    def _ensure_download_item(self, episode_id: int, title: str | None = None) -> dict[str, Any]:
        item = self._downloads_by_episode.get(episode_id)
        if item is not None:
            return item
        ep = self.repo.get_episode(episode_id)
        item = {
            "episode_id": episode_id,
            "title": title or (ep.title if ep else f"Episode {episode_id}"),
            "status": "downloading",
            "bytes_received": 0,
            "bytes_total": 0,
            "progress_percent": 0,
            "speed_bps": 0,
            "file_path": ep.local_path if ep else "",
            "completed_at": "",
            "error_reason": "",
            "section": "Downloading",
            "progress_label": "0 B / ?",
            "speed_label": "0 B/s",
        }
        self._downloads_by_episode[episode_id] = item
        self._sync_download_model()
        return item

    def _set_download_fields(self, episode_id: int, **kwargs: Any) -> None:
        item = self._ensure_download_item(episode_id)
        item.update(kwargs)
        status = item.get("status", "downloading")
        item["section"] = "Completed" if status == "completed" else "Downloading"
        item["progress_label"] = self._format_progress(item.get("bytes_received", 0), item.get("bytes_total", 0))
        if status == "completed" and item.get("completed_at"):
            item["speed_label"] = f"Downloaded on {item['completed_at']}"
        elif status == "failed":
            item["speed_label"] = item.get("error_reason") or "Failed"
        elif status == "paused":
            item["speed_label"] = "Paused"
        elif status == "canceled":
            item["speed_label"] = "Canceled"
        else:
            item["speed_label"] = f"{self._format_bytes(item.get('speed_bps', 0))}/s"
        self._sync_download_model()

    def _on_download_progress(self, episode_id: int, bytes_received: int, bytes_total: int, speed_bps: int) -> None:
        progress_percent = int((bytes_received / bytes_total) * 100) if bytes_total > 0 else 0
        self._set_download_fields(
            episode_id,
            status="downloading",
            bytes_received=bytes_received,
            bytes_total=bytes_total,
            progress_percent=progress_percent,
            speed_bps=speed_bps,
            error_reason="",
        )

    def _on_download_status(self, episode_id: int, status: str) -> None:
        self._set_download_fields(episode_id, status=status)

    def _on_download_finished(self, episode_id: int, path: str) -> None:
        self.repo.mark_downloaded(episode_id, path)
        completed_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        item = self._ensure_download_item(episode_id)
        total = item.get("bytes_total") or item.get("bytes_received")
        self._set_download_fields(
            episode_id,
            status="completed",
            file_path=path,
            bytes_total=total,
            bytes_received=total,
            progress_percent=100,
            speed_bps=0,
            completed_at=completed_at,
            error_reason="",
        )
        if self.selected_podcast_id is not None:
            self.select_podcast(self.selected_podcast_id)
        self.info.emit("Download complete")

    def _on_download_failed(self, episode_id: int, reason: str) -> None:
        self.logger.error("Download failed for episode_id=%s: %s", episode_id, reason)
        self._set_download_fields(episode_id, status="failed", error_reason=reason, speed_bps=0)
        self.error.emit(f"Download failed for {episode_id}: {reason}")

    def _on_download_canceled(self, episode_id: int) -> None:
        self._set_download_fields(
            episode_id,
            status="canceled",
            bytes_received=0,
            bytes_total=0,
            progress_percent=0,
            speed_bps=0,
            file_path="",
        )
        self.repo.mark_downloaded(episode_id, None)
        if self.selected_podcast_id is not None:
            self.select_podcast(self.selected_podcast_id)

    def _sync_download_model(self) -> None:
        items = sorted(
            self._downloads_by_episode.values(),
            key=lambda item: (item.get("section") != "Downloading", item.get("title", "").lower()),
        )
        filter_text = self._download_filter.strip().lower()
        if filter_text:
            items = [item for item in items if self._matches_download_filter(item, filter_text)]
        self._download_model.set_items(items)

    @Slot(str)
    def set_subscription_filter(self, text: str) -> None:
        self._subscription_filter = text
        self._apply_subscription_filter()

    @Slot(str)
    def set_episode_filter(self, text: str) -> None:
        self._episode_filter = text
        self._apply_episode_filter_and_sort()

    @Slot(int)
    def set_episode_sort(self, mode: int) -> None:
        if mode not in {self.EPISODE_SORT_NEWEST, self.EPISODE_SORT_OLDEST, self.EPISODE_SORT_DURATION_DESC}:
            mode = self.EPISODE_SORT_NEWEST
        self._episode_sort_mode = mode
        self._apply_episode_filter_and_sort()

    @Slot(str)
    def set_download_filter(self, text: str) -> None:
        self._download_filter = text
        self._sync_download_model()

    @Slot(str)
    def set_queue_filter(self, text: str) -> None:
        self._queue_filter = text
        self._apply_queue_filter()

    def _apply_subscription_filter(self) -> None:
        filter_text = self._subscription_filter.strip().lower()
        if not filter_text:
            self._podcast_model.set_items(self._podcast_items_all)
            return
        filtered = [
            item for item in self._podcast_items_all
            if filter_text in (item.get("title") or "").lower()
            or filter_text in (item.get("feed_url") or "").lower()
        ]
        self._podcast_model.set_items(filtered)

    def _apply_episode_filter_and_sort(self) -> None:
        items = list(self._episode_items_all)
        filter_text = self._episode_filter.strip().lower()
        if filter_text:
            items = [item for item in items if filter_text in (item.get("title") or "").lower()]

        if self._episode_sort_mode == self.EPISODE_SORT_OLDEST:
            items.sort(key=lambda item: (self._episode_timestamp(item), (item.get("title") or "").lower(), item.get("id", 0)))
        elif self._episode_sort_mode == self.EPISODE_SORT_DURATION_DESC:
            items.sort(
                key=lambda item: (
                    -(item.get("duration_seconds") or 0),
                    -self._episode_timestamp(item),
                    (item.get("title") or "").lower(),
                    item.get("id", 0),
                )
            )
        else:
            items.sort(
                key=lambda item: (
                    -self._episode_timestamp(item),
                    (item.get("title") or "").lower(),
                    item.get("id", 0),
                )
            )

        self._episode_model.set_items(items)

    def _apply_queue_filter(self) -> None:
        filter_text = self._queue_filter.strip().lower()
        if not filter_text:
            self._queue_model.set_items(self._queue_items_all)
            return
        filtered = [
            item for item in self._queue_items_all
            if filter_text in (item.get("title") or "").lower()
            or filter_text in (item.get("podcast") or "").lower()
        ]
        self._queue_model.set_items(filtered)

    @staticmethod
    def _episode_timestamp(item: dict[str, Any]) -> float:
        published_at = item.get("published_at")
        if not published_at:
            return 0.0
        if isinstance(published_at, str) and published_at.endswith("Z"):
            published_at = f"{published_at[:-1]}+00:00"
        try:
            return datetime.fromisoformat(published_at).timestamp()
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _matches_download_filter(item: dict[str, Any], filter_text: str) -> bool:
        return (
            filter_text in (item.get("title") or "").lower()
            or filter_text in (item.get("status") or "").lower()
            or filter_text in (item.get("section") or "").lower()
            or filter_text in (item.get("error_reason") or "").lower()
        )

    @staticmethod
    def _format_bytes(value: int) -> str:
        value_f = float(max(value, 0))
        units = ["B", "KB", "MB", "GB"]
        idx = 0
        while value_f >= 1024 and idx < len(units) - 1:
            value_f /= 1024
            idx += 1
        return f"{value_f:.1f} {units[idx]}"

    def _format_progress(self, received: int, total: int) -> str:
        if total > 0:
            return f"{self._format_bytes(received)} / {self._format_bytes(total)}"
        return f"{self._format_bytes(received)} / ?"

    @staticmethod
    def _format_published_display(published_at: str | None) -> str:
        if not published_at:
            return "Unknown date"
        normalized = f"{published_at[:-1]}+00:00" if published_at.endswith("Z") else published_at
        try:
            stamp = datetime.fromisoformat(normalized)
        except (TypeError, ValueError):
            return str(published_at)
        return stamp.strftime("%Y-%m-%d")

    def _episode_item_from_row(self, episode: Any) -> dict[str, Any]:
        item = asdict(episode)
        item["episode_id"] = item["id"]
        item["duration"] = self._format_duration(item.get("duration_seconds"))
        item["published_display"] = self._format_published_display(item.get("published_at"))
        return item

    @staticmethod
    def _format_duration(total_seconds: int | None) -> str:
        if not total_seconds:
            return "--:--"
        total_seconds = max(0, total_seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @Slot()
    def toggle_playback(self) -> None:
        self.player.toggle()

    @Slot(int)
    def seek(self, ms: int) -> None:
        self.player.seek(ms)

    @Slot()
    @Slot(int)
    def skip_back(self, seconds: int | None = None) -> None:
        if seconds is None:
            self.player.skip_back()
            return
        self.seek(max(0, self._playback_position_ms - (max(1, int(seconds)) * 1000)))

    @Slot()
    @Slot(int)
    def skip_forward(self, seconds: int | None = None) -> None:
        if seconds is None:
            self.player.skip_forward()
            return
        target = self._playback_position_ms + (max(1, int(seconds)) * 1000)
        if self._playback_duration_ms > 0:
            target = min(target, self._playback_duration_ms)
        self.seek(target)

    @Slot(float)
    def set_volume(self, value: float) -> None:
        clamped = max(0.0, min(float(value), 1.0))
        self.player.set_volume(clamped)
        if self._volume != clamped:
            self._volume = clamped
            self.volume_changed.emit()

    @Slot(float)
    def set_playback_speed(self, value: float) -> None:
        clamped = max(0.5, min(float(value), 3.0))
        self.player.set_speed(clamped)
        if self._playback_speed != clamped:
            self._playback_speed = clamped
            self.playback_speed_changed.emit()

    @Slot(int, bool)
    def set_played(self, episode_id: int, played: bool) -> None:
        self.repo.set_played(episode_id, played)
        if self.selected_podcast_id is not None:
            self.select_podcast(self.selected_podcast_id)

    @Property(bool, notify=startup_behavior_changed)
    def startup_behavior(self) -> bool:
        return self._settings.startup_behavior

    @Property(str, notify=now_playing_title_changed)
    def now_playing_title(self) -> str:
        return self._now_playing_title

    @Property(str, notify=now_playing_podcast_changed)
    def now_playing_podcast(self) -> str:
        return self._now_playing_podcast

    @Property(int, notify=now_playing_episode_id_changed)
    def now_playing_episode_id(self) -> int:
        return self._now_playing_episode_id if self._now_playing_episode_id is not None else -1

    @Property(int, notify=playback_position_ms_changed)
    def playback_position_ms(self) -> int:
        return self._playback_position_ms

    @Property(int, notify=playback_duration_ms_changed)
    def playback_duration_ms(self) -> int:
        return self._playback_duration_ms

    @Property(bool, notify=is_playing_changed)
    def is_playing(self) -> bool:
        return self._is_playing

    @Property(float, notify=volume_changed)
    def volume(self) -> float:
        return self._volume

    @Property(float, notify=playback_speed_changed)
    def playback_speed(self) -> float:
        return self._playback_speed

    @startup_behavior.setter
    def startup_behavior(self, enabled: bool) -> None:
        value = bool(enabled)
        if self._settings.startup_behavior == value:
            return
        self.settings.set_startup_behavior(value)
        self._settings = replace(self._settings, startup_behavior=value)
        self.startup_behavior_changed.emit()

    @Property(bool, notify=notifications_enabled_changed)
    def notifications_enabled(self) -> bool:
        return self._settings.notifications_enabled

    @notifications_enabled.setter
    def notifications_enabled(self, enabled: bool) -> None:
        value = bool(enabled)
        if self._settings.notifications_enabled == value:
            return
        self.settings.set_notifications_enabled(value)
        self._settings = replace(self._settings, notifications_enabled=value)
        self.downloads.set_notifications_enabled(value)
        self.notifications_enabled_changed.emit()

    @Property(float, notify=default_speed_changed)
    def default_speed(self) -> float:
        return self._settings.default_speed

    @default_speed.setter
    def default_speed(self, speed: float) -> None:
        value = self.settings.set_default_speed(speed)
        if self._settings.default_speed == value:
            return
        self._settings = replace(self._settings, default_speed=value)
        self.player.set_speed(value)
        if self._playback_speed != value:
            self._playback_speed = value
            self.playback_speed_changed.emit()
        self.default_speed_changed.emit()

    @Property(int, notify=skip_back_seconds_changed)
    def skip_back_seconds(self) -> int:
        return self._settings.skip_back_seconds

    @skip_back_seconds.setter
    def skip_back_seconds(self, seconds: int) -> None:
        value = self.settings.set_skip_back_seconds(seconds)
        if self._settings.skip_back_seconds == value:
            return
        self._settings = replace(self._settings, skip_back_seconds=value)
        self.player.set_skip_intervals(self._settings.skip_back_seconds, self._settings.skip_forward_seconds)
        self.skip_back_seconds_changed.emit()

    @Property(int, notify=skip_forward_seconds_changed)
    def skip_forward_seconds(self) -> int:
        return self._settings.skip_forward_seconds

    @skip_forward_seconds.setter
    def skip_forward_seconds(self, seconds: int) -> None:
        value = self.settings.set_skip_forward_seconds(seconds)
        if self._settings.skip_forward_seconds == value:
            return
        self._settings = replace(self._settings, skip_forward_seconds=value)
        self.player.set_skip_intervals(self._settings.skip_back_seconds, self._settings.skip_forward_seconds)
        self.skip_forward_seconds_changed.emit()

    @Property(str, notify=download_directory_changed)
    def download_directory(self) -> str:
        return self._settings.download_directory

    @download_directory.setter
    def download_directory(self, path: str) -> None:
        if not path:
            return
        value = self.settings.set_download_directory(path)
        if self._settings.download_directory == value:
            return
        self._settings = replace(self._settings, download_directory=value)
        self.downloads.set_target_dir(Path(value))
        self.download_directory_changed.emit()

    @Property(str, notify=auto_download_policy_changed)
    def auto_download_policy(self) -> str:
        return self._settings.auto_download_policy

    @auto_download_policy.setter
    def auto_download_policy(self, policy: str) -> None:
        value = self.settings.set_auto_download_policy(policy)
        if self._settings.auto_download_policy == value:
            return
        self._settings = replace(self._settings, auto_download_policy=value)
        self.downloads.set_auto_download_policy(value)
        self.auto_download_policy_changed.emit()

    @Slot()
    def browse_download_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(None, "Choose Download Directory", self.download_directory)
        if path:
            self.download_directory = path

    @Property(str, notify=database_path_changed)
    def database_path(self) -> str:
        return self._settings.database_path

    @Slot()
    def browse_database_path(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(None, "Choose SQLite Database", self.database_path, "SQLite files (*.db *.sqlite3);;All Files (*)")
        if path:
            self._settings = replace(self._settings, database_path=self.settings.set_database_path(path))
            self.database_path_changed.emit()
            self.info.emit("Please restart PlainPod for the database change to take effect.")
