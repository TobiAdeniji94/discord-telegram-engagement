"""
Notification service interface.

Defines the abstract contract for notification operations.
"""

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from twitter_intel.domain.entities.tweet import TweetCandidate


class NotificationService(ABC):
    """
    Abstract interface for notification services.

    This interface defines the contract for sending notifications
    to users for approval, logging, and status updates.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Get the service name for logging and identification.

        Returns:
            Service name (e.g., "discord", "telegram")
        """
        pass

    @abstractmethod
    async def send_approval_request(
        self,
        tweet: "TweetCandidate",
        analysis: dict[str, Any],
    ) -> tuple[str, str] | None:
        """
        Send an approval request for a tweet.

        Args:
            tweet: The tweet candidate requiring approval
            analysis: AI classification results with reply options

        Returns:
            Tuple of (message_id, channel_id) if successful, None otherwise
        """
        pass

    @abstractmethod
    async def log_approved(
        self,
        tweet_id: str,
        tweet_url: str,
        reply_text: str,
        author: str,
    ) -> None:
        """
        Log that a tweet was approved and replied to.

        Args:
            tweet_id: The tweet ID
            tweet_url: URL to the original tweet
            reply_text: The reply that was posted
            author: The tweet author's username
        """
        pass

    @abstractmethod
    async def log_rejected(
        self,
        tweet_id: str,
        tweet_url: str,
        author: str,
    ) -> None:
        """
        Log that a tweet was rejected/skipped.

        Args:
            tweet_id: The tweet ID
            tweet_url: URL to the original tweet
            author: The tweet author's username
        """
        pass

    @abstractmethod
    async def send_status(self, message: str) -> None:
        """
        Send a status message.

        Args:
            message: Status message to send
        """
        pass

    # Alias for use case compatibility
    async def send_approval(
        self,
        tweet: "TweetCandidate",
        analysis: dict[str, Any],
    ) -> tuple[str, str] | None:
        """Alias for send_approval_request."""
        return await self.send_approval_request(tweet, analysis)
