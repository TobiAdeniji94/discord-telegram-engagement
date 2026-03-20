"""
Tweet repository interface.

Defines the abstract contract for tweet persistence operations.
"""

from abc import ABC, abstractmethod
from typing import Any


class TweetRepository(ABC):
    """
    Abstract interface for tweet persistence operations.

    This interface defines the contract that any tweet storage implementation
    must fulfill. Implementations may use SQLite, PostgreSQL, or any other
    storage backend.
    """

    @abstractmethod
    def is_processed(self, tweet_id: str) -> bool:
        """
        Check if a tweet has already been processed.

        Args:
            tweet_id: The unique tweet identifier

        Returns:
            True if the tweet has been processed, False otherwise
        """
        pass

    @abstractmethod
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
        """
        Mark a tweet as processed (initial state: pending).

        Args:
            tweet_id: The unique tweet identifier
            url: URL to the tweet
            text: Tweet text content
            author: Author's username
            category: Classified category
            sentiment: Detected sentiment
            search_query: The query that found this tweet
        """
        pass

    @abstractmethod
    def save_pending_approval(
        self,
        tweet_id: str,
        reply_options: list[str],
        discord_message_id: str,
        discord_channel_id: str,
        category: str,
    ) -> None:
        """
        Save a tweet pending human approval.

        Args:
            tweet_id: The unique tweet identifier
            reply_options: List of suggested reply texts
            discord_message_id: Discord message ID for the approval embed
            discord_channel_id: Discord channel ID where embed was posted
            category: Tweet category
        """
        pass

    @abstractmethod
    def get_pending_approval(
        self, tweet_id: str
    ) -> tuple[list[str] | None, str | None, str | None, str | None]:
        """
        Get pending approval details for a tweet.

        Args:
            tweet_id: The unique tweet identifier

        Returns:
            Tuple of (reply_options, discord_message_id, discord_channel_id, category)
            All values are None if no pending approval exists
        """
        pass

    @abstractmethod
    def mark_replied(self, tweet_id: str, reply_text: str) -> None:
        """
        Mark a tweet as replied and remove from pending.

        Args:
            tweet_id: The unique tweet identifier
            reply_text: The reply that was posted
        """
        pass

    @abstractmethod
    def mark_rejected(self, tweet_id: str) -> None:
        """
        Mark a tweet as rejected and remove from pending.

        Args:
            tweet_id: The unique tweet identifier
        """
        pass

    @abstractmethod
    def get_stats(self) -> dict[str, Any]:
        """
        Get statistics about processed tweets.

        Returns:
            Dictionary with keys:
            - total_processed: Total tweets processed
            - replied: Count of replied tweets
            - rejected: Count of rejected tweets
            - pending: Count of pending tweets
            - by_category: Dict mapping category to count
        """
        pass

    @abstractmethod
    def get_processed_ids(self) -> set[str]:
        """
        Get all processed tweet IDs.

        Returns:
            Set of tweet IDs that have been processed
        """
        pass

    @abstractmethod
    def get_tweet_info(self, tweet_id: str) -> dict[str, Any] | None:
        """
        Get basic info about a processed tweet.

        Args:
            tweet_id: The unique tweet identifier

        Returns:
            Dict with url and author keys, or None if not found
        """
        pass

    @abstractmethod
    def set_runtime_value(self, key: str, value: str) -> None:
        """
        Persist a runtime key/value pair.

        Args:
            key: Runtime state key
            value: Runtime state value
        """
        pass

    @abstractmethod
    def get_runtime_value(self, key: str) -> str | None:
        """
        Retrieve a runtime key/value pair.

        Args:
            key: Runtime state key

        Returns:
            Stored value or None if missing
        """
        pass

    # Aliases for use case compatibility
    def save_pending(
        self,
        tweet_id: str,
        replies: list[str],
        message_id: str,
        channel_id: str,
        category: str,
    ) -> None:
        """Alias for save_pending_approval."""
        return self.save_pending_approval(
            tweet_id, replies, message_id, channel_id, category
        )

    def get_pending(
        self, tweet_id: str
    ) -> tuple[list[str] | None, str | None, str | None, str | None]:
        """Alias for get_pending_approval."""
        return self.get_pending_approval(tweet_id)
