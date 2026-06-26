from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Iterable
from email.utils import parsedate_to_datetime # Add this import
from urllib.parse import urlparse


_ALLOWED_MEDIA_URL_SCHEMES = frozenset({"https", "http"})


def _is_allowed_media_url(media_url: str) -> bool:
    return urlparse(media_url).scheme.lower() in _ALLOWED_MEDIA_URL_SCHEMES

@dataclass
class Podcast:
    id: int
    title: str
    feed_url: str
    site_url: str | None
    description: str | None
    artwork_url: str | None
    download_policy: str


@dataclass
class Episode:
    id: int
    podcast_id: int
    guid: str
    title: str
    published_at: str | None
    duration_seconds: int | None
    description: str | None
    media_url: str
    local_path: str | None
    played: int
    progress_seconds: int
    discovered_at: str


@dataclass
class DownloadedEpisode:
    id: int
    podcast_id: int
    guid: str
    title: str
    published_at: str | None
    duration_seconds: int | None
    description: str | None
    media_url: str
    local_path: str | None
    played: int
    progress_seconds: int
    discovered_at: str
    podcast_title: str


@dataclass
class SyncDevice:
    username: str
    device_id: str
    caption: str | None
    type: str | None


@dataclass
class SyncSubscriptionEvent:
    sequence: int
    username: str
    device_id: str | None
    feed_url: str
    action: str


@dataclass
class SyncEpisodeAction:
    sequence: int
    username: str
    device_id: str | None
    podcast_url: str
    episode_url: str
    action: str
    started: int | None
    position: int | None
    total: int | None


class Repository:
    def __init__(self, db_file: Path):
        self.logger = logging.getLogger(__name__)
        self.db_file = db_file
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS podcasts (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                feed_url TEXT NOT NULL UNIQUE,
                site_url TEXT,
                description TEXT,
                artwork_url TEXT,
                download_policy TEXT NOT NULL DEFAULT 'ask'
            );

            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY,
                podcast_id INTEGER NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
                guid TEXT NOT NULL,
                title TEXT NOT NULL,
                published_at TEXT,
                duration_seconds INTEGER,
                description TEXT,
                media_url TEXT NOT NULL,
                local_path TEXT,
                played INTEGER NOT NULL DEFAULT 0,
                progress_seconds INTEGER NOT NULL DEFAULT 0,
                discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(podcast_id, guid)
            );

            CREATE TABLE IF NOT EXISTS queue (
                episode_id INTEGER PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
                position INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_episodes_podcast_media_url
            ON episodes(podcast_id, media_url);

            CREATE TABLE IF NOT EXISTS sync_devices (
                username TEXT NOT NULL,
                device_id TEXT NOT NULL,
                caption TEXT,
                type TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(username, device_id)
            );

            CREATE TABLE IF NOT EXISTS sync_sequence (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                value INTEGER NOT NULL
            );

            INSERT OR IGNORE INTO sync_sequence(id, value) VALUES(1, 0);

            CREATE TABLE IF NOT EXISTS sync_subscription_events (
                sequence INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                device_id TEXT,
                feed_url TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('add', 'remove'))
            );

            CREATE TABLE IF NOT EXISTS sync_episode_actions (
                sequence INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                device_id TEXT,
                podcast_url TEXT NOT NULL,
                episode_url TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('play', 'new', 'download', 'delete')),
                started INTEGER,
                position INTEGER,
                total INTEGER
            );
            """
        )
        episode_columns = {row["name"] for row in cur.execute("PRAGMA table_info(episodes)").fetchall()}
        if "discovered_at" not in episode_columns:
            migration_time = datetime.now(timezone.utc).isoformat()
            cur.execute("ALTER TABLE episodes ADD COLUMN discovered_at TEXT NOT NULL DEFAULT ''")
            cur.execute(
                "UPDATE episodes SET discovered_at = COALESCE(published_at, ?) WHERE discovered_at = ''",
                (migration_time,),
            )
        self.conn.commit()

    def add_podcast(self, *, title: str, feed_url: str, site_url: str | None, description: str | None, artwork_url: str | None, download_policy: str | None = None) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO podcasts(title, feed_url, site_url, description, artwork_url, download_policy)
            VALUES(?,?,?,?,?,COALESCE(?, 'ask'))
            ON CONFLICT(feed_url) DO UPDATE SET
                title=excluded.title,
                site_url=COALESCE(excluded.site_url, podcasts.site_url),
                description=COALESCE(excluded.description, podcasts.description),
                artwork_url=COALESCE(excluded.artwork_url, podcasts.artwork_url),
                download_policy=COALESCE(?, podcasts.download_policy)
            """,
            (title, feed_url, site_url, description, artwork_url, download_policy, download_policy),
        )
        self.conn.commit()
        row = cur.execute("SELECT id FROM podcasts WHERE feed_url=?", (feed_url,)).fetchone()
        assert row is not None
        self.logger.info("Podcast upserted: id=%s title=%s", row["id"], title)
        return int(row["id"])

    def list_podcasts(self) -> list[Podcast]:
        rows = self.conn.execute(
            """
            SELECT p.*
            FROM podcasts p
            LEFT JOIN (
                SELECT podcast_id, MAX(datetime(published_at)) AS latest_episode_at
                FROM episodes
                GROUP BY podcast_id
            ) latest ON latest.podcast_id = p.id
            ORDER BY latest.latest_episode_at DESC NULLS LAST, p.title COLLATE NOCASE
            """
        ).fetchall()
        return [Podcast(**dict(r)) for r in rows]

    def set_podcast_download_policy(self, podcast_id: int, policy: str) -> None:
        self.conn.execute("UPDATE podcasts SET download_policy=? WHERE id=?", (policy, podcast_id))
        self.conn.commit()

    def podcast_episode_summary(self, podcast_id: int, new_since_at: str | None = None) -> dict[str, object]:
        if new_since_at:
            row = self.conn.execute(
                """
                SELECT
                    COUNT(*) AS episode_count,
                    SUM(CASE WHEN discovered_at > ? THEN 1 ELSE 0 END) AS new_count,
                    MAX(datetime(published_at)) AS latest_episode_at
                FROM episodes
                WHERE podcast_id=?
                """,
                (new_since_at, podcast_id),
            ).fetchone()
        else:
            row = self.conn.execute(
                """
                SELECT
                    COUNT(*) AS episode_count,
                    SUM(CASE WHEN played = 0 THEN 1 ELSE 0 END) AS new_count,
                    MAX(datetime(published_at)) AS latest_episode_at
                FROM episodes
                WHERE podcast_id=?
                """,
                (podcast_id,),
            ).fetchone()
        return {
            "episode_count": int(row["episode_count"] or 0),
            "new_count": int(row["new_count"] or 0),
            "latest_episode_at": row["latest_episode_at"],
        }

    def get_podcast(self, podcast_id: int) -> Podcast | None:
        row = self.conn.execute("SELECT * FROM podcasts WHERE id=?", (podcast_id,)).fetchone()
        if row is None:
            return None
        return Podcast(**dict(row))

    def get_podcast_by_feed_url(self, feed_url: str) -> Podcast | None:
        row = self.conn.execute("SELECT * FROM podcasts WHERE feed_url=?", (feed_url,)).fetchone()
        if row is None:
            return None
        return Podcast(**dict(row))

    def remove_podcast(self, podcast_id: int) -> None:
        self.conn.execute("DELETE FROM podcasts WHERE id=?", (podcast_id,))
        self.conn.commit()

    def upsert_episodes(self, podcast_id: int, episodes: Iterable[dict]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        count = 0
        for ep in episodes:
            media_url = ep["media_url"]
            if not _is_allowed_media_url(media_url):
                raise ValueError(f"Unsupported media_url scheme for episode '{ep.get('guid') or ep.get('title') or media_url}'")
            guid = ep.get("guid") or ep["media_url"]
            cur.execute(
                """
                INSERT INTO episodes(
                    podcast_id, guid, title, published_at, duration_seconds,
                    description, media_url, discovered_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(podcast_id, guid) DO UPDATE SET
                    title=excluded.title,
                    published_at=excluded.published_at,
                    duration_seconds=excluded.duration_seconds,
                    description=excluded.description,
                    media_url=excluded.media_url
                """,
                (
                    podcast_id,
                    guid,
                    ep.get("title") or "Untitled episode",
                    self.normalize_dt(ep.get("published_at")) or now,
                    ep.get("duration_seconds"),
                    ep.get("description"),
                    media_url,
                    now,
                ),
            )
            count += 1
        self.conn.commit()
        return count

    def normalize_dt(self, value):
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            text = value.rstrip("Z") + "+00:00" if value.endswith("Z") else value
            try:
                # Try ISO-8601 first (for Atom feeds)
                dt = datetime.fromisoformat(text)
            except ValueError:
                try:
                    # Fallback for standard RSS dates (RFC 2822)
                    dt = parsedate_to_datetime(text)
                except (ValueError, TypeError):
                    return None
        else:
            return None

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    
    def episodes_for_podcast(self, podcast_id: int) -> list[Episode]:
        rows = self.conn.execute(
            """
            SELECT * FROM episodes WHERE podcast_id=?
            ORDER BY datetime(published_at) DESC
            """,
            (podcast_id,),
        ).fetchall()
        return [Episode(**dict(r)) for r in rows]

    def get_episode(self, episode_id: int) -> Episode | None:
        row = self.conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if row is None:
            return None
        return Episode(**dict(row))

    def get_episode_by_media_url(self, podcast_id: int, media_url: str) -> Episode | None:
        row = self.conn.execute(
            "SELECT * FROM episodes WHERE podcast_id=? AND media_url=? ORDER BY id LIMIT 1",
            (podcast_id, media_url),
        ).fetchone()
        if row is None:
            return None
        return Episode(**dict(row))

    def list_downloaded_episodes(self) -> list[DownloadedEpisode]:
        rows = self.conn.execute(
            """
            SELECT e.*, p.title AS podcast_title
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.local_path IS NOT NULL AND TRIM(e.local_path) <> ''
            ORDER BY datetime(e.published_at) DESC, e.id DESC
            """
        ).fetchall()
        return [DownloadedEpisode(**dict(r)) for r in rows]

    def podcast_title_for_episode(self, episode_id: int) -> str:
        row = self.conn.execute(
            """
            SELECT p.title
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.id=?
            """,
            (episode_id,),
        ).fetchone()
        return str(row["title"]) if row is not None else ""

    def update_episode_progress(self, episode_id: int, seconds: int, played: bool = False) -> None:
        self.conn.execute(
            "UPDATE episodes SET progress_seconds=?, played=? WHERE id=?",
            (seconds, int(played), episode_id),
        )
        self.conn.commit()

    def update_episode_progress_by_media_url(
        self,
        podcast_feed_url: str,
        episode_media_url: str,
        seconds: int,
        played: bool = False,
    ) -> bool:
        podcast = self.get_podcast_by_feed_url(podcast_feed_url)
        if podcast is None:
            return False
        episode = self.get_episode_by_media_url(podcast.id, episode_media_url)
        if episode is None:
            return False
        self.update_episode_progress(episode.id, seconds, played)
        return True

    def mark_downloaded(self, episode_id: int, path: str | None) -> None:
        self.conn.execute("UPDATE episodes SET local_path=? WHERE id=?", (path, episode_id))
        self.conn.commit()
        self.logger.info("Episode marked downloaded: episode_id=%s path=%s", episode_id, path)

    def episode_id_for_local_path(self, path: str) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM episodes WHERE local_path=? ORDER BY id LIMIT 1",
            (path,),
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def set_played(self, episode_id: int, played: bool) -> None:
        self.conn.execute("UPDATE episodes SET played=? WHERE id=?", (int(played), episode_id))
        self.conn.commit()

    def next_sync_sequence(self) -> int:
        cur = self.conn.cursor()
        cur.execute("UPDATE sync_sequence SET value=value+1 WHERE id=1")
        row = cur.execute("SELECT value FROM sync_sequence WHERE id=1").fetchone()
        self.conn.commit()
        return int(row["value"])

    def current_sync_sequence(self) -> int:
        row = self.conn.execute("SELECT value FROM sync_sequence WHERE id=1").fetchone()
        return int(row["value"]) if row is not None else 0

    def upsert_sync_device(self, username: str, device_id: str, caption: str | None = None, device_type: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO sync_devices(username, device_id, caption, type, created_at, updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(username, device_id) DO UPDATE SET
                caption=excluded.caption,
                type=excluded.type,
                updated_at=excluded.updated_at
            """,
            (username, device_id, caption, device_type, now, now),
        )
        self.conn.commit()

    def list_sync_devices(self, username: str) -> list[SyncDevice]:
        rows = self.conn.execute(
            "SELECT username, device_id, caption, type FROM sync_devices WHERE username=? ORDER BY device_id",
            (username,),
        ).fetchall()
        return [SyncDevice(**dict(row)) for row in rows]

    def record_subscription_event(self, username: str, device_id: str | None, feed_url: str, action: str) -> int:
        if action not in {"add", "remove"}:
            raise ValueError(f"Unsupported subscription sync action: {action}")
        sequence = self.next_sync_sequence()
        self.conn.execute(
            """
            INSERT INTO sync_subscription_events(sequence, username, device_id, feed_url, action)
            VALUES(?,?,?,?,?)
            """,
            (sequence, username, device_id, feed_url, action),
        )
        self.conn.commit()
        return sequence

    def list_subscription_events_since(
        self,
        username: str,
        since: int = 0,
        exclude_device_id: str | None = None,
    ) -> list[SyncSubscriptionEvent]:
        params: list[object] = [username, since]
        exclude_clause = ""
        if exclude_device_id is not None:
            exclude_clause = " AND (device_id IS NULL OR device_id <> ?)"
            params.append(exclude_device_id)
        rows = self.conn.execute(
            f"""
            SELECT sequence, username, device_id, feed_url, action
            FROM sync_subscription_events
            WHERE username=? AND sequence>?{exclude_clause}
            ORDER BY sequence
            """,
            params,
        ).fetchall()
        return [SyncSubscriptionEvent(**dict(row)) for row in rows]

    def record_episode_action(
        self,
        username: str,
        device_id: str | None,
        podcast_url: str,
        episode_url: str,
        action: str,
        *,
        started: int | None = None,
        position: int | None = None,
        total: int | None = None,
    ) -> int:
        if action not in {"play", "new", "download", "delete"}:
            raise ValueError(f"Unsupported episode sync action: {action}")
        sequence = self.next_sync_sequence()
        self.conn.execute(
            """
            INSERT INTO sync_episode_actions(
                sequence, username, device_id, podcast_url, episode_url,
                action, started, position, total
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (sequence, username, device_id, podcast_url, episode_url, action, started, position, total),
        )
        self.conn.commit()
        return sequence

    def list_episode_actions_since(
        self,
        username: str,
        since: int = 0,
        exclude_device_id: str | None = None,
    ) -> list[SyncEpisodeAction]:
        params: list[object] = [username, since]
        exclude_clause = ""
        if exclude_device_id is not None:
            exclude_clause = " AND (device_id IS NULL OR device_id <> ?)"
            params.append(exclude_device_id)
        rows = self.conn.execute(
            f"""
            SELECT sequence, username, device_id, podcast_url, episode_url,
                   action, started, position, total
            FROM sync_episode_actions
            WHERE username=? AND sequence>?{exclude_clause}
            ORDER BY sequence
            """,
            params,
        ).fetchall()
        return [SyncEpisodeAction(**dict(row)) for row in rows]

    def enqueue(self, episode_id: int) -> None:
        self.conn.execute("DELETE FROM queue WHERE episode_id=?", (episode_id,))
        row = self.conn.execute("SELECT COALESCE(MAX(position), -1) AS max_position FROM queue").fetchone()
        max_pos = int(row["max_position"]) if row else -1
        self.conn.execute("INSERT INTO queue(episode_id, position) VALUES(?,?)", (episode_id, max_pos + 1))
        self.conn.commit()
        self._normalize_queue_positions()

    def list_queue(self) -> list[int]:
        rows = self.conn.execute("SELECT episode_id FROM queue ORDER BY position").fetchall()
        return [int(r["episode_id"]) for r in rows]

    def remove_from_queue(self, episode_id: int) -> None:
        self.conn.execute("DELETE FROM queue WHERE episode_id=?", (episode_id,))
        self.conn.commit()
        self._normalize_queue_positions()

    def clear_queue(self) -> None:
        self.conn.execute("DELETE FROM queue")
        self.conn.commit()

    def reorder_queue(self, episode_id: int, new_position: int) -> None:
        order = self.list_queue()
        if episode_id not in order:
            return
        order.remove(episode_id)
        clamped_position = max(0, min(new_position, len(order)))
        order.insert(clamped_position, episode_id)
        self.replace_queue_order(order)

    def replace_queue_order(self, episode_ids: list[int]) -> None:
        self.conn.execute("DELETE FROM queue")
        self.conn.executemany(
            "INSERT INTO queue(episode_id, position) VALUES(?,?)",
            [(episode_id, position) for position, episode_id in enumerate(episode_ids)],
        )
        self.conn.commit()

    def dequeue_next(self) -> int | None:
        row = self.conn.execute(
            "SELECT episode_id FROM queue ORDER BY position LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        episode_id = int(row["episode_id"])
        self.conn.execute("DELETE FROM queue WHERE episode_id=?", (episode_id,))
        self.conn.commit()
        self._normalize_queue_positions()
        return episode_id

    def _normalize_queue_positions(self) -> None:
        rows = self.conn.execute("SELECT episode_id FROM queue ORDER BY position").fetchall()
        self.conn.executemany(
            "UPDATE queue SET position=? WHERE episode_id=?",
            [(position, int(row["episode_id"])) for position, row in enumerate(rows)],
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
