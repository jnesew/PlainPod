from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Iterable
from email.utils import parsedate_to_datetime # Add this import

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


class Repository:
    def __init__(self, db_file: Path):
        self.logger = logging.getLogger(__name__)
        self.db_file = db_file
        self.conn = sqlite3.connect(db_file)
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
                UNIQUE(podcast_id, guid)
            );

            CREATE TABLE IF NOT EXISTS queue (
                episode_id INTEGER PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
                position INTEGER NOT NULL
            );
            """
        )
        self.conn.commit()

    def add_podcast(self, *, title: str, feed_url: str, site_url: str | None, description: str | None, artwork_url: str | None) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO podcasts(title, feed_url, site_url, description, artwork_url)
            VALUES(?,?,?,?,?)
            """,
            (title, feed_url, site_url, description, artwork_url),
        )
        self.conn.commit()
        row = cur.execute("SELECT id FROM podcasts WHERE feed_url=?", (feed_url,)).fetchone()
        assert row is not None
        self.logger.info("Podcast upserted: id=%s title=%s", row["id"], title)
        return int(row["id"])

    def list_podcasts(self) -> list[Podcast]:
        rows = self.conn.execute("SELECT * FROM podcasts ORDER BY title COLLATE NOCASE").fetchall()
        return [Podcast(**dict(r)) for r in rows]

    def remove_podcast(self, podcast_id: int) -> None:
        self.conn.execute("DELETE FROM podcasts WHERE id=?", (podcast_id,))
        self.conn.commit()

    def upsert_episodes(self, podcast_id: int, episodes: Iterable[dict]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        count = 0
        for ep in episodes:
            guid = ep.get("guid") or ep["media_url"]
            cur.execute(
                """
                INSERT INTO episodes(
                    podcast_id, guid, title, published_at, duration_seconds,
                    description, media_url
                ) VALUES(?,?,?,?,?,?,?)
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
                    ep["media_url"],
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

    def list_downloaded_episodes(self) -> list[Episode]:
        rows = self.conn.execute(
            """
            SELECT * FROM episodes
            WHERE local_path IS NOT NULL AND TRIM(local_path) <> ''
            ORDER BY datetime(published_at) DESC, id DESC
            """
        ).fetchall()
        return [Episode(**dict(r)) for r in rows]

    def update_episode_progress(self, episode_id: int, seconds: int, played: bool = False) -> None:
        self.conn.execute(
            "UPDATE episodes SET progress_seconds=?, played=? WHERE id=?",
            (seconds, int(played), episode_id),
        )
        self.conn.commit()

    def mark_downloaded(self, episode_id: int, path: str | None) -> None:
        self.conn.execute("UPDATE episodes SET local_path=? WHERE id=?", (path, episode_id))
        self.conn.commit()
        self.logger.info("Episode marked downloaded: episode_id=%s path=%s", episode_id, path)

    def set_played(self, episode_id: int, played: bool) -> None:
        self.conn.execute("UPDATE episodes SET played=? WHERE id=?", (int(played), episode_id))
        self.conn.commit()

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
