"""
Telegram notification service implementation.

Provides notification capabilities using Telegram's Bot API.
Used as a fallback/alert channel.
"""

import logging
from typing import Any, TYPE_CHECKING

import httpx

from twitter_intel.domain.interfaces.notification_service import NotificationService

if TYPE_CHECKING:
    from twitter_intel.config import Config
    from twitter_intel.domain.entities.tweet import TweetCandidate

log = logging.getLogger("twitter_intel.notifications.telegram")


class TelegramNotifier(NotificationService):
    """
    Telegram-based notification service.

    Used as a fallback/alert channel for important notifications.
    Does not support full approval workflows - use Discord for that.
    """

    def __init__(self, config: "Config"):
        """
        Initialize the Telegram notifier.

        Args:
            config: Application configuration
        """
        self._config = config
        self._base_url = f"https://api.telegram.org/bot{config.telegram_bot_token}"

    @property
    def name(self) -> str:
        """Get service name."""
        return "telegram"

    @property
    def enabled(self) -> bool:
        """Check if Telegram notifications are enabled."""
        return (
            self._config.telegram_enabled
            and bool(self._config.telegram_bot_token)
            and bool(self._config.telegram_chat_id)
        )

    async def _send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Send a message to the configured Telegram chat.

        Args:
            text: Message text (supports HTML formatting)
            parse_mode: Telegram parse mode (HTML or Markdown)

        Returns:
            True if message was sent successfully
        """
        if not self.enabled:
            return False

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._base_url}/sendMessage",
                    json={
                        "chat_id": self._config.telegram_chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                    },
                )
                return resp.status_code == 200
        except Exception as e:
            log.error("Telegram send failed: %s", e)
            return False

    async def send_approval_request(
        self,
        tweet: "TweetCandidate",
        analysis: dict[str, Any],
    ) -> tuple[str, str] | None:
        """
        Send an approval notification to Telegram.

        Note: Telegram doesn't support interactive buttons for approval.
        This just sends a notification that a tweet is pending review.
        """
        if not self.enabled:
            return None

        category = analysis.get("category", "unknown")
        sentiment = analysis.get("sentiment", "neutral")

        message = (
            f"🐦 <b>New Tweet for Review</b>\n\n"
            f"<b>Author:</b> @{tweet.author_username}\n"
            f"<b>Category:</b> {category}\n"
            f"<b>Sentiment:</b> {sentiment}\n"
            f"<b>Engagement:</b> {tweet.likes}❤️ {tweet.replies}💬\n\n"
            f"<i>{tweet.text[:500]}</i>\n\n"
            f"<a href=\"{tweet.url}\">View Tweet</a>"
        )

        success = await self._send_message(message)
        # Telegram doesn't return message IDs in the same way as Discord
        return ("telegram", "telegram") if success else None

    async def log_approved(
        self,
        tweet_id: str,
        tweet_url: str,
        reply_text: str,
        author: str,
    ) -> None:
        """Log that a tweet was approved and replied to."""
        if not self.enabled:
            return

        message = (
            f"✅ <b>Reply Posted</b>\n\n"
            f"<b>To:</b> @{author}\n"
            f"<b>Reply:</b> {reply_text}\n\n"
            f"<a href=\"{tweet_url}\">View Original</a>"
        )
        await self._send_message(message)

    async def log_rejected(
        self,
        tweet_id: str,
        tweet_url: str,
        author: str,
    ) -> None:
        """Log that a tweet was rejected/skipped."""
        # Don't spam Telegram with rejections
        pass

    async def send_status(self, message: str) -> None:
        """Send a status message to Telegram."""
        if not self.enabled:
            return

        await self._send_message(f"📊 <b>Bot Status</b>\n\n{message}")

    async def send_alert(self, message: str) -> None:
        """
        Send an alert message to Telegram.

        Use this for important notifications that require attention.
        """
        if not self.enabled:
            return

        await self._send_message(f"🚨 <b>Alert</b>\n\n{message}")


async def telegram_notify(config: "Config", message: str) -> None:
    """
    Send a notification to Telegram (standalone function).

    This is a convenience function for sending simple notifications
    without creating a full TelegramNotifier instance.

    Args:
        config: Application configuration
        message: Message to send
    """
    if not config.telegram_enabled or not config.telegram_bot_token:
        return

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": config.telegram_chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                },
            )
    except Exception as e:
        log.error("Telegram notify failed: %s", e)
