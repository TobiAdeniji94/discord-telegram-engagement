"""
Main configuration settings for Twitter Intelligence Bot.

Provides the Config dataclass that holds all bot settings, loaded from
environment variables via load_config().
"""

import json
import logging
import os
from dataclasses import dataclass, field

from twitter_intel.config.env_utils import (
    env_flag,
    parse_handle_env_list,
    parse_id_env_list,
    resolve_db_path,
    resolve_twscrape_db_path,
)
from twitter_intel.config.search_queries import SearchQuery, DEFAULT_SEARCH_QUERIES


log = logging.getLogger("twitter_intel.config")


# Default brand context for AI classification and reply generation
DEFAULT_BRAND_CONTEXT = """ABOUT YARA.CASH:
Yara.cash is an African fintech platform that makes cross-border payments simple and affordable.

KEY SELLING POINTS:
- Fast cross-border transfers (Africa <-> World)
- Low/transparent fees (no hidden charges)
- Virtual dollar cards for online payments
- Multi-currency wallets
- Built for freelancers, remote workers, and businesses
- Reliable and modern UX

COMPETITOR LANDSCAPE:
- Chipper Cash: Often has downtime, high fees on certain corridors
- LemFi: Limited coverage, slow support
- Grey.co: Complex onboarding, occasional card issues
- Sendwave: Transfer failures, limited features
- Wise: High fees for Nigeria corridor, slow
- Remitly: Delays, poor exchange rates for Africa

TONE:
- Never bash competitors directly - focus on what Yara.cash does better
- Be empathetic to frustrated users
- Offer genuine help, not just marketing
- Witty but professional
- Use "we" when talking about Yara.cash
- Sound like a real person, not a brand account"""


@dataclass
class Config:
    """
    Main configuration for the Twitter Intelligence Bot.

    All settings are loaded from environment variables via load_config().
    """

    # --- Search Queries ---
    search_queries: list[SearchQuery] = field(
        default_factory=lambda: list(DEFAULT_SEARCH_QUERIES)
    )

    # --- Filters ---
    max_tweet_age_minutes: int = 120
    min_replies_top: int = 3
    min_likes_top: int = 5
    min_replies_latest: int = 2
    min_likes_latest: int = 3
    num_reply_options: int = 4
    poll_interval: int = 900  # 15 minutes

    # --- Search Provider ---
    search_provider: str = "twitterapi_io"
    search_since_days: int | None = None
    twitterapi_io_api_key: str = ""
    brand_x_username: str = ""

    # --- Rate Limiting ---
    max_api_requests_per_scan: int = 4
    max_local_candidates_per_scan: int = 8
    max_ai_candidates_per_scan: int = 4
    max_discord_approvals_per_scan: int = 2
    enable_latest_fallback: bool = False
    lane_empty_scan_threshold: int = 3

    # --- Debug ---
    debug_discarded_to_status: bool = False

    # --- xAI / Grok ---
    xai_api_key: str = ""
    xai_model: str = "grok-4-1-fast-reasoning"
    xai_max_turns: int = 2
    xai_request_timeout_seconds: int = 30
    xai_excluded_x_handles: list[str] = field(default_factory=list)
    xai_allowed_x_handles: list[str] = field(default_factory=list)
    xai_enable_image_understanding: bool = False
    xai_enable_video_understanding: bool = False
    xai_debug_log_tool_calls: bool = False

    # --- Gemini ---
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # --- Discord ---
    discord_bot_token: str = ""
    discord_guild_id: str = ""
    discord_channel_competitor: str = ""
    discord_channel_seekers: str = ""
    discord_channel_brand: str = ""
    discord_channel_approved_log: str = ""
    discord_channel_rejected_log: str = ""
    discord_channel_status: str = ""
    discord_command_auth_mode: str = "enforce"
    discord_allowed_user_ids: list[str] = field(default_factory=list)
    discord_allowed_role_ids: list[str] = field(default_factory=list)
    discord_allowed_channel_ids: list[str] = field(default_factory=list)
    discord_require_pending_channel_match: bool = True

    # --- Telegram (fallback/notification) ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = False

    # --- X/Twitter Posting ---
    x_auth_token: str = ""
    x_csrf_token: str = ""
    x_cookie: str = ""
    x_posting_dry_run: bool = False

    # --- twscrape ---
    twscrape_username: str = ""
    twscrape_password: str = ""
    twscrape_email: str = ""
    twscrape_email_password: str = ""
    twscrape_cookies: str = ""
    twscrape_db_path: str = "accounts.db"
    twscrape_auto_reset_locks: bool = True
    twscrape_lock_reset_cooldown: int = 300

    # --- Brand Context ---
    brand_context: str = DEFAULT_BRAND_CONTEXT

    # --- Database ---
    db_path: str = "bot_state.db"


@dataclass
class SearchJob:
    """
    Represents a search job to be executed.

    Combines a SearchQuery with the specific query type to use for this run.
    """
    query: SearchQuery
    query_type: str = "Top"


@dataclass
class SearchRuntime:
    """
    Runtime state tracking for search operations.

    Tracks timing, statistics, and telemetry across scan cycles.
    """
    # Timing
    last_query_run: dict[str, float] = field(default_factory=dict)
    empty_scan_counts: dict[str, int] = field(default_factory=dict)
    provider_paused_until: float = 0.0
    provider_pause_reason: str = ""
    last_fetch_summary: str = ""

    # Statistics
    scans_completed: int = 0
    api_requests_made: int = 0
    tweets_fetched: int = 0
    duplicates_dropped: int = 0
    locally_filtered_out: int = 0
    sent_to_gemini: int = 0
    queued_to_discord: int = 0

    # xAI-specific metrics
    xai_requests_made: int = 0
    xai_x_search_tool_calls: int = 0
    xai_prompt_tokens: int = 0
    xai_completion_tokens: int = 0
    xai_reasoning_tokens: int = 0
    xai_cost_usd_ticks: int = 0
    auth_denied_commands: int = 0
    auth_denied_interactions: int = 0
    custom_reply_missing_pending: int = 0
    pending_channel_mismatch_denied: int = 0
    stale_candidate_ids: set[str] = field(default_factory=set)


def load_config() -> Config:
    """
    Load configuration from environment variables.

    Returns:
        Config instance with all settings loaded from environment
    """
    # Parse search queries from JSON env var, or use defaults
    raw_queries = os.getenv("SEARCH_QUERIES")
    raw_since_days = os.getenv("SEARCH_SINCE_DAYS", "").strip()
    search_queries = None
    search_since_days = None

    if raw_queries:
        try:
            parsed = json.loads(raw_queries)
            search_queries = [
                SearchQuery(**q) if isinstance(q, dict) else q
                for q in parsed
            ]
        except Exception:
            log.warning("Failed to parse SEARCH_QUERIES, using defaults")

    if raw_since_days:
        try:
            search_since_days = max(0, int(raw_since_days))
        except ValueError:
            log.warning("Ignoring invalid SEARCH_SINCE_DAYS value: %s", raw_since_days)

    discord_command_auth_mode = os.getenv("DISCORD_COMMAND_AUTH_MODE", "enforce").strip().lower()
    if discord_command_auth_mode not in {"audit", "enforce"}:
        discord_command_auth_mode = "enforce"

    cfg = Config(
        # Filters
        max_tweet_age_minutes=int(os.getenv("MAX_TWEET_AGE_MINUTES", "120")),
        min_replies_top=int(os.getenv("MIN_REPLIES_TOP", "3")),
        min_likes_top=int(os.getenv("MIN_LIKES_TOP", "5")),
        min_replies_latest=int(os.getenv("MIN_REPLIES_LATEST", "2")),
        min_likes_latest=int(os.getenv("MIN_LIKES_LATEST", "3")),
        num_reply_options=int(os.getenv("NUM_REPLY_OPTIONS", "4")),
        poll_interval=int(os.getenv("POLL_INTERVAL", "900")),
        # Search provider
        search_provider=os.getenv("SEARCH_PROVIDER", "twitterapi_io").strip().lower(),
        search_since_days=search_since_days,
        twitterapi_io_api_key=os.getenv("TWITTERAPI_IO_API_KEY", ""),
        brand_x_username=os.getenv("BRAND_X_USERNAME", "").lstrip("@"),
        # Rate limiting
        max_api_requests_per_scan=int(os.getenv("MAX_API_REQUESTS_PER_SCAN", "4")),
        max_local_candidates_per_scan=int(
            os.getenv("MAX_LOCAL_CANDIDATES_PER_SCAN", "8")
        ),
        max_ai_candidates_per_scan=int(os.getenv("MAX_AI_CANDIDATES_PER_SCAN", "4")),
        max_discord_approvals_per_scan=int(
            os.getenv("MAX_DISCORD_APPROVALS_PER_SCAN", "2")
        ),
        enable_latest_fallback=env_flag("ENABLE_LATEST_FALLBACK"),
        lane_empty_scan_threshold=int(os.getenv("LANE_EMPTY_SCAN_THRESHOLD", "3")),
        debug_discarded_to_status=env_flag("DEBUG_DISCARDED_TO_STATUS"),
        # xAI
        xai_api_key=os.getenv("XAI_API_KEY", ""),
        xai_model=os.getenv("XAI_MODEL", "grok-4-1-fast-reasoning"),
        xai_max_turns=max(1, int(os.getenv("XAI_MAX_TURNS", "2"))),
        xai_request_timeout_seconds=max(
            5,
            int(os.getenv("XAI_REQUEST_TIMEOUT_SECONDS", "30")),
        ),
        xai_excluded_x_handles=parse_handle_env_list("XAI_EXCLUDED_X_HANDLES"),
        xai_allowed_x_handles=parse_handle_env_list("XAI_ALLOWED_X_HANDLES"),
        xai_enable_image_understanding=env_flag("XAI_ENABLE_IMAGE_UNDERSTANDING"),
        xai_enable_video_understanding=env_flag("XAI_ENABLE_VIDEO_UNDERSTANDING"),
        xai_debug_log_tool_calls=env_flag("XAI_DEBUG_LOG_TOOL_CALLS"),
        # Gemini
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        # Discord
        discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", ""),
        discord_guild_id=os.getenv("DISCORD_GUILD_ID", ""),
        discord_channel_competitor=os.getenv("DISCORD_CH_COMPETITOR", ""),
        discord_channel_seekers=os.getenv("DISCORD_CH_SEEKERS", ""),
        discord_channel_brand=os.getenv("DISCORD_CH_BRAND", ""),
        discord_channel_approved_log=os.getenv("DISCORD_CH_APPROVED", ""),
        discord_channel_rejected_log=os.getenv("DISCORD_CH_REJECTED", ""),
        discord_channel_status=os.getenv("DISCORD_CH_STATUS", ""),
        discord_command_auth_mode=discord_command_auth_mode,
        discord_allowed_user_ids=parse_id_env_list("DISCORD_ALLOWED_USER_IDS"),
        discord_allowed_role_ids=parse_id_env_list("DISCORD_ALLOWED_ROLE_IDS"),
        discord_allowed_channel_ids=parse_id_env_list("DISCORD_ALLOWED_CHANNEL_IDS"),
        discord_require_pending_channel_match=env_flag(
            "DISCORD_REQUIRE_PENDING_CHANNEL_MATCH", "true"
        ),
        # Telegram
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        telegram_enabled=env_flag("TELEGRAM_ENABLED"),
        # X/Twitter posting
        x_auth_token=os.getenv("X_AUTH_TOKEN", ""),
        x_csrf_token=os.getenv("X_CSRF_TOKEN", ""),
        x_cookie=os.getenv("X_COOKIE", ""),
        x_posting_dry_run=env_flag("X_POSTING_DRY_RUN"),
        # twscrape
        twscrape_username=os.getenv("TWSCRAPE_USERNAME", ""),
        twscrape_password=os.getenv("TWSCRAPE_PASSWORD", ""),
        twscrape_email=os.getenv("TWSCRAPE_EMAIL", ""),
        twscrape_email_password=os.getenv("TWSCRAPE_EMAIL_PASSWORD", ""),
        twscrape_cookies=os.getenv("TWSCRAPE_COOKIES", ""),
        twscrape_db_path=resolve_twscrape_db_path(
            os.getenv("TWSCRAPE_DB_PATH", "accounts.db")
        ),
        twscrape_auto_reset_locks=env_flag("TWSCRAPE_AUTO_RESET_LOCKS", "true"),
        twscrape_lock_reset_cooldown=int(
            os.getenv("TWSCRAPE_LOCK_RESET_COOLDOWN", "300")
        ),
        # Brand context
        brand_context=os.getenv("BRAND_CONTEXT", DEFAULT_BRAND_CONTEXT),
        # Database
        db_path=resolve_db_path(os.getenv("DB_PATH", "bot_state.db")),
    )

    if search_queries:
        cfg.search_queries = search_queries

    return cfg
