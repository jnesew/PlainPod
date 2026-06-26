from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Property, QAbstractListModel, QCoreApplication, QModelIndex, QObject, Qt, QThreadPool, QTimer, Slot, Signal
from PySide6.QtWidgets import QFileDialog

from .async_worker import WorkerTask
from .artwork_cache import cache_podcast_artwork
from .download_manager import DownloadManager
from .feed import fetch_feed
from .filtering import filter_items_by_text
from .player import PlayerController
from .repository import Repository
from .services.downloads_state import DownloadsStateService
from .services.playback_state import PlaybackStateService
from .services.queue_service import QueueService
from .services.subscriptions import SubscriptionService
from .settings import SettingsStore


class DictListModel(QAbstractListModel):
    def __init__(self, role_names: list[str]):
        super().__init__()
        self._items: list[dict[str, Any]] = []
        self._roles = {Qt.UserRole + i + 1: name.encode("utf-8") for i, name in enumerate(role_names)}
        self._role_to_name = {rid: name.decode("utf-8") for rid, name in self._roles.items()}
        self._name_to_role = {name: rid for rid, name in self._role_to_name.items()}

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

    def update_item_by_key(self, key_name: str, key_value: Any, updates: dict[str, Any]) -> bool:
        for row, item in enumerate(self._items):
            if item.get(key_name) != key_value:
                continue

            changed_roles: list[int] = []
            for field, value in updates.items():
                if item.get(field) == value:
                    continue
                item[field] = value
                role = self._name_to_role.get(field)
                if role is not None:
                    changed_roles.append(role)

            if changed_roles:
                model_index = self.index(row, 0)
                self.dataChanged.emit(model_index, model_index, changed_roles)
            return True

        return False

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
    refresh_feeds_on_startup_changed = Signal()
    sync_server_enabled_changed = Signal()
    sync_server_host_changed = Signal()
    sync_server_port_changed = Signal()
    sync_server_username_changed = Signal()
    sync_server_require_auth_changed = Signal()
    max_concurrent_downloads_changed = Signal()
    selected_podcast_download_policy_changed = Signal()
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
    podcasts_refresh_completed = Signal()
    podcast_selection_completed = Signal(int)
    queue_refresh_completed = Signal()
    library_changed = Signal()

    def __init__(self, repo: Repository, downloads: DownloadManager, player: PlayerController, settings: SettingsStore):
        QCoreApplication.instance() or QCoreApplication([])
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.repo = repo
        self.downloads = downloads
        self.player = player
        self.settings = settings
        self._settings = self.settings.load()
        launch_at = datetime.now(timezone.utc).isoformat()
        if hasattr(self.settings, "record_launch"):
            self._new_since_at = self.settings.record_launch(launch_at)
        else:
            self._new_since_at = self._settings.last_launch_at
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
        self._selected_podcast_download_policy = "ask"
        self._refresh_all_in_progress = False
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
        self._auto_advance_in_progress = False
        self._downloads_episode_sort_mode = self.EPISODE_SORT_NEWEST
        self._worker_pool = QThreadPool.globalInstance()
        self._artwork_memo: dict[str, str] = {}
        self._artwork_jobs_in_flight: set[str] = set()
        self._worker_tasks: set[WorkerTask] = set()

        self._podcast_model = DictListModel([
            "podcast_id",
            "title",
            "feed_url",
            "download_policy",
            "artwork_source",
            "latest_episode_at",
            "latest_episode_display",
            "new_count",
            "is_stale",
            "stale_label",
        ])
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
            "is_new_since_launch",
            "is_unplayed",
            "is_in_progress",
            "episode_badge_label",
            "progress_seconds",
            "has_progress",
            "progress_display",
            "progress_percent",
        ])
        self._download_model = DictListModel([
            "episode_id",
            "title",
            "podcast_title",
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
        self._queue_model = DictListModel([
            "episode_id",
            "title",
            "duration",
            "podcast",
            "podcast_id",
            "now_playing",
            "podcast_artwork_url",
            "podcast_artwork_source",
        ])
        self._now_playing_episode_id: int | None = None
        self.subscriptions_service = SubscriptionService(self.repo, fetch_feed, self.download_episode)
        self.playback_service = PlaybackStateService(self.repo, self.player)
        self.downloads_state_service = DownloadsStateService(self.repo)
        self.queue_service = QueueService(self.repo, self._format_duration)
        self._downloads_by_episode = self.downloads_state_service.downloads_by_episode

        self.downloads.download_progress.connect(self._on_download_progress)
        self.downloads.download_status.connect(self._on_download_status)
        self.downloads.download_finished.connect(self._on_download_finished)
        self.downloads.download_failed.connect(self._on_download_failed)
        self.downloads.download_canceled.connect(self._on_download_canceled)
        self.player.position_changed.connect(self._on_player_position_changed)
        self.player.duration_changed.connect(self._on_player_duration_changed)
        self.player.playing_changed.connect(self._on_player_playing_changed)
        self.player.playback_finished.connect(self._on_player_finished)

        self._progress_save_timer = QTimer(self)
        self._progress_save_timer.setInterval(10_000)
        self._progress_save_timer.timeout.connect(self._persist_playback_progress)
        self._progress_save_timer.start()

        self._download_model_sync_timer = QTimer(self)
        self._download_model_sync_timer.setInterval(75)
        self._download_model_sync_timer.setSingleShot(True)
        self._download_model_sync_timer.timeout.connect(lambda: self._sync_download_model())

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
        if hasattr(self.downloads, "set_max_concurrent_downloads"):
            self.downloads.set_max_concurrent_downloads(self._settings.max_concurrent_downloads)

    def _run_worker(
        self,
        fn: Any,
        *args: Any,
        on_result: Any = None,
        on_error: Any = None,
        **kwargs: Any,
    ) -> None:
        task = WorkerTask(fn, *args, **kwargs)
        if on_result is not None:
            task.signals.result.connect(on_result)
        if on_error is not None:
            task.signals.error.connect(on_error)
        self._worker_tasks.add(task)
        task.signals.finished.connect(lambda task=task: self._worker_tasks.discard(task))
        self._worker_pool.start(task)

    @staticmethod
    def _artwork_cache_key(podcast_id: int, artwork_url: str | None) -> str | None:
        if not artwork_url:
            return None
        digest = hashlib.sha256(artwork_url.encode("utf-8")).hexdigest()
        return f"{podcast_id}:{digest}"

    def _memoized_artwork_source(self, podcast_id: int, artwork_url: str | None) -> str:
        key = self._artwork_cache_key(podcast_id, artwork_url)
        if key is None:
            return ""
        return self._artwork_memo.get(key, artwork_url or "")

    def _podcast_summary_fields(self, podcast_id: int) -> dict[str, Any]:
        summary = self.repo.podcast_episode_summary(podcast_id, new_since_at=self._new_since_at)
        latest = summary.get("latest_episode_at")
        latest_display = self._format_published_display(str(latest)) if latest else "No episodes"
        is_stale = False
        if latest:
            try:
                latest_dt = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
                if latest_dt.tzinfo is None:
                    latest_dt = latest_dt.replace(tzinfo=timezone.utc)
                is_stale = (datetime.now(timezone.utc) - latest_dt.astimezone(timezone.utc)).days >= 90
            except (TypeError, ValueError):
                is_stale = False
        return {
            "latest_episode_at": latest or "",
            "latest_episode_display": latest_display,
            "new_count": int(summary.get("new_count") or 0),
            "is_stale": is_stale,
            "stale_label": "Stale" if is_stale else "",
        }

    def _queue_artwork_cache_job(self, podcast_id: int, artwork_url: str | None, on_cached: Any) -> None:
        key = self._artwork_cache_key(podcast_id, artwork_url)
        if key is None or key in self._artwork_memo or key in self._artwork_jobs_in_flight:
            return
        self._artwork_jobs_in_flight.add(key)

        def _on_result(cached_value: object) -> None:
            resolved = str(cached_value or artwork_url or "")
            self._artwork_memo[key] = resolved
            self._artwork_jobs_in_flight.discard(key)
            on_cached(resolved)

        def _on_error(exc: Exception) -> None:
            self.logger.warning("Artwork cache failed for podcast id=%s: %s", podcast_id, exc)
            self._artwork_memo[key] = artwork_url or ""
            self._artwork_jobs_in_flight.discard(key)
            on_cached(artwork_url or "")

        self._run_worker(cache_podcast_artwork, artwork_url, on_result=_on_result, on_error=_on_error)

    def refresh_podcasts(self) -> None:
        self._podcast_items_all = []
        for podcast in self.repo.list_podcasts():
            item = asdict(podcast)
            item["podcast_id"] = item["id"]
            item["artwork_source"] = self._memoized_artwork_source(podcast.id, item.get("artwork_url"))
            item.update(self._podcast_summary_fields(podcast.id))
            self._podcast_items_all.append(item)
            self._queue_artwork_cache_job(
                podcast.id,
                item.get("artwork_url"),
                on_cached=lambda resolved, pid=podcast.id: self._on_podcast_artwork_cached(pid, resolved),
            )
        self._apply_subscription_filter()
        self.podcasts_refresh_completed.emit()

    def _refresh_library_models_after_feed_update(self, selected_podcast_id: int | None = None) -> None:
        self.refresh_podcasts()
        if selected_podcast_id is not None:
            self.select_podcast(selected_podcast_id)
        elif self.selected_podcast_id is not None and self.repo.get_podcast(self.selected_podcast_id) is None:
            self.select_podcast(-1)
        self.refresh_queue()
        self._load_downloads_from_library()
        self.library_changed.emit()

    @Slot(str)
    def add_feed(self, url: str) -> None:
        def _on_result(feed: object) -> None:
            result = self.subscriptions_service.add_feed_from_data(url, feed, self._settings.auto_download_policy)
            self._refresh_library_models_after_feed_update(result.podcast_id)
            self.info.emit(f"Subscribed to {result.title}")

        def _on_error(exc: Exception) -> None:
            self.logger.exception("Subscription failed for feed URL: %s", url)
            self.error.emit(f"Could not subscribe: {exc}")

        self._run_worker(fetch_feed, url, on_result=_on_result, on_error=_on_error)

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

    def _on_podcast_artwork_cached(self, podcast_id: int, artwork_source: str) -> None:
        updated = False
        for item in self._podcast_items_all:
            if item.get("podcast_id") == podcast_id and item.get("artwork_source") != artwork_source:
                item["artwork_source"] = artwork_source
                updated = True
        if self.selected_podcast_id == podcast_id and self._selected_podcast_artwork_url != artwork_source:
            self._selected_podcast_artwork_url = artwork_source
            self.selected_podcast_artwork_url_changed.emit()
        if updated:
            self._apply_subscription_filter()
        self._on_queue_artwork_cached(podcast_id, artwork_source)

    def _on_queue_artwork_cached(self, podcast_id: int, artwork_source: str) -> None:
        updated = False
        for item in self._queue_items_all:
            if item.get("podcast_id") == podcast_id and item.get("podcast_artwork_source") != artwork_source:
                item["podcast_artwork_source"] = artwork_source
                updated = True
        if updated:
            self._apply_queue_filter()
        for item in self._podcast_items_all:
            if item.get("podcast_id") == podcast_id and item.get("artwork_source") != artwork_source:
                item["artwork_source"] = artwork_source
                self._apply_subscription_filter()
                break

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

    @Property(str, notify=selected_podcast_download_policy_changed)
    def selected_podcast_download_policy(self) -> str:
        return self._selected_podcast_download_policy

    @selected_podcast_download_policy.setter
    def selected_podcast_download_policy(self, policy: str) -> None:
        if self.selected_podcast_id is None:
            return
        value = self.subscriptions_service.normalize_download_policy(policy)
        if self._selected_podcast_download_policy == value:
            return
        self.repo.set_podcast_download_policy(self.selected_podcast_id, value)
        if self.subscriptions_service.latest_limit_for_policy(value) is not None:
            self.subscriptions_service.apply_download_policy(self.selected_podcast_id, set(), value)
        self._selected_podcast_download_policy = value
        self.selected_podcast_download_policy_changed.emit()
        self.refresh_podcasts()

    @Slot(int)
    def select_podcast(self, podcast_id: int) -> None:
        podcast = next((podcast for podcast in self.repo.list_podcasts() if podcast.id == podcast_id), None)
        if podcast is None:
            if self.selected_podcast_id is not None:
                self.selected_podcast_id = None
                self.selected_podcast_id_changed.emit()
            self._set_selected_podcast_fields(title="", site_url="", description="", artwork_url="")
            self._episode_items_all = []
            self._apply_episode_filter_and_sort('main')
            self.podcast_selection_completed.emit(-1)
            return

        if self.selected_podcast_id != podcast.id:
            self.selected_podcast_id = podcast.id
            self.selected_podcast_id_changed.emit()
        if self._selected_podcast_download_policy != podcast.download_policy:
            self._selected_podcast_download_policy = podcast.download_policy
            self.selected_podcast_download_policy_changed.emit()
        self._set_selected_podcast_fields(
            title=podcast.title,
            site_url=podcast.site_url or "",
            description=podcast.description or "",
            artwork_url=self._memoized_artwork_source(podcast.id, podcast.artwork_url),
        )
        self._episode_items_all = []
        for episode in self.repo.episodes_for_podcast(podcast.id):
            self._episode_items_all.append(self._episode_item_from_row(episode))
        self._apply_episode_filter_and_sort('main')
        self._queue_artwork_cache_job(
            podcast.id,
            podcast.artwork_url,
            on_cached=lambda resolved, pid=podcast.id: self._on_podcast_artwork_cached(pid, resolved),
        )
        self.podcast_selection_completed.emit(podcast.id)
        
    @Slot()
    def _on_player_finished(self) -> None:
        if self._auto_advance_in_progress:
            return
        self._auto_advance_in_progress = True
        try:
            result = self.playback_service.on_player_finished(
                self._now_playing_episode_id,
                self._playback_position_ms,
                self._playback_duration_ms,
            )
            if result.completed_episode_id is not None:
                self._last_progress_persisted_ms = max(self._playback_position_ms, self._playback_duration_ms)
            next_episode_id = result.next_episode_id
            if next_episode_id is not None:
                next_episode = self.repo.get_episode(next_episode_id)
                message = (
                    f"Now playing next queued episode: {next_episode.title}"
                    if next_episode is not None
                    else "Now playing next queued episode…"
                )
                self.play_episode(next_episode_id, info_message=message)
                self.refresh_queue()
                return

            self._set_now_playing_episode(None)
            if self._playback_position_ms != 0:
                self._playback_position_ms = 0
                self.playback_position_ms_changed.emit()
            if self._playback_duration_ms != 0:
                self._playback_duration_ms = 0
                self.playback_duration_ms_changed.emit()
            self._last_progress_persisted_ms = -1
            self.refresh_queue()
        finally:
            self._auto_advance_in_progress = False

    @Slot(int)
    def remove_podcast(self, podcast_id: int) -> None:
        title = self.subscriptions_service.remove_podcast(podcast_id)
        if title is None:
            self.error.emit("Could not unsubscribe: podcast no longer exists")
            return

        selected_podcast_id = None if self.selected_podcast_id == podcast_id else self.selected_podcast_id
        self._refresh_library_models_after_feed_update(selected_podcast_id)
        self.info.emit(f"Unsubscribed from {title}")

    @Slot()
    def refresh_all_podcasts(self) -> None:
        if self._refresh_all_in_progress:
            return
        podcasts = self.repo.list_podcasts()
        if not podcasts:
            return
        self._refresh_all_in_progress = True

        def _refresh_all() -> list[int]:
            refreshed: list[int] = []
            for podcast in podcasts:
                feed = fetch_feed(podcast.feed_url)
                result = self.subscriptions_service.refresh_selected_with_feed(podcast.id, feed)
                if result is not None:
                    refreshed.append(result.podcast_id)
            return refreshed

        def _on_result(_ids: object) -> None:
            self._refresh_all_in_progress = False
            self._refresh_library_models_after_feed_update(self.selected_podcast_id)
            self.info.emit("Refreshed all feeds")

        def _on_error(exc: Exception) -> None:
            self._refresh_all_in_progress = False
            self.logger.exception("Refresh all failed")
            self.error.emit(f"Refresh all failed: {exc}")

        self._run_worker(_refresh_all, on_result=_on_result, on_error=_on_error)

    @Slot()
    def refresh_selected(self) -> None:
        if self.selected_podcast_id is None:
            return
        podcast = self.repo.get_podcast(self.selected_podcast_id)
        if podcast is None:
            return

        def _on_result(feed: object) -> None:
            result = self.subscriptions_service.refresh_selected_with_feed(
                podcast.id,
                feed,
            )
            if result is None:
                return
            self._refresh_library_models_after_feed_update(result.podcast_id)
            self.info.emit(f"Refreshed {result.title}")

        def _on_error(exc: Exception) -> None:
            self.logger.exception("Refresh failed for podcast id=%s", self.selected_podcast_id)
            self.error.emit(f"Refresh failed: {exc}")

        self._run_worker(fetch_feed, podcast.feed_url, on_result=_on_result, on_error=_on_error)

    @Slot(int)
    def play_episode(self, episode_id: int, *, info_message: str | None = None) -> None:
        self._play_episode_core(episode_id, require_local=False, info_message=info_message)

    def _play_episode_core(self, episode_id: int, *, require_local: bool, info_message: str | None = None) -> None:
        self._auto_advance_in_progress = True
        try:
            self._persist_playback_progress()
            self._now_playing_episode_id = None
            result = self.playback_service.play_episode(episode_id, prefer_download=require_local)
            if result is None:
                if require_local:
                    self.error.emit("Downloaded file is not available")
                else:
                    self.logger.error("Play requested for missing episode id=%s", episode_id)
                return
            self._set_now_playing_episode(episode_id)
            self._playback_position_ms = result.start_position_ms
            self.playback_position_ms_changed.emit()
            self.refresh_queue()
            self.info.emit(info_message or f"Playing {result.title}")
        finally:
            self._auto_advance_in_progress = False

    def _apply_download_policy(self, podcast_id: int, existing_guids: set[str]) -> None:
        podcast = self.repo.get_podcast(podcast_id)
        self.subscriptions_service.apply_download_policy(
            podcast_id,
            existing_guids,
            podcast.download_policy if podcast else "ask",
        )

    @Slot(int)
    def download_episode(self, episode_id: int) -> None:
        episode = self.repo.get_episode(episode_id)
        if not episode:
            self.logger.error("Download requested for missing episode id=%s", episode_id)
            return
        if self._episode_has_download(episode):
            self.info.emit(f"Already downloaded: {episode.title}")
            return

        def _queue_download_state() -> None:
            self.downloads_state_service.ensure_download_item(episode_id, episode.title)
            self.downloads_state_service.set_download_fields(episode_id, status="downloading", error_reason=None)

        self._update_download_state(_queue_download_state)
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
        self._play_episode_core(episode_id, require_local=True, info_message=None)

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
                podcast_row = self.repo.get_podcast(episode.podcast_id)
                podcast = podcast_row.title if podcast_row else ""
        if self._now_playing_title != title:
            self._now_playing_title = title
            self.now_playing_title_changed.emit()
        if self._now_playing_podcast != podcast:
            self._now_playing_podcast = podcast
            self.now_playing_podcast_changed.emit()

    def _persist_playback_progress(self) -> None:
        persisted = self.playback_service.persist_playback_progress(
            self._now_playing_episode_id,
            self._playback_position_ms,
            self._playback_duration_ms,
        )
        if persisted is None:
            return
        position_seconds, played = persisted
        self._last_progress_persisted_ms = self._playback_position_ms
        if self.selected_podcast_id is not None:
            selected_episode = next(
                (item for item in self._episode_items_all if item.get("id") == self._now_playing_episode_id),
                None,
            )
            if selected_episode is not None:
                selected_episode["progress_seconds"] = position_seconds
                selected_episode["played"] = int(played)
                self._populate_episode_badge_fields(selected_episode)
                self._populate_episode_progress_fields(selected_episode)
                self._apply_episode_filter_and_sort('main')

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
            if (
                self._now_playing_episode_id is not None
                and self._playback_duration_ms > 0
                and self._playback_position_ms >= self._playback_duration_ms
            ):
                self._on_player_finished()



    def refresh_queue(self) -> None:
        self._queue_items_all = self.queue_service.refresh_queue(
            self._now_playing_episode_id,
            "",
        )
        for item in self._queue_items_all:
            podcast_id = int(item.get("podcast_id", -1))
            artwork_url = item.get("podcast_artwork_url")
            item["podcast_artwork_source"] = self._memoized_artwork_source(podcast_id, artwork_url)
            self._queue_artwork_cache_job(
                podcast_id,
                artwork_url,
                on_cached=lambda resolved, pid=podcast_id: self._on_queue_artwork_cached(pid, resolved),
            )
        self._apply_queue_filter()
        self.queue_refresh_completed.emit()

    def _load_downloads_from_library(self) -> None:
        self.downloads_state_service.load_downloads_from_library()
        self._sync_download_model()

    @Slot(int)
    def enqueue_episode(self, episode_id: int) -> None:
        title = self.queue_service.enqueue_episode(episode_id)
        if title is None:
            self.logger.error("Queue requested for missing episode id=%s", episode_id)
            return
        self.refresh_queue()
        self.info.emit(f"Queued {title}")

    @Slot(int)
    def remove_queue_item(self, episode_id: int) -> None:
        self.queue_service.remove_queue_item(episode_id)
        self.refresh_queue()

    @Slot()
    def clear_queue(self) -> None:
        self.queue_service.clear_queue()
        self.refresh_queue()

    @Slot(int, int)
    def move_queue_item(self, episode_id: int, new_position: int) -> None:
        self.queue_service.move_queue_item(episode_id, new_position)
        self.refresh_queue()

    def _ensure_download_item(self, episode_id: int, title: str | None = None) -> dict[str, Any]:
        item: dict[str, Any] = {}

        def _action() -> None:
            nonlocal item
            item = self.downloads_state_service.ensure_download_item(episode_id, title)

        self._update_download_state(_action)
        return item

    def _set_download_fields(self, episode_id: int, **kwargs: Any) -> None:
        self._update_download_state(lambda: self.downloads_state_service.set_download_fields(episode_id, **kwargs))

    def _on_download_progress(self, episode_id: int, bytes_received: int, bytes_total: int, speed_bps: int) -> None:
        self._update_download_state(
            lambda: self.downloads_state_service.on_download_progress(episode_id, bytes_received, bytes_total, speed_bps),
            episode_id=episode_id,
            prefer_row_update=True,
        )

    def _on_download_status(self, episode_id: int, status: str) -> None:
        self._update_download_state(
            lambda: self.downloads_state_service.on_download_status(episode_id, status),
            episode_id=episode_id,
            prefer_row_update=True,
        )

    def _on_download_finished(self, episode_id: int, path: str) -> None:
        self._update_download_state(lambda: self.downloads_state_service.on_download_finished(episode_id, path))
        if self.selected_podcast_id is not None:
            self.select_podcast(self.selected_podcast_id)
        self.info.emit("Download complete")

    def _on_download_failed(self, episode_id: int, reason: str) -> None:
        self.logger.error("Download failed for episode_id=%s: %s", episode_id, reason)
        self._update_download_state(lambda: self.downloads_state_service.on_download_failed(episode_id, reason))
        self.error.emit(f"Download failed for {episode_id}: {reason}")

    def _on_download_canceled(self, episode_id: int) -> None:
        self._update_download_state(lambda: self.downloads_state_service.on_download_canceled(episode_id))
        if self.selected_podcast_id is not None:
            self.select_podcast(self.selected_podcast_id)

    def _update_download_state(
        self,
        action: Callable[[], None],
        *,
        episode_id: int | None = None,
        prefer_row_update: bool = False,
    ) -> None:
        before = dict(self._downloads_by_episode.get(episode_id, {})) if episode_id is not None else {}
        action()
        if prefer_row_update and episode_id is not None:
            self._update_existing_download_model_item(episode_id, before)
        self._schedule_download_model_sync()

    def _schedule_download_model_sync(self) -> None:
        if self._download_model_sync_timer.isActive():
            return
        self._download_model_sync_timer.start()

    def _update_existing_download_model_item(self, episode_id: int, before: dict[str, Any]) -> bool:
        item = self._downloads_by_episode.get(episode_id)
        if item is None:
            return False
        if before.get("section") != item.get("section"):
            return False
        if not self.downloads_state_service.matches_download_filter(item, self._download_filter):
            return False
        return self._download_model.update_item_by_key("episode_id", episode_id, item)

    def _sync_download_model(self) -> None:
        items = self.downloads_state_service.model_items(self._download_filter)
        self._download_model.set_items(items)

    @Slot(str)
    def set_subscription_filter(self, text: str) -> None:
        self._subscription_filter = text
        self._apply_subscription_filter()

    @Slot(str)
    def set_episode_filter(self, text: str) -> None:
        self._episode_filter = text
        self._apply_episode_filter_and_sort('main')

    @Slot(int)
    def set_episode_sort(self, mode: int) -> None:
        if mode not in {self.EPISODE_SORT_NEWEST, self.EPISODE_SORT_OLDEST, self.EPISODE_SORT_DURATION_DESC}:
            mode = self.EPISODE_SORT_NEWEST
        self._episode_sort_mode = mode
        self._apply_episode_filter_and_sort('main')

    @Slot(int)
    def set_episode_sort_downloads(self, mode: int) -> None:
        if mode not in {self.EPISODE_SORT_NEWEST, self.EPISODE_SORT_OLDEST, self.EPISODE_SORT_DURATION_DESC}:
            mode = self.EPISODE_SORT_NEWEST
        self._downloads_episode_sort_mode = mode
        self._apply_episode_filter_and_sort('download')

    @Slot(str)
    def set_download_filter(self, text: str) -> None:
        self._download_filter = text
        self._sync_download_model()

    @Slot(str)
    def set_queue_filter(self, text: str) -> None:
        self._queue_filter = text
        self._apply_queue_filter()

    def _apply_subscription_filter(self) -> None:
        self._podcast_model.set_items(
            filter_items_by_text(
                self._podcast_items_all,
                self._subscription_filter,
                fields=("title", "feed_url"),
            )
        )



    def _apply_episode_filter_and_sort(self, view) -> None:

        is_download = view == "download"
        sort_mode = self._episode_sort_mode

        if is_download:
            sort_mode = self._downloads_episode_sort_mode

        items = list(self._download_model._items) if is_download else list(self._episode_items_all)

        filter_text = self._episode_filter.strip().lower()
        if filter_text:
            items = [item for item in items if filter_text in (item.get("title") or "").lower()]

        if sort_mode == self.EPISODE_SORT_OLDEST:
            items.sort(key=lambda item: (self._episode_timestamp(item), (item.get("title") or "").lower(), item.get("id", 0)))
        elif sort_mode == self.EPISODE_SORT_DURATION_DESC:
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
        if is_download:
            self._download_model.set_items(items)
        else:
            self._episode_model.set_items(items)

    def _apply_queue_filter(self) -> None:
        self._queue_model.set_items(self.queue_service.apply_filter(self._queue_items_all, self._queue_filter))

    @staticmethod
    def _episode_timestamp(item: dict[str, Any]) -> float:
        time_stamp = item.get("published_at")
        if not time_stamp:
            time_stamp = item.get("completed_at")
            if not time_stamp:
                return 0.0          
        if isinstance(time_stamp, str) and time_stamp.endswith("Z"):
            time_stamp = f"{time_stamp[:-1]}+00:00"
        try:
            return datetime.fromisoformat(time_stamp).timestamp()
        except (TypeError, ValueError):
            return 0.0

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
        item["local_path"] = self._normalized_local_path(item.get("local_path"))
        item["duration"] = self._format_duration(item.get("duration_seconds"))
        item["published_display"] = self._format_published_display(item.get("published_at"))
        self._populate_episode_badge_fields(item)
        self._populate_episode_progress_fields(item)
        return item

    def _populate_episode_badge_fields(self, item: dict[str, Any]) -> None:
        discovered_at = item.get("discovered_at")
        is_new_since_launch = bool(self._new_since_at and discovered_at and discovered_at > self._new_since_at)
        played = bool(item.get("played"))
        progress_seconds = max(0, int(item.get("progress_seconds") or 0))
        duration_seconds = int(item.get("duration_seconds") or 0)
        is_in_progress = (
            not played
            and progress_seconds > 0
            and not PlaybackStateService.is_near_completion(progress_seconds * 1000, duration_seconds * 1000)
        )
        is_unplayed = not played
        if played:
            badge_label = "Played"
        elif is_in_progress:
            badge_label = "In progress"
        elif is_new_since_launch:
            badge_label = "New"
        elif is_unplayed:
            badge_label = "Unplayed"
        else:
            badge_label = ""
        item["is_new_since_launch"] = is_new_since_launch
        item["is_unplayed"] = is_unplayed
        item["is_in_progress"] = is_in_progress
        item["episode_badge_label"] = badge_label

    def _populate_episode_progress_fields(self, item: dict[str, Any]) -> None:
        progress_seconds = max(0, int(item.get("progress_seconds") or 0))
        duration_seconds = item.get("duration_seconds")
        duration_seconds = int(duration_seconds) if duration_seconds else 0
        has_progress = progress_seconds > 0 and not bool(item.get("played"))
        item["has_progress"] = has_progress
        if has_progress:
            progress_display = f"{self._format_duration(progress_seconds)} listened"
            if duration_seconds > 0:
                progress_display = f"{self._format_duration(progress_seconds)} / {self._format_duration(duration_seconds)}"
            item["progress_display"] = progress_display
            item["progress_percent"] = min(100, max(0, round((progress_seconds / duration_seconds) * 100))) if duration_seconds > 0 else 0
        else:
            item["progress_display"] = ""
            item["progress_percent"] = 0

    @staticmethod
    def _normalized_local_path(local_path: str | None) -> str:
        if local_path is None:
            return ""
        return str(local_path).strip()

    @classmethod
    def _episode_has_download(cls, episode: Any) -> bool:
        return bool(cls._normalized_local_path(getattr(episode, "local_path", None)))

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

    @Slot(bool)
    def set_startup_behavior_enabled(self, enabled: bool) -> None:
        self.startup_behavior = enabled

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

    @Slot(bool)
    def set_notifications_enabled(self, enabled: bool) -> None:
        self.notifications_enabled = enabled

    @Property(bool, notify=refresh_feeds_on_startup_changed)
    def refresh_feeds_on_startup(self) -> bool:
        return self._settings.refresh_feeds_on_startup

    @refresh_feeds_on_startup.setter
    def refresh_feeds_on_startup(self, enabled: bool) -> None:
        value = bool(enabled)
        if self._settings.refresh_feeds_on_startup == value:
            return
        self.settings.set_refresh_feeds_on_startup(value)
        self._settings = replace(self._settings, refresh_feeds_on_startup=value)
        self.refresh_feeds_on_startup_changed.emit()

    @Slot(bool)
    def set_refresh_feeds_on_startup_enabled(self, enabled: bool) -> None:
        self.refresh_feeds_on_startup = enabled

    @Property(bool, notify=sync_server_enabled_changed)
    def sync_server_enabled(self) -> bool:
        return self._settings.sync_server_enabled

    @sync_server_enabled.setter
    def sync_server_enabled(self, enabled: bool) -> None:
        value = bool(enabled)
        if self._settings.sync_server_enabled == value:
            return
        self.settings.set_sync_server_enabled(value)
        self._settings = replace(self._settings, sync_server_enabled=value)
        self.sync_server_enabled_changed.emit()
        self.info.emit("Restart PlainPod for the sync server setting to take effect.")


    @Property(str, notify=sync_server_host_changed)
    def sync_server_host(self) -> str:
        return self._settings.sync_server_host

    @sync_server_host.setter
    def sync_server_host(self, host: str) -> None:
        value = self.settings.set_sync_server_host(host)
        if self._settings.sync_server_host == value:
            return
        self._settings = replace(self._settings, sync_server_host=value)
        self.sync_server_host_changed.emit()
        self.info.emit("Restart PlainPod for the sync server setting to take effect.")

    @Property(int, notify=sync_server_port_changed)
    def sync_server_port(self) -> int:
        return self._settings.sync_server_port

    @sync_server_port.setter
    def sync_server_port(self, port: int) -> None:
        value = self.settings.set_sync_server_port(port)
        if self._settings.sync_server_port == value:
            return
        self._settings = replace(self._settings, sync_server_port=value)
        self.sync_server_port_changed.emit()
        self.info.emit("Restart PlainPod for the sync server setting to take effect.")

    @Property(str, notify=sync_server_username_changed)
    def sync_server_username(self) -> str:
        return self._settings.sync_server_username

    @sync_server_username.setter
    def sync_server_username(self, username: str) -> None:
        value = self.settings.set_sync_server_username(username)
        if self._settings.sync_server_username == value:
            return
        self._settings = replace(self._settings, sync_server_username=value)
        self.sync_server_username_changed.emit()
        self.info.emit("Restart PlainPod for the sync server setting to take effect.")

    @Property(bool, notify=sync_server_require_auth_changed)
    def sync_server_require_auth(self) -> bool:
        return self._settings.sync_server_require_auth

    @sync_server_require_auth.setter
    def sync_server_require_auth(self, enabled: bool) -> None:
        value = bool(enabled)
        if self._settings.sync_server_require_auth == value:
            return
        self.settings.set_sync_server_require_auth(value)
        self._settings = replace(self._settings, sync_server_require_auth=value)
        self.sync_server_require_auth_changed.emit()
        self.info.emit("Restart PlainPod for the sync server setting to take effect.")

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

    @Property(int, notify=max_concurrent_downloads_changed)
    def max_concurrent_downloads(self) -> int:
        return self._settings.max_concurrent_downloads

    @max_concurrent_downloads.setter
    def max_concurrent_downloads(self, count: int) -> None:
        value = self.settings.set_max_concurrent_downloads(count)
        if self._settings.max_concurrent_downloads == value:
            return
        self._settings = replace(self._settings, max_concurrent_downloads=value)
        if hasattr(self.downloads, "set_max_concurrent_downloads"):
            self.downloads.set_max_concurrent_downloads(value)
        self.max_concurrent_downloads_changed.emit()

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
