"""
Database infrastructure for Twitter Intelligence Bot.

Provides SQLite-based persistence for tweet processing state.
"""

from twitter_intel.infrastructure.database.sqlite_repository import SqliteTweetRepository

__all__ = ["SqliteTweetRepository"]
