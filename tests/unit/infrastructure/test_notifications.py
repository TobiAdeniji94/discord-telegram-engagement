"""
Unit tests for notification service infrastructure.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

from twitter_intel.infrastructure.notifications import (
    DiscordBot,
    TelegramNotifier,
    telegram_notify,
)
from twitter_intel.domain.interfaces import NotificationService
from twitter_intel.domain.entities.tweet import TweetCandidate


@pytest.fixture
def sample_tweet():
    """Create a sample TweetCandidate for testing."""
    return TweetCandidate(
        tweet_id="1234567890",
        text="Looking for the best way to send money to Nigeria",
        author_username="test_user",
        author_name="Test User",
        author_followers=1000,
        url="https://x.com/test_user/status/1234567890",
        created_at=datetime.now(timezone.utc),
        likes=50,
        retweets=10,
        replies=5,
        quotes=2,
        views=5000,
        age_minutes=30.0,
        source_tab="Top",
        search_query="send money Nigeria",
        category_hint="solution_seeker",
    )


@pytest.fixture
def sample_analysis():
    """Create a sample analysis dict for testing."""
    return {
        "category": "solution-seekers",
        "sentiment": "positive",
        "confidence": 0.95,
        "urgency": "medium",
        "themes": ["money transfer", "Nigeria"],
        "yara_angle": "Perfect fit for Yara.cash services",
        "competitor_mentioned": None,
        "replies": [
            {"tone": "helpful", "text": "Check out yara.cash!", "strategy": "Direct approach"},
            {"tone": "friendly", "text": "We can help with that!", "strategy": "Warm approach"},
        ],
    }


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    config = MagicMock()
    config.discord_bot_token = "test_token"
    config.discord_guild_id = "guild_123"
    config.discord_channel_competitor = "ch_competitor"
    config.discord_channel_seekers = "ch_seekers"
    config.discord_channel_brand = "ch_brand"
    config.discord_channel_approved_log = "ch_approved"
    config.discord_channel_rejected_log = "ch_rejected"
    config.discord_channel_status = "ch_status"
    config.telegram_enabled = True
    config.telegram_bot_token = "telegram_token"
    config.telegram_chat_id = "chat_123"
    return config


class TestDiscordBot:
    """Tests for DiscordBot."""

    def test_implements_notification_service(self, mock_config):
        """Bot should implement NotificationService interface."""
        bot = DiscordBot(mock_config)
        assert isinstance(bot, NotificationService)

    def test_name_property(self, mock_config):
        """Bot should have correct name."""
        bot = DiscordBot(mock_config)
        assert bot.name == "discord"

    def test_get_channel_for_category_competitor(self, mock_config):
        """Should return competitor channel for competitor complaints."""
        bot = DiscordBot(mock_config)
        channel = bot._get_channel_for_category("competitor-complaints")
        assert channel == "ch_competitor"

    def test_get_channel_for_category_seekers(self, mock_config):
        """Should return seekers channel for solution seekers."""
        bot = DiscordBot(mock_config)
        channel = bot._get_channel_for_category("solution-seekers")
        assert channel == "ch_seekers"

    def test_get_channel_for_category_brand(self, mock_config):
        """Should return brand channel for brand mentions."""
        bot = DiscordBot(mock_config)
        channel = bot._get_channel_for_category("brand-mentions")
        assert channel == "ch_brand"

    def test_get_channel_for_unknown_defaults_to_brand(self, mock_config):
        """Should default to brand channel for unknown categories."""
        bot = DiscordBot(mock_config)
        channel = bot._get_channel_for_category("unknown-category")
        assert channel == "ch_brand"

    def test_build_approval_embed(self, mock_config, sample_tweet, sample_analysis):
        """Should build a valid embed dict."""
        bot = DiscordBot(mock_config)
        embed = bot._build_approval_embed(sample_tweet, sample_analysis)

        assert "title" in embed
        assert sample_tweet.author_username in embed["title"]
        assert embed["url"] == sample_tweet.url
        assert embed["description"] == sample_tweet.text
        assert "fields" in embed
        assert "footer" in embed
        assert "timestamp" in embed

    def test_build_approval_embed_color_by_sentiment(self, mock_config, sample_tweet):
        """Should set embed color based on sentiment."""
        bot = DiscordBot(mock_config)

        # Positive sentiment = green
        embed = bot._build_approval_embed(sample_tweet, {"sentiment": "positive"})
        assert embed["color"] == 0x2ECC71

        # Negative sentiment = red
        embed = bot._build_approval_embed(sample_tweet, {"sentiment": "negative"})
        assert embed["color"] == 0xE74C3C

        # Neutral sentiment = gray
        embed = bot._build_approval_embed(sample_tweet, {"sentiment": "neutral"})
        assert embed["color"] == 0x95A5A6

    def test_build_approval_components(self, mock_config, sample_tweet, sample_analysis):
        """Should build valid component structure."""
        bot = DiscordBot(mock_config)
        components = bot._build_approval_components(sample_tweet, sample_analysis)

        assert len(components) >= 2  # At least reply row + control row
        # Last row should have skip and custom buttons
        last_row = components[-1]
        assert last_row["type"] == 1
        assert len(last_row["components"]) == 2

    def test_build_approval_components_button_ids(self, mock_config, sample_tweet, sample_analysis):
        """Should create correct button custom_ids."""
        bot = DiscordBot(mock_config)
        components = bot._build_approval_components(sample_tweet, sample_analysis)

        # Check control buttons
        last_row = components[-1]
        reject_btn = last_row["components"][0]
        custom_btn = last_row["components"][1]

        assert reject_btn["custom_id"] == f"reject:{sample_tweet.tweet_id}"
        assert custom_btn["custom_id"] == f"custom:{sample_tweet.tweet_id}"


class TestTelegramNotifier:
    """Tests for TelegramNotifier."""

    def test_implements_notification_service(self, mock_config):
        """Notifier should implement NotificationService interface."""
        notifier = TelegramNotifier(mock_config)
        assert isinstance(notifier, NotificationService)

    def test_name_property(self, mock_config):
        """Notifier should have correct name."""
        notifier = TelegramNotifier(mock_config)
        assert notifier.name == "telegram"

    def test_enabled_when_configured(self, mock_config):
        """Should be enabled when all config is present."""
        notifier = TelegramNotifier(mock_config)
        assert notifier.enabled is True

    def test_disabled_when_telegram_disabled(self, mock_config):
        """Should be disabled when telegram_enabled is False."""
        mock_config.telegram_enabled = False
        notifier = TelegramNotifier(mock_config)
        assert notifier.enabled is False

    def test_disabled_when_no_token(self, mock_config):
        """Should be disabled when no bot token."""
        mock_config.telegram_bot_token = ""
        notifier = TelegramNotifier(mock_config)
        assert notifier.enabled is False

    def test_disabled_when_no_chat_id(self, mock_config):
        """Should be disabled when no chat ID."""
        mock_config.telegram_chat_id = ""
        notifier = TelegramNotifier(mock_config)
        assert notifier.enabled is False

    @pytest.mark.asyncio
    async def test_log_rejected_does_nothing(self, mock_config, sample_tweet):
        """log_rejected should not spam Telegram."""
        notifier = TelegramNotifier(mock_config)
        # Should not raise
        await notifier.log_rejected(
            sample_tweet.tweet_id,
            sample_tweet.url,
            sample_tweet.author_username,
        )


class TestTelegramNotifyFunction:
    """Tests for telegram_notify standalone function."""

    @pytest.mark.asyncio
    async def test_does_nothing_when_disabled(self):
        """Should do nothing when Telegram is disabled."""
        config = MagicMock()
        config.telegram_enabled = False
        config.telegram_bot_token = ""

        # Should not raise
        await telegram_notify(config, "Test message")

    @pytest.mark.asyncio
    async def test_does_nothing_when_no_token(self):
        """Should do nothing when no bot token."""
        config = MagicMock()
        config.telegram_enabled = True
        config.telegram_bot_token = ""

        # Should not raise
        await telegram_notify(config, "Test message")
