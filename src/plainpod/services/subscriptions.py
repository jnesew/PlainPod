from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from plainpod.repository import Repository


@dataclass
class SubscriptionResult:
    podcast_id: int
    title: str


class SubscriptionService:
    def __init__(
        self,
        repo: Repository,
        fetch_feed_fn: Callable[[str], object],
        download_episode_fn: Callable[[int], None],
        *,
        sync_username: str = "plainpod",
        sync_device_id: str = "plainpod",
    ) -> None:
        self._repo = repo
        self._fetch_feed = fetch_feed_fn
        self._download_episode = download_episode_fn
        self._sync_username = sync_username
        self._sync_device_id = sync_device_id

    def add_feed(self, url: str, default_download_policy: str) -> SubscriptionResult:
        feed = self._fetch_feed(url)
        return self.add_feed_from_data(url, feed, default_download_policy)

    def add_feed_from_data(self, url: str, feed: object, default_download_policy: str) -> SubscriptionResult:
        policy = self.normalize_download_policy(default_download_policy)
        podcast_id = self._repo.add_podcast(
            title=feed.title,
            feed_url=url,
            site_url=feed.site_url,
            description=feed.description,
            artwork_url=feed.artwork_url,
            download_policy=policy,
        )
        existing_guids = {episode.guid for episode in self._repo.episodes_for_podcast(podcast_id)}
        self._repo.upsert_episodes(podcast_id, feed.episodes)
        self.apply_download_policy(podcast_id, existing_guids, policy)
        self._repo.record_subscription_event(self._sync_username, self._sync_device_id, url, "add")
        return SubscriptionResult(podcast_id=podcast_id, title=feed.title)

    def refresh_selected(self, podcast_id: int, _default_download_policy: str = "ask") -> SubscriptionResult | None:
        podcast = self._repo.get_podcast(podcast_id)
        if podcast is None:
            return None
        feed = self._fetch_feed(podcast.feed_url)
        return self.refresh_selected_with_feed(podcast_id, feed)

    def refresh_selected_with_feed(self, podcast_id: int, feed: object, _default_download_policy: str = "ask") -> SubscriptionResult | None:
        podcast = self._repo.get_podcast(podcast_id)
        if podcast is None:
            return None
        existing_guids = {episode.guid for episode in self._repo.episodes_for_podcast(podcast.id)}
        self._repo.upsert_episodes(podcast.id, feed.episodes)
        self.apply_download_policy(podcast.id, existing_guids, podcast.download_policy)
        return SubscriptionResult(podcast_id=podcast.id, title=podcast.title)

    @staticmethod
    def normalize_download_policy(policy: str | None) -> str:
        allowed = {"ask", "off", "new_episodes", "latest_1", "latest_3", "latest_5", "latest_10"}
        return policy if policy in allowed else "ask"

    @staticmethod
    def latest_limit_for_policy(policy: str) -> int | None:
        if not policy.startswith("latest_"):
            return None
        try:
            return max(1, int(policy.removeprefix("latest_")))
        except ValueError:
            return None

    def apply_download_policy(self, podcast_id: int, existing_guids: set[str], download_policy: str) -> None:
        policy = self.normalize_download_policy(download_policy)
        if policy in {"ask", "off"}:
            return
        all_episodes = self._repo.episodes_for_podcast(podcast_id)
        limit = self.latest_limit_for_policy(policy)
        if limit is not None:
            latest_window = all_episodes[:limit]
            for episode in latest_window:
                if not episode.local_path:
                    self._download_episode(episode.id)
            return
        if policy == "new_episodes":
            new_episodes = [
                episode
                for episode in all_episodes
                if not episode.local_path and episode.guid not in existing_guids
            ]
            for episode in new_episodes:
                self._download_episode(episode.id)

    def remove_podcast(self, podcast_id: int) -> str | None:
        podcast = self._repo.get_podcast(podcast_id)
        if podcast is None:
            return None
        self._repo.remove_podcast(podcast_id)
        self._repo.record_subscription_event(self._sync_username, self._sync_device_id, podcast.feed_url, "remove")
        return podcast.title
