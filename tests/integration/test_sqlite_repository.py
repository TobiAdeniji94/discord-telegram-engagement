"""
Integration tests for SqliteTweetRepository.

These tests use actual SQLite databases (in temp directories).
"""

import pytest
from pathlib import Path
from twitter_intel.infrastructure.database import SqliteTweetRepository
from twitter_intel.domain.interfaces import TweetRepository


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Create a temporary database path."""
    return str(tmp_path / "test.db")


@pytest.fixture
def repository(db_path: str) -> SqliteTweetRepository:
    """Create a repository instance."""
    repo = SqliteTweetRepository(db_path)
    yield repo
    repo.close()


class TestSqliteTweetRepository:
    """Tests for SqliteTweetRepository."""

    def test_implements_interface(self, repository: SqliteTweetRepository):
        """Repository should implement TweetRepository interface."""
        assert isinstance(repository, TweetRepository)

    def test_creates_database_file(self, db_path: str):
        """Repository should create database file on init."""
        assert not Path(db_path).exists()
        repo = SqliteTweetRepository(db_path)
        assert Path(db_path).exists()
        repo.close()

    def test_creates_parent_directories(self, tmp_path: Path):
        """Repository should create parent directories if needed."""
        nested_path = tmp_path / "a" / "b" / "c" / "test.db"
        assert not nested_path.parent.exists()
        repo = SqliteTweetRepository(str(nested_path))
        assert nested_path.parent.exists()
        repo.close()


class TestIsProcessed:
    """Tests for is_processed method."""

    def test_returns_false_for_unknown_tweet(self, repository: SqliteTweetRepository):
        """is_processed should return False for unknown tweet."""
        assert repository.is_processed("unknown_id") is False

    def test_returns_true_for_processed_tweet(self, repository: SqliteTweetRepository):
        """is_processed should return True after marking processed."""
        repository.mark_processed(
            tweet_id="12345",
            url="https://x.com/user/status/12345",
            text="Test tweet",
            author="test_user",
            category="brand-mentions",
            sentiment="positive",
            search_query="test query",
        )
        assert repository.is_processed("12345") is True

    def test_returns_false_for_different_tweet(self, repository: SqliteTweetRepository):
        """is_processed should return False for different tweet ID."""
        repository.mark_processed(
            tweet_id="12345",
            url="https://x.com/user/status/12345",
            text="Test tweet",
            author="test_user",
            category="brand-mentions",
            sentiment="positive",
            search_query="test query",
        )
        assert repository.is_processed("99999") is False


class TestMarkProcessed:
    """Tests for mark_processed method."""

    def test_stores_tweet_data(self, repository: SqliteTweetRepository):
        """mark_processed should store tweet data in database."""
        repository.mark_processed(
            tweet_id="12345",
            url="https://x.com/user/status/12345",
            text="Hello world",
            author="testuser",
            category="solution-seekers",
            sentiment="neutral",
            search_query="hello",
        )

        # Verify via raw SQL
        row = repository.connection.execute(
            "SELECT * FROM processed_tweets WHERE tweet_id = ?",
            ("12345",)
        ).fetchone()

        assert row is not None
        assert row[0] == "12345"  # tweet_id
        assert row[1] == "https://x.com/user/status/12345"  # tweet_url
        assert row[2] == "Hello world"  # tweet_text
        assert row[3] == "testuser"  # author
        assert row[4] == "solution-seekers"  # category
        assert row[5] == "neutral"  # sentiment
        assert row[6] == "pending"  # status (default)

    def test_ignores_duplicate_inserts(self, repository: SqliteTweetRepository):
        """mark_processed should not error on duplicate tweet_id."""
        repository.mark_processed(
            tweet_id="12345",
            url="url1",
            text="text1",
            author="author1",
            category="cat1",
            sentiment="sent1",
            search_query="query1",
        )
        # Should not raise
        repository.mark_processed(
            tweet_id="12345",
            url="url2",  # Different data
            text="text2",
            author="author2",
            category="cat2",
            sentiment="sent2",
            search_query="query2",
        )

        # Original data should be preserved
        row = repository.connection.execute(
            "SELECT tweet_url FROM processed_tweets WHERE tweet_id = ?",
            ("12345",)
        ).fetchone()
        assert row[0] == "url1"


class TestPendingApprovals:
    """Tests for pending approval methods."""

    def test_save_and_get_pending(self, repository: SqliteTweetRepository):
        """save_pending_approval should store and get_pending_approval should retrieve."""
        repository.save_pending_approval(
            tweet_id="12345",
            reply_options=["Reply 1", "Reply 2", "Reply 3"],
            discord_message_id="msg_123",
            discord_channel_id="ch_456",
            category="brand-mentions",
        )

        options, msg_id, ch_id, category = repository.get_pending_approval("12345")

        assert options == ["Reply 1", "Reply 2", "Reply 3"]
        assert msg_id == "msg_123"
        assert ch_id == "ch_456"
        assert category == "brand-mentions"

    def test_get_pending_returns_none_for_unknown(self, repository: SqliteTweetRepository):
        """get_pending_approval should return None values for unknown tweet."""
        options, msg_id, ch_id, category = repository.get_pending_approval("unknown")

        assert options is None
        assert msg_id is None
        assert ch_id is None
        assert category is None

    def test_save_pending_replaces_existing(self, repository: SqliteTweetRepository):
        """save_pending_approval should replace existing entry."""
        repository.save_pending_approval(
            tweet_id="12345",
            reply_options=["Old reply"],
            discord_message_id="old_msg",
            discord_channel_id="old_ch",
            category="old_cat",
        )
        repository.save_pending_approval(
            tweet_id="12345",
            reply_options=["New reply"],
            discord_message_id="new_msg",
            discord_channel_id="new_ch",
            category="new_cat",
        )

        options, msg_id, ch_id, category = repository.get_pending_approval("12345")

        assert options == ["New reply"]
        assert msg_id == "new_msg"


class TestMarkReplied:
    """Tests for mark_replied method."""

    def test_updates_status_and_removes_pending(self, repository: SqliteTweetRepository):
        """mark_replied should update status and remove from pending."""
        # Setup: create processed tweet and pending approval
        repository.mark_processed(
            tweet_id="12345",
            url="url",
            text="text",
            author="author",
            category="cat",
            sentiment="sent",
            search_query="query",
        )
        repository.save_pending_approval(
            tweet_id="12345",
            reply_options=["Reply"],
            discord_message_id="msg",
            discord_channel_id="ch",
            category="cat",
        )

        # Act
        repository.mark_replied("12345", "My actual reply")

        # Assert: status updated
        row = repository.connection.execute(
            "SELECT status, approved_reply, replied_at FROM processed_tweets WHERE tweet_id = ?",
            ("12345",)
        ).fetchone()
        assert row[0] == "replied"
        assert row[1] == "My actual reply"
        assert row[2] is not None  # replied_at timestamp

        # Assert: pending removed
        options, _, _, _ = repository.get_pending_approval("12345")
        assert options is None


class TestMarkRejected:
    """Tests for mark_rejected method."""

    def test_updates_status_and_removes_pending(self, repository: SqliteTweetRepository):
        """mark_rejected should update status and remove from pending."""
        # Setup
        repository.mark_processed(
            tweet_id="12345",
            url="url",
            text="text",
            author="author",
            category="cat",
            sentiment="sent",
            search_query="query",
        )
        repository.save_pending_approval(
            tweet_id="12345",
            reply_options=["Reply"],
            discord_message_id="msg",
            discord_channel_id="ch",
            category="cat",
        )

        # Act
        repository.mark_rejected("12345")

        # Assert: status updated
        row = repository.connection.execute(
            "SELECT status FROM processed_tweets WHERE tweet_id = ?",
            ("12345",)
        ).fetchone()
        assert row[0] == "rejected"

        # Assert: pending removed
        options, _, _, _ = repository.get_pending_approval("12345")
        assert options is None


class TestGetStats:
    """Tests for get_stats method."""

    def test_empty_database(self, repository: SqliteTweetRepository):
        """get_stats should return zeros for empty database."""
        stats = repository.get_stats()

        assert stats["total_processed"] == 0
        assert stats["replied"] == 0
        assert stats["rejected"] == 0
        assert stats["pending"] == 0
        assert stats["by_category"] == {}

    def test_counts_by_status(self, repository: SqliteTweetRepository):
        """get_stats should count tweets by status."""
        # Create some tweets
        for i in range(5):
            repository.mark_processed(
                tweet_id=f"tweet_{i}",
                url=f"url_{i}",
                text=f"text_{i}",
                author="author",
                category="brand-mentions",
                sentiment="neutral",
                search_query="query",
            )

        # Mark some as replied/rejected
        repository.mark_replied("tweet_0", "reply")
        repository.mark_replied("tweet_1", "reply")
        repository.mark_rejected("tweet_2")

        stats = repository.get_stats()

        assert stats["total_processed"] == 5
        assert stats["replied"] == 2
        assert stats["rejected"] == 1
        assert stats["pending"] == 2

    def test_counts_by_category(self, repository: SqliteTweetRepository):
        """get_stats should count tweets by category."""
        categories = ["brand-mentions", "brand-mentions", "solution-seekers", "competitor-complaints"]
        for i, cat in enumerate(categories):
            repository.mark_processed(
                tweet_id=f"tweet_{i}",
                url=f"url_{i}",
                text=f"text_{i}",
                author="author",
                category=cat,
                sentiment="neutral",
                search_query="query",
            )

        stats = repository.get_stats()

        assert stats["by_category"]["brand-mentions"] == 2
        assert stats["by_category"]["solution-seekers"] == 1
        assert stats["by_category"]["competitor-complaints"] == 1
