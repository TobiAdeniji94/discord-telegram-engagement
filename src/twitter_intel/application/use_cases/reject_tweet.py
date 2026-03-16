"""
Reject tweet use case.

Handles the skip button interaction by marking the tweet as rejected
and logging the action.
"""

import logging
from dataclasses import dataclass

from twitter_intel.domain.interfaces import NotificationService, TweetRepository

log = logging.getLogger(__name__)


@dataclass
class RejectionResult:
    """Result of a rejection attempt."""
    success: bool
    message: str


class RejectTweetUseCase:
    """
    Handle tweet rejection from Discord button interaction.

    Marks the tweet as rejected in the database and logs to the
    rejected channel.
    """

    def __init__(
        self,
        repository: TweetRepository,
        notification_service: NotificationService,
    ):
        self._repository = repository
        self._notification_service = notification_service

    async def execute(self, tweet_id: str) -> RejectionResult:
        """
        Execute the rejection for a given tweet.

        Args:
            tweet_id: The tweet ID to reject

        Returns:
            RejectionResult with success status and message
        """
        # Mark as rejected in database
        self._repository.mark_rejected(tweet_id)

        # Log to rejected channel
        tweet_info = self._repository.get_tweet_info(tweet_id)
        if tweet_info:
            await self._notification_service.log_rejected(
                tweet_id=tweet_id,
                tweet_url=tweet_info.get("url", ""),
                author=tweet_info.get("author", ""),
            )

        log.info(f"Rejected tweet {tweet_id}")
        return RejectionResult(
            success=True,
            message="Skipped.",
        )
