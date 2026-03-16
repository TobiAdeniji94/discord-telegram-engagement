"""
Pytest configuration and fixtures for Twitter Intelligence Bot tests.
"""

import os
import sys
from pathlib import Path

import pytest

# Add src to path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


@pytest.fixture
def clean_env(monkeypatch):
    """
    Fixture that clears all Twitter Intel related environment variables.
    Use this to ensure tests start with a clean slate.
    """
    env_vars_to_clear = [
        "SEARCH_QUERIES",
        "SEARCH_SINCE_DAYS",
        "MAX_TWEET_AGE_MINUTES",
        "MIN_REPLIES_TOP",
        "MIN_LIKES_TOP",
        "MIN_REPLIES_LATEST",
        "MIN_LIKES_LATEST",
        "NUM_REPLY_OPTIONS",
        "POLL_INTERVAL",
        "SEARCH_PROVIDER",
        "TWITTERAPI_IO_API_KEY",
        "BRAND_X_USERNAME",
        "MAX_API_REQUESTS_PER_SCAN",
        "MAX_LOCAL_CANDIDATES_PER_SCAN",
        "MAX_AI_CANDIDATES_PER_SCAN",
        "MAX_DISCORD_APPROVALS_PER_SCAN",
        "ENABLE_LATEST_FALLBACK",
        "LANE_EMPTY_SCAN_THRESHOLD",
        "DEBUG_DISCARDED_TO_STATUS",
        "XAI_API_KEY",
        "XAI_MODEL",
        "XAI_MAX_TURNS",
        "XAI_REQUEST_TIMEOUT_SECONDS",
        "XAI_EXCLUDED_X_HANDLES",
        "XAI_ALLOWED_X_HANDLES",
        "XAI_ENABLE_IMAGE_UNDERSTANDING",
        "XAI_ENABLE_VIDEO_UNDERSTANDING",
        "XAI_DEBUG_LOG_TOOL_CALLS",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "DISCORD_BOT_TOKEN",
        "DISCORD_GUILD_ID",
        "DISCORD_CH_COMPETITOR",
        "DISCORD_CH_SEEKERS",
        "DISCORD_CH_BRAND",
        "DISCORD_CH_APPROVED",
        "DISCORD_CH_REJECTED",
        "DISCORD_CH_STATUS",
        "DISCORD_COMMAND_AUTH_MODE",
        "DISCORD_ALLOWED_USER_IDS",
        "DISCORD_ALLOWED_ROLE_IDS",
        "DISCORD_ALLOWED_CHANNEL_IDS",
        "DISCORD_REQUIRE_PENDING_CHANNEL_MATCH",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_ENABLED",
        "X_AUTH_TOKEN",
        "X_CSRF_TOKEN",
        "X_COOKIE",
        "X_POSTING_DRY_RUN",
        "TWSCRAPE_USERNAME",
        "TWSCRAPE_PASSWORD",
        "TWSCRAPE_EMAIL",
        "TWSCRAPE_EMAIL_PASSWORD",
        "TWSCRAPE_COOKIES",
        "TWSCRAPE_DB_PATH",
        "TWSCRAPE_AUTO_RESET_LOCKS",
        "TWSCRAPE_LOCK_RESET_COOLDOWN",
        "BRAND_CONTEXT",
        "DB_PATH",
    ]
    for var in env_vars_to_clear:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def sample_config(clean_env):
    """
    Fixture that provides a Config with test values.
    """
    clean_env.setenv("DISCORD_BOT_TOKEN", "test_token_12345")
    clean_env.setenv("GEMINI_API_KEY", "test_gemini_key")
    clean_env.setenv("SEARCH_PROVIDER", "manual_only")
    clean_env.setenv("POLL_INTERVAL", "60")
    clean_env.setenv("DB_PATH", ":memory:")

    from twitter_intel.config import load_config
    return load_config()
