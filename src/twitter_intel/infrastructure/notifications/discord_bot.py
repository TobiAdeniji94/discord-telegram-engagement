"""
Discord notification service implementation.

Provides notification capabilities using Discord's API.
"""

import logging
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

import httpx

from twitter_intel.domain.entities.category import TweetCategory
from twitter_intel.domain.interfaces.notification_service import NotificationService

if TYPE_CHECKING:
    from twitter_intel.config import Config
    from twitter_intel.domain.entities.tweet import TweetCandidate
    from twitter_intel.domain.interfaces.tweet_repository import TweetRepository

log = logging.getLogger("twitter_intel.notifications.discord")


class DiscordBot(NotificationService):
    """
    Discord-based notification service.

    Manages Discord interactions:
    - Sends approval requests to categorized channels
    - Posts logs to #approved-log and #rejected-log
    - Sends status updates to #bot-status
    """

    def __init__(
        self,
        config: "Config",
        repository: "TweetRepository | None" = None,
    ):
        """
        Initialize the Discord bot.

        Args:
            config: Application configuration
            repository: Optional tweet repository for stats
        """
        self._config = config
        self._repository = repository
        self._base_url = "https://discord.com/api/v10"
        self._headers = {
            "Authorization": f"Bot {config.discord_bot_token}",
            "Content-Type": "application/json",
        }

    @property
    def name(self) -> str:
        """Get service name."""
        return "discord"

    def _get_channel_for_category(self, category: str) -> str:
        """Get the Discord channel ID for a category."""
        mapping = {
            TweetCategory.COMPETITOR_COMPLAINT.value: self._config.discord_channel_competitor,
            TweetCategory.SOLUTION_SEEKER.value: self._config.discord_channel_seekers,
            TweetCategory.BRAND_MENTION.value: self._config.discord_channel_brand,
            # Also handle enum values directly
            TweetCategory.COMPETITOR_COMPLAINT: self._config.discord_channel_competitor,
            TweetCategory.SOLUTION_SEEKER: self._config.discord_channel_seekers,
            TweetCategory.BRAND_MENTION: self._config.discord_channel_brand,
        }
        return mapping.get(category, self._config.discord_channel_brand)

    async def send_approval_request(
        self,
        tweet: "TweetCandidate",
        analysis: dict[str, Any],
    ) -> tuple[str, str] | None:
        """Send approval embed to the appropriate Discord channel."""
        category = analysis.get("category", TweetCategory.BRAND_MENTION.value)
        channel_id = self._get_channel_for_category(category)

        if not channel_id:
            log.error("No Discord channel configured for category: %s", category)
            return None

        # Build and send the embed
        embed = self._build_approval_embed(tweet, analysis)
        components = self._build_approval_components(tweet, analysis)

        payload = {
            "embeds": [embed],
            "components": components,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/channels/{channel_id}/messages",
                headers=self._headers,
                json=payload,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["id"], channel_id
            else:
                log.error("Discord send failed: %s %s", resp.status_code, resp.text[:300])
                return None

    def _build_approval_embed(
        self,
        tweet: "TweetCandidate",
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the Discord embed for an approval request."""
        # Sentiment colors
        color_map = {
            "positive": 0x2ECC71,   # green
            "negative": 0xE74C3C,   # red
            "neutral": 0x95A5A6,    # gray
            "mixed": 0xF39C12,      # yellow
        }
        color = color_map.get(analysis.get("sentiment", "neutral"), 0x95A5A6)

        urgency_emoji = {"low": "🔵", "medium": "🟠", "high": "🔴"}.get(
            analysis.get("urgency", "low"), "🔵"
        )

        embed: dict[str, Any] = {
            "title": f"🐦 Tweet from @{tweet.author_username}",
            "url": tweet.url,
            "description": tweet.text[:2000],
            "color": color,
            "fields": [
                {
                    "name": "📊 Engagement",
                    "value": f"{tweet.likes}❤️  {tweet.replies}💬  {tweet.retweets}🔁  {tweet.views:,}👁️",
                    "inline": True,
                },
                {
                    "name": "⏰ Age",
                    "value": f"{tweet.age_minutes:.0f} min ({tweet.source_tab})",
                    "inline": True,
                },
                {
                    "name": "🎯 Sentiment",
                    "value": f"{analysis.get('sentiment', 'neutral')} ({analysis.get('confidence', 0):.0%})",
                    "inline": True,
                },
                {
                    "name": f"{urgency_emoji} Urgency",
                    "value": analysis.get("urgency", "low"),
                    "inline": True,
                },
                {
                    "name": "🏷️ Themes",
                    "value": ", ".join(analysis.get("themes", ["—"])),
                    "inline": True,
                },
                {
                    "name": "🎯 Yara Angle",
                    "value": (analysis.get("yara_angle") or "—")[:200],
                    "inline": False,
                },
            ],
            "footer": {
                "text": f"Query: {tweet.search_query} | ID: {tweet.tweet_id}",
            },
            "timestamp": tweet.created_at.isoformat(),
        }

        # Add competitor field if mentioned
        if analysis.get("competitor_mentioned"):
            embed["fields"].insert(3, {
                "name": "⚔️ Competitor",
                "value": analysis["competitor_mentioned"],
                "inline": True,
            })

        # Add reply option fields
        replies = analysis.get("replies", [])
        for i, r in enumerate(replies):
            strategy = r.get("strategy", "")
            strategy_note = f"\n*{strategy}*" if strategy else ""
            embed["fields"].append({
                "name": f"💬 {i+1}. [{r.get('tone', 'reply')}]",
                "value": f"{r.get('text', '')}{strategy_note}",
                "inline": False,
            })

        return embed

    def _build_approval_components(
        self,
        tweet: "TweetCandidate",
        analysis: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build the Discord button components for an approval request."""
        replies = analysis.get("replies", [])

        # Reply buttons (up to 2 rows of 4)
        row1: list[dict[str, Any]] = []
        row2: list[dict[str, Any]] = []

        for i, r in enumerate(replies):
            btn = {
                "type": 2,  # button
                "style": 1 if i == 0 else 2,  # primary for first, secondary for rest
                "label": f"{i+1}. {r.get('tone', 'reply')[:20]}",
                "custom_id": f"approve:{tweet.tweet_id}:{i}",
            }
            if len(row1) < 4:
                row1.append(btn)
            else:
                row2.append(btn)

        action_rows = []
        if row1:
            action_rows.append({"type": 1, "components": row1})
        if row2:
            action_rows.append({"type": 1, "components": row2})

        # Control buttons (skip and custom)
        action_rows.append({
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 4,  # danger
                    "label": "❌ Skip",
                    "custom_id": f"reject:{tweet.tweet_id}",
                },
                {
                    "type": 2,
                    "style": 2,  # secondary
                    "label": "✏️ Custom Reply",
                    "custom_id": f"custom:{tweet.tweet_id}",
                },
            ],
        })

        return action_rows

    async def _send_to_channel(
        self,
        channel_id: str,
        content: str = "",
        embed: dict[str, Any] | None = None,
    ) -> None:
        """Send a message to a Discord channel."""
        if not channel_id:
            return

        payload: dict[str, Any] = {"content": content}
        if embed:
            payload["embeds"] = [embed]

        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self._base_url}/channels/{channel_id}/messages",
                headers=self._headers,
                json=payload,
            )

    async def log_approved(
        self,
        tweet_id: str,
        tweet_url: str,
        reply_text: str,
        author: str,
    ) -> None:
        """Log that a tweet was approved and replied to."""
        if not self._config.discord_channel_approved_log:
            return

        embed = {
            "title": "✅ Reply Posted",
            "color": 0x2ECC71,
            "fields": [
                {"name": "Tweet", "value": f"[@{author}]({tweet_url})", "inline": True},
                {"name": "Reply", "value": reply_text, "inline": False},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._send_to_channel(self._config.discord_channel_approved_log, "", embed)

    async def log_rejected(
        self,
        tweet_id: str,
        tweet_url: str,
        author: str,
    ) -> None:
        """Log that a tweet was rejected/skipped."""
        if not self._config.discord_channel_rejected_log:
            return

        await self._send_to_channel(
            self._config.discord_channel_rejected_log,
            f"⏭️ Skipped: [@{author}]({tweet_url}) (ID: {tweet_id})",
        )

    async def send_status(self, message: str) -> None:
        """Send a status message to the status channel."""
        if not self._config.discord_channel_status:
            return

        await self._send_to_channel(self._config.discord_channel_status, message)

    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        content: str | None = None,
        embeds: list[dict[str, Any]] | None = None,
        components: list[dict[str, Any]] | None = None,
    ) -> None:
        """Edit an existing Discord message."""
        payload: dict[str, Any] = {}
        if content is not None:
            payload["content"] = content
        if embeds is not None:
            payload["embeds"] = embeds
        if components is not None:
            payload["components"] = components

        async with httpx.AsyncClient() as client:
            await client.patch(
                f"{self._base_url}/channels/{channel_id}/messages/{message_id}",
                headers=self._headers,
                json=payload,
            )
