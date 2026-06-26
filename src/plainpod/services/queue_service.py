from __future__ import annotations

from typing import Any, Callable

from plainpod.filtering import filter_items_by_text
from plainpod.repository import Repository


class QueueService:
    def __init__(self, repo: Repository, format_duration: Callable[[int | None], str]):
        self._repo = repo
        self._format_duration = format_duration

    def enqueue_episode(self, episode_id: int) -> str | None:
        episode = self._repo.get_episode(episode_id)
        if episode is None:
            return None
        self._repo.enqueue(episode_id)
        return episode.title

    def move_queue_item(self, episode_id: int, new_position: int) -> None:
        self._repo.reorder_queue(episode_id, new_position)

    def remove_queue_item(self, episode_id: int) -> None:
        self._repo.remove_from_queue(episode_id)

    def clear_queue(self) -> None:
        self._repo.clear_queue()

    def refresh_queue(self, now_playing_episode_id: int | None, filter_text: str = "") -> list[dict[str, Any]]:
        podcasts = list(self._repo.list_podcasts())
        podcast_titles = {podcast.id: podcast.title for podcast in podcasts}
        podcast_artwork_urls = {podcast.id: (podcast.artwork_url or "") for podcast in podcasts}
        items: list[dict[str, Any]] = []
        for episode_id in self._repo.list_queue():
            episode = self._repo.get_episode(episode_id)
            if episode is None:
                continue
            items.append(
                {
                    "episode_id": episode.id,
                    "title": episode.title,
                    "duration": self._format_duration(episode.duration_seconds),
                    "podcast": podcast_titles.get(episode.podcast_id, "Unknown podcast"),
                    "podcast_id": episode.podcast_id,
                    "now_playing": episode.id == now_playing_episode_id,
                    "podcast_artwork_url": podcast_artwork_urls.get(episode.podcast_id, ""),
                    "podcast_artwork_source": podcast_artwork_urls.get(episode.podcast_id, ""),
                }
            )
        return self.apply_filter(items, filter_text)

    @staticmethod
    def apply_filter(items: list[dict[str, Any]], filter_text: str) -> list[dict[str, Any]]:
        return filter_items_by_text(items, filter_text, fields=("title", "podcast"))
