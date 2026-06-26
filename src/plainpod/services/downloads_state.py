from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plainpod.filtering import filter_items_by_text
from plainpod.repository import Repository


class DownloadsStateService:
    def __init__(self, repo: Repository):
        self._repo = repo
        self.downloads_by_episode: dict[int, dict[str, Any]] = {}

    def ensure_download_item(self, episode_id: int, title: str | None = None) -> dict[str, Any]:
        item = self.downloads_by_episode.get(episode_id)
        if item is not None:
            return item
        episode = self._repo.get_episode(episode_id)
        item = {
            "episode_id": episode_id,
            "title": title or (episode.title if episode else f"Episode {episode_id}"),
            "podcast_title": self._repo.podcast_title_for_episode(episode_id),
            "status": "downloading",
            "bytes_received": 0,
            "bytes_total": 0,
            "progress_percent": 0,
            "speed_bps": 0,
            "file_path": episode.local_path if episode else "",
            "completed_at": "",
            "error_reason": "",
            "section": "Downloading",
            "progress_label": "0 B / ?",
            "speed_label": "0 B/s",
        }
        self.downloads_by_episode[episode_id] = item
        return item

    def set_download_fields(self, episode_id: int, **kwargs: Any) -> dict[str, Any]:
        item = self.ensure_download_item(episode_id)
        item.update(kwargs)
        status = item.get("status", "downloading")
        item["section"] = "Completed" if status == "completed" else "Downloading"
        item["progress_label"] = self.format_progress(item.get("bytes_received", 0), item.get("bytes_total", 0))
        if status == "completed" and item.get("completed_at"):
            item["speed_label"] = f"Downloaded on {item['completed_at']}"
        elif status == "failed":
            item["speed_label"] = item.get("error_reason") or "Failed"
        elif status == "paused":
            item["speed_label"] = "Paused"
        elif status == "canceled":
            item["speed_label"] = "Canceled"
        else:
            item["speed_label"] = f"{self.format_bytes(item.get('speed_bps', 0))}/s"
        return item

    def on_download_progress(self, episode_id: int, bytes_received: int, bytes_total: int, speed_bps: int) -> None:
        progress_percent = int((bytes_received / bytes_total) * 100) if bytes_total > 0 else 0
        self.set_download_fields(
            episode_id,
            status="downloading",
            bytes_received=bytes_received,
            bytes_total=bytes_total,
            progress_percent=progress_percent,
            speed_bps=speed_bps,
            error_reason="",
        )

    def on_download_status(self, episode_id: int, status: str) -> None:
        self.set_download_fields(episode_id, status=status)

    def on_download_finished(self, episode_id: int, path: str) -> None:
        self._repo.mark_downloaded(episode_id, path)
        completed_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        item = self.ensure_download_item(episode_id)
        total = item.get("bytes_total") or item.get("bytes_received")
        self.set_download_fields(
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

    def on_download_failed(self, episode_id: int, reason: str) -> None:
        self.set_download_fields(episode_id, status="failed", error_reason=reason, speed_bps=0)

    def on_download_canceled(self, episode_id: int) -> None:
        self.set_download_fields(
            episode_id,
            status="canceled",
            bytes_received=0,
            bytes_total=0,
            progress_percent=0,
            speed_bps=0,
            file_path="",
        )
        self._repo.mark_downloaded(episode_id, None)

    def load_downloads_from_library(self, path_exists_fn=Path.exists) -> None:
        for episode in self._repo.list_downloaded_episodes():
            local_path = episode.local_path or ""
            if not local_path:
                continue
            path = Path(local_path)
            if not path_exists_fn(path):
                self._repo.mark_downloaded(episode.id, None)
                continue
            stat = path.stat()
            completed_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
            self.downloads_by_episode[episode.id] = {
                "episode_id": episode.id,
                "title": episode.title,
                "podcast_title": episode.podcast_title,
                "status": "completed",
                "bytes_received": stat.st_size,
                "bytes_total": stat.st_size,
                "progress_percent": 100,
                "speed_bps": 0,
                "file_path": str(path),
                "completed_at": completed_at,
                "error_reason": "",
                "section": "Completed",
                "progress_label": self.format_progress(stat.st_size, stat.st_size),
                "speed_label": f"Downloaded on {completed_at}",
            }

    def model_items(self, filter_text: str = "") -> list[dict[str, Any]]:
        items = sorted(
            self.downloads_by_episode.values(),
            key=lambda item: (item.get("section") != "Downloading", item.get("title", "").lower()),
        )
        return filter_items_by_text(
            items,
            filter_text,
            fields=("title", "podcast_title", "status", "section", "error_reason"),
        )

    @staticmethod
    def matches_download_filter(item: dict[str, Any], filter_text: str) -> bool:
        return bool(
            filter_items_by_text(
                [item],
                filter_text,
                fields=("title", "podcast_title", "status", "section", "error_reason"),
            )
        )

    @staticmethod
    def format_bytes(value: int) -> str:
        value_f = float(max(value, 0))
        units = ["B", "KB", "MB", "GB"]
        idx = 0
        while value_f >= 1024 and idx < len(units) - 1:
            value_f /= 1024
            idx += 1
        return f"{value_f:.1f} {units[idx]}"

    def format_progress(self, received: int, total: int) -> str:
        if total > 0:
            return f"{self.format_bytes(received)} / {self.format_bytes(total)}"
        return f"{self.format_bytes(received)} / ?"
