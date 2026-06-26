from __future__ import annotations

from dataclasses import dataclass

from plainpod.repository import Episode, Repository


@dataclass
class PlayResult:
    episode_id: int
    title: str
    start_position_ms: int


@dataclass
class FinishedResult:
    completed_episode_id: int | None
    next_episode_id: int | None
    reset_state: bool


class PlaybackStateService:
    def __init__(self, repo: Repository, player, *, sync_username: str = "plainpod", sync_device_id: str = "plainpod"):
        self._repo = repo
        self._player = player
        self._sync_username = sync_username
        self._sync_device_id = sync_device_id

    def play_episode(self, episode_id: int, *, prefer_download: bool = False) -> PlayResult | None:
        episode = self._repo.get_episode(episode_id)
        if episode is None:
            return None
        start_position_ms = self.resume_position_ms_for_episode(episode)
        if prefer_download:
            if not episode.local_path:
                return None
            self._repo.remove_from_queue(episode_id)
            self._player.play_file(episode.local_path, start_position_ms=start_position_ms)
        elif episode.local_path:
            self._repo.remove_from_queue(episode_id)
            self._player.play_file(episode.local_path, start_position_ms=start_position_ms)
        else:
            self._repo.remove_from_queue(episode_id)
            self._player.play_url(episode.media_url, start_position_ms=start_position_ms)
        return PlayResult(episode_id=episode_id, title=episode.title, start_position_ms=start_position_ms)

    def on_player_finished(self, now_playing_episode_id: int | None, position_ms: int, duration_ms: int) -> FinishedResult:
        if now_playing_episode_id is not None:
            final_position_ms = max(position_ms, duration_ms)
            self._repo.update_episode_progress(now_playing_episode_id, max(0, final_position_ms // 1000), played=True)
            self._record_play_action(now_playing_episode_id, final_position_ms // 1000, duration_ms // 1000)
        next_episode_id = self._repo.dequeue_next()
        return FinishedResult(
            completed_episode_id=now_playing_episode_id,
            next_episode_id=next_episode_id,
            reset_state=next_episode_id is None,
        )

    def persist_playback_progress(
        self,
        now_playing_episode_id: int | None,
        position_ms: int,
        duration_ms: int,
    ) -> tuple[int, bool] | None:
        if now_playing_episode_id is None:
            return None
        if position_ms <= 0 and duration_ms <= 0:
            return None
        position_seconds = max(0, position_ms // 1000)
        played = self.is_near_completion(position_ms, duration_ms)
        self._repo.update_episode_progress(now_playing_episode_id, position_seconds, played=played)
        self._record_play_action(now_playing_episode_id, position_seconds, duration_ms // 1000 if duration_ms > 0 else None)
        return position_seconds, played

    def _record_play_action(self, episode_id: int, position_seconds: int, total_seconds: int | None) -> None:
        episode = self._repo.get_episode(episode_id)
        if episode is None:
            return
        podcast = self._repo.get_podcast(episode.podcast_id)
        if podcast is None:
            return
        total = total_seconds if total_seconds and total_seconds > 0 else episode.duration_seconds
        self._repo.record_episode_action(
            self._sync_username,
            self._sync_device_id,
            podcast.feed_url,
            episode.media_url,
            "play",
            position=max(0, position_seconds),
            total=total,
        )

    @staticmethod
    def resume_position_ms_for_episode(episode: Episode) -> int:
        if bool(episode.played):
            return 0
        return max(0, int(episode.progress_seconds or 0) * 1000)

    @staticmethod
    def is_near_completion(position_ms: int, duration_ms: int) -> bool:
        if duration_ms <= 0:
            return False
        return position_ms >= max(duration_ms - 15_000, int(duration_ms * 0.95))
