"""
SQLite implementation of the TweetRepository.

Provides persistent storage for tweet processing state using SQLite.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from twitter_intel.domain.interfaces.tweet_repository import TweetRepository


class SqliteTweetRepository(TweetRepository):
    """
    SQLite-backed implementation of TweetRepository.

    This implementation stores tweet data in a local SQLite database file.
    It manages three tables:
    - processed_tweets: All tweets that have been seen
    - pending_approvals: Tweets awaiting human review
    - bot_stats: Key-value store for bot statistics
    """

    def __init__(self, db_path: str):
        """
        Initialize the SQLite repository.

        Args:
            db_path: Path to the SQLite database file
        """
        self._db_path = db_path
        self._conn = self._init_db()

    def _init_db(self) -> sqlite3.Connection:
        """
        Initialize the database schema.

        Creates tables if they don't exist and returns a connection.
        """
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_tweets (
                tweet_id TEXT PRIMARY KEY,
                tweet_url TEXT,
                tweet_text TEXT,
                author TEXT,
                category TEXT,
                sentiment TEXT,
                status TEXT DEFAULT 'pending',
                approved_reply TEXT,
                search_query TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                replied_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_approvals (
                tweet_id TEXT PRIMARY KEY,
                reply_options TEXT,
                discord_message_id TEXT,
                discord_channel_id TEXT,
                category TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_stats (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        conn.commit()
        return conn

    @property
    def connection(self) -> sqlite3.Connection:
        """Get the underlying database connection."""
        return self._conn

    def is_processed(self, tweet_id: str) -> bool:
        """Check if a tweet has already been processed."""
        row = self._conn.execute(
            "SELECT 1 FROM processed_tweets WHERE tweet_id = ?",
            (tweet_id,)
        ).fetchone()
        return row is not None

    def mark_processed(
        self,
        tweet_id: str,
        url: str,
        text: str,
        author: str,
        category: str,
        sentiment: str,
        search_query: str,
    ) -> None:
        """Mark a tweet as processed (initial state: pending)."""
        self._conn.execute(
            """INSERT OR IGNORE INTO processed_tweets
               (tweet_id, tweet_url, tweet_text, author, category, sentiment, search_query)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tweet_id, url, text, author, category, sentiment, search_query),
        )
        self._conn.commit()

    def save_pending_approval(
        self,
        tweet_id: str,
        reply_options: list[str],
        discord_message_id: str,
        discord_channel_id: str,
        category: str,
    ) -> None:
        """Save a tweet pending human approval."""
        self._conn.execute(
            """INSERT OR REPLACE INTO pending_approvals
               (tweet_id, reply_options, discord_message_id, discord_channel_id, category)
               VALUES (?, ?, ?, ?, ?)""",
            (tweet_id, json.dumps(reply_options), discord_message_id, discord_channel_id, category),
        )
        self._conn.commit()

    def get_pending_approval(
        self, tweet_id: str
    ) -> tuple[list[str] | None, str | None, str | None, str | None]:
        """Get pending approval details for a tweet."""
        row = self._conn.execute(
            """SELECT reply_options, discord_message_id, discord_channel_id, category
               FROM pending_approvals WHERE tweet_id = ?""",
            (tweet_id,),
        ).fetchone()

        if row:
            return json.loads(row[0]), row[1], row[2], row[3]
        return None, None, None, None

    def mark_replied(self, tweet_id: str, reply_text: str) -> None:
        """Mark a tweet as replied and remove from pending."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE processed_tweets SET status='replied', approved_reply=?, replied_at=? WHERE tweet_id=?",
            (reply_text, now, tweet_id),
        )
        self._conn.execute(
            "DELETE FROM pending_approvals WHERE tweet_id=?",
            (tweet_id,)
        )
        self._conn.commit()

    def mark_rejected(self, tweet_id: str) -> None:
        """Mark a tweet as rejected and remove from pending."""
        self._conn.execute(
            "UPDATE processed_tweets SET status='rejected' WHERE tweet_id=?",
            (tweet_id,),
        )
        self._conn.execute(
            "DELETE FROM pending_approvals WHERE tweet_id=?",
            (tweet_id,)
        )
        self._conn.commit()

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about processed tweets."""
        rows = self._conn.execute("SELECT * FROM processed_tweets").fetchall()
        total = len(rows)
        replied = sum(1 for r in rows if r[6] == "replied")
        rejected = sum(1 for r in rows if r[6] == "rejected")
        pending = sum(1 for r in rows if r[6] == "pending")

        by_category: dict[str, int] = {}
        for r in rows:
            cat = r[4] or "unknown"
            by_category[cat] = by_category.get(cat, 0) + 1

        return {
            "total_processed": total,
            "replied": replied,
            "rejected": rejected,
            "pending": pending,
            "by_category": by_category,
        }

    def get_processed_ids(self) -> set[str]:
        """Get all processed tweet IDs."""
        rows = self._conn.execute(
            "SELECT tweet_id FROM processed_tweets"
        ).fetchall()
        return {row[0] for row in rows}

    def get_tweet_info(self, tweet_id: str) -> dict[str, Any] | None:
        """Get basic info about a processed tweet."""
        row = self._conn.execute(
            "SELECT tweet_url, author FROM processed_tweets WHERE tweet_id=?",
            (tweet_id,)
        ).fetchone()

        if row:
            return {"url": row[0], "author": row[1]}
        return None

    def set_runtime_value(self, key: str, value: str) -> None:
        """Persist a runtime key/value pair in bot_stats."""
        self._conn.execute(
            """INSERT OR REPLACE INTO bot_stats (key, value)
               VALUES (?, ?)""",
            (key, value),
        )
        self._conn.commit()

    def get_runtime_value(self, key: str) -> str | None:
        """Retrieve a runtime key/value pair from bot_stats."""
        row = self._conn.execute(
            "SELECT value FROM bot_stats WHERE key=?",
            (key,),
        ).fetchone()
        if row:
            return row[0]
        return None

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
