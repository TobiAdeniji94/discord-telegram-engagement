"""
Approve tweet use case.

Handles the approval button interaction by posting the selected reply
to X/Twitter and logging the action.
"""

import logging
from dataclasses import dataclass

from twitter_intel.domain.interfaces import NotificationService, TweetRepository
from twitter_intel.infrastructure.twitter.x_poster import XPoster

log = logging.getLogger(__name__)


@dataclass
class ApprovalResult:
    """Result of an approval attempt."""
    success: bool
    message: str
    reply_text: str | None = None


class ApproveTweetUseCase:
    """
    Handle tweet approval from Discord button interaction.

    Retrieves the pending approval, posts the reply to X (unless test/dry-run),
    updates the database, and logs to the approved channel.
    """

    def __init__(
        self,
        repository: TweetRepository,
        x_poster: XPoster,
        notification_service: NotificationService,
    ):
        self._repository = repository
        self._x_poster = x_poster
        self._notification_service = notification_service

    async def execute(self, tweet_id: str, reply_idx: int) -> ApprovalResult:
        """
        Execute the approval for a given tweet and reply index.

        Args:
            tweet_id: The tweet ID to approve
            reply_idx: Index of the selected reply option

        Returns:
            ApprovalResult with success status and message
        """
        # Get pending approval from database
        pending = self._repository.get_pending(tweet_id)
        if not pending or pending[0] is None:
            log.warning(f"No pending approval found for tweet {tweet_id}")
            return ApprovalResult(
                success=False,
                message="No pending approval found for this tweet.",
            )

        replies, msg_id, ch_id, category = pending

        # Validate reply index
        if not replies or not (0 <= reply_idx < len(replies)):
            log.warning(f"Invalid reply index {reply_idx} for tweet {tweet_id}")
            return ApprovalResult(
                success=False,
                message=f"Invalid reply index {reply_idx}.",
            )

        reply_text = replies[reply_idx]

        # Post the reply to X
        success = await self._x_poster.post_reply(tweet_id, reply_text)

        if success:
            # Mark as replied in database
            self._repository.mark_replied(tweet_id, reply_text)

            # Log to approved channel
            tweet_info = self._repository.get_tweet_info(tweet_id)
            if tweet_info:
                await self._notification_service.log_approved(
                    tweet_id=tweet_id,
                    tweet_url=tweet_info.get("url", ""),
                    reply_text=reply_text,
                    author=tweet_info.get("author", ""),
                )

            log.info(f"Approved tweet {tweet_id} with reply: {reply_text[:50]}...")
            return ApprovalResult(
                success=True,
                message="Reply posted successfully!",
                reply_text=reply_text,
            )
        else:
            log.error(f"Failed to post reply for tweet {tweet_id}")
            return ApprovalResult(
                success=False,
                message="Failed to post reply. Check X credentials.",
            )

    async def execute_custom_reply(
        self, tweet_id: str, reply_text: str
    ) -> ApprovalResult:
        """
        Execute a custom reply for a tweet.

        Args:
            tweet_id: The tweet ID to reply to
            reply_text: The custom reply text

        Returns:
            ApprovalResult with success status and message
        """
        # Validate reply length
        if len(reply_text) > 280:
            return ApprovalResult(
                success=False,
                message=f"{len(reply_text)} chars - must be <= 280",
            )

        pending = self._repository.get_pending(tweet_id)
        if not pending or pending[0] is None:
            log.warning(f"Custom reply denied: no pending approval for tweet {tweet_id}")
            return ApprovalResult(
                success=False,
                message="No pending approval found for this tweet.",
            )

        # Post the reply to X
        success = await self._x_poster.post_reply(tweet_id, reply_text)

        if success:
            # Mark as replied in database
            self._repository.mark_replied(tweet_id, reply_text)

            # Log to approved channel
            tweet_info = self._repository.get_tweet_info(tweet_id)
            if tweet_info:
                await self._notification_service.log_approved(
                    tweet_id=tweet_id,
                    tweet_url=tweet_info.get("url", ""),
                    reply_text=reply_text,
                    author=tweet_info.get("author", ""),
                )

            log.info(f"Custom reply posted for tweet {tweet_id}")
            return ApprovalResult(
                success=True,
                message="Custom reply posted!",
                reply_text=reply_text,
            )
        else:
            log.error(f"Failed to post custom reply for tweet {tweet_id}")
            return ApprovalResult(
                success=False,
                message="Failed to post. Check X credentials.",
            )
