"""
Yara.cash Twitter Intelligence Bot v2
======================================
Monitors X/Twitter for:
  1. Competitor complaints (shortcomings → pitch Yara.cash solutions)
  2. Pain-point tweets (people needing solutions Yara.cash provides)
  3. Brand mentions (direct mentions of yara/yara.cash)

Uses Discord as the control plane with organized channels:
  #competitor-complaints   → Tweets complaining about competitors
  #solution-seekers        → People asking for solutions yara provides
  #brand-mentions          → Direct yara.cash mentions
  #approved-log            → Log of all approved & posted replies
  #rejected-log            → Skipped tweets for review
  #bot-status              → Health, stats, errors

Inspired by @geniusyinka's approach: each concern gets its own channel,
so nothing gets buried in context.

Flow:
  Cron (15 min) → Scrape X → Classify → Gemini sentiment + reply gen
  → Route to correct Discord channel with approve buttons
  → Human approves → Post reply to X → Log to #approved-log
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import time
import traceback
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from enum import Enum

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("yara-bot")


def env_flag(name: str, default: str = "false") -> bool:
    """Parse a boolean env var consistently."""
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def parse_handle_env_list(name: str) -> list[str]:
    """Parse a comma-separated list of X handles, capped to 10 unique values."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return []

    handles: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        handle = part.strip().lstrip("@")
        if not handle or handle in seen:
            continue
        handles.append(handle)
        seen.add(handle)
        if len(handles) >= 10:
            break
    return handles


def parse_id_env_list(name: str, max_items: int = 100) -> list[str]:
    """Parse a comma-separated list of Discord ID values."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return []

    values: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        value = part.strip()
        if not value or not value.isdigit() or value in seen:
            continue
        values.append(value)
        seen.add(value)
        if len(values) >= max(1, max_items):
            break
    return values


def resolve_data_path(raw_path: str, default_name: str) -> str:
    """
    Prefer the mounted Docker volume when a relative DB path is provided.
    Outside Docker, keep relative paths unchanged.
    """
    path = Path(raw_path or default_name)
    if path.is_absolute():
        return str(path)

    container_data_dir = Path("/app/data")
    if container_data_dir.exists():
        return str(container_data_dir / path.name)

    return str(path)


def resolve_db_path(raw_path: str) -> str:
    return resolve_data_path(raw_path, "bot_state.db")


def resolve_twscrape_db_path(raw_path: str) -> str:
    return resolve_data_path(raw_path, "accounts.db")


# ---------------------------------------------------------------------------
# Tweet Categories
# ---------------------------------------------------------------------------

class TweetCategory(str, Enum):
    COMPETITOR_COMPLAINT = "competitor-complaints"
    SOLUTION_SEEKER = "solution-seekers"
    BRAND_MENTION = "brand-mentions"
    IRRELEVANT = "irrelevant"


def parse_smoke_category(raw_value: str | None) -> TweetCategory | None:
    if raw_value is None:
        return TweetCategory.BRAND_MENTION

    normalized = raw_value.strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "brand": TweetCategory.BRAND_MENTION,
        "brand-mention": TweetCategory.BRAND_MENTION,
        "brand-mentions": TweetCategory.BRAND_MENTION,
        "mention": TweetCategory.BRAND_MENTION,
        "mentions": TweetCategory.BRAND_MENTION,
        "competitor": TweetCategory.COMPETITOR_COMPLAINT,
        "competitor-complaint": TweetCategory.COMPETITOR_COMPLAINT,
        "competitor-complaints": TweetCategory.COMPETITOR_COMPLAINT,
        "complaint": TweetCategory.COMPETITOR_COMPLAINT,
        "complaints": TweetCategory.COMPETITOR_COMPLAINT,
        "seeker": TweetCategory.SOLUTION_SEEKER,
        "seekers": TweetCategory.SOLUTION_SEEKER,
        "solution": TweetCategory.SOLUTION_SEEKER,
        "solution-seeker": TweetCategory.SOLUTION_SEEKER,
        "solution-seekers": TweetCategory.SOLUTION_SEEKER,
    }
    return aliases.get(normalized)


def category_to_hint(category: TweetCategory) -> str:
    mapping = {
        TweetCategory.COMPETITOR_COMPLAINT: "competitor_complaint",
        TweetCategory.SOLUTION_SEEKER: "solution_seeker",
        TweetCategory.BRAND_MENTION: "brand_mention",
    }
    return mapping.get(category, "brand_mention")


def is_local_test_tweet_id(tweet_id: str) -> bool:
    return tweet_id.startswith(("smoke-", "manual-"))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SearchQuery:
    """A search query with its category context."""
    query: str
    category_hint: str  # helps Gemini classify
    description: str
    query_type: str = "Top"
    cooldown_seconds: int = 3600
    max_pages: int = 1
    enabled: bool = True


@dataclass
class Config:
    # --- Search Queries ---
    # Organized by intent. Each query tells the bot WHERE to look and
    # gives Gemini context about what we're looking for.
    search_queries: list[SearchQuery] = field(default_factory=lambda: [
        SearchQuery(
            query='("chipper cash" OR "lemfi" OR "grey.co" OR "sendwave" OR "wise" OR "remitly") '
                  '(down OR failed OR issue OR complaint OR slow OR fees) '
                  "lang:en -filter:retweets -filter:replies min_faves:3",
            category_hint="competitor_complaint",
            description="High-signal competitor complaints",
            cooldown_seconds=3600,
        ),
        SearchQuery(
            query='("send money" OR "receive USD" OR "dollar card" OR "best app") '
                  '(Nigeria OR Africa) '
                  '(need OR looking OR best OR cheapest) '
                  "lang:en -filter:retweets -filter:replies min_faves:2",
            category_hint="solution_seeker",
            description="People asking for the exact problems Yara solves",
            cooldown_seconds=2700,
        ),
        SearchQuery(
            query='("yara.cash" OR "yara cash") lang:en -filter:retweets',
            category_hint="brand_mention",
            description="Fallback brand keyword mentions when direct mentions are unavailable",
            cooldown_seconds=900,
        ),
    ])

    # --- Filters ---
    max_tweet_age_minutes: int = 120
    min_replies_top: int = 3
    min_likes_top: int = 5
    min_replies_latest: int = 2
    min_likes_latest: int = 3
    num_reply_options: int = 4
    poll_interval: int = 900  # 15 min
    search_provider: str = "twitterapi_io"
    search_since_days: int | None = None
    twitterapi_io_api_key: str = ""
    brand_x_username: str = ""
    max_api_requests_per_scan: int = 4
    max_local_candidates_per_scan: int = 8
    max_ai_candidates_per_scan: int = 4
    max_discord_approvals_per_scan: int = 2
    enable_latest_fallback: bool = False
    lane_empty_scan_threshold: int = 3
    debug_discarded_to_status: bool = False
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
    discord_guild_id: str = ""  # Your server ID
    # Channel IDs (set after running /setup or manually)
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

    # --- Telegram (kept as fallback/notification) ---
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
    brand_context: str = """ABOUT YARA.CASH:
Yara.cash is an African fintech platform that makes cross-border payments simple and affordable.

KEY SELLING POINTS:
- Fast cross-border transfers (Africa ↔ World)
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
- Never bash competitors directly — focus on what Yara.cash does better
- Be empathetic to frustrated users
- Offer genuine help, not just marketing
- Witty but professional
- Use "we" when talking about Yara.cash
- Sound like a real person, not a brand account"""

    db_path: str = "bot_state.db"


@dataclass
class SearchJob:
    query: SearchQuery
    query_type: str = "Top"


@dataclass
class SearchRuntime:
    last_query_run: dict[str, float] = field(default_factory=dict)
    empty_scan_counts: dict[str, int] = field(default_factory=dict)
    provider_paused_until: float = 0.0
    provider_pause_reason: str = ""
    last_fetch_summary: str = ""
    api_requests_made: int = 0
    tweets_fetched: int = 0
    duplicates_dropped: int = 0
    locally_filtered_out: int = 0
    sent_to_gemini: int = 0
    queued_to_discord: int = 0
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


def load_config() -> Config:
    """Load config from environment variables."""
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
            pass
    if raw_since_days:
        try:
            search_since_days = max(0, int(raw_since_days))
        except ValueError:
            log.warning("Ignoring invalid SEARCH_SINCE_DAYS value: %s", raw_since_days)

    discord_command_auth_mode = os.getenv("DISCORD_COMMAND_AUTH_MODE", "enforce").strip().lower()
    if discord_command_auth_mode not in {"audit", "enforce"}:
        discord_command_auth_mode = "enforce"

    cfg = Config(
        max_tweet_age_minutes=int(os.getenv("MAX_TWEET_AGE_MINUTES", "120")),
        min_replies_top=int(os.getenv("MIN_REPLIES_TOP", "3")),
        min_likes_top=int(os.getenv("MIN_LIKES_TOP", "5")),
        min_replies_latest=int(os.getenv("MIN_REPLIES_LATEST", "2")),
        min_likes_latest=int(os.getenv("MIN_LIKES_LATEST", "3")),
        num_reply_options=int(os.getenv("NUM_REPLY_OPTIONS", "4")),
        poll_interval=int(os.getenv("POLL_INTERVAL", "900")),
        search_provider=os.getenv("SEARCH_PROVIDER", "twitterapi_io").strip().lower(),
        search_since_days=search_since_days,
        twitterapi_io_api_key=os.getenv("TWITTERAPI_IO_API_KEY", ""),
        brand_x_username=os.getenv("BRAND_X_USERNAME", "").lstrip("@"),
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
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
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
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        telegram_enabled=env_flag("TELEGRAM_ENABLED"),
        x_auth_token=os.getenv("X_AUTH_TOKEN", ""),
        x_csrf_token=os.getenv("X_CSRF_TOKEN", ""),
        x_cookie=os.getenv("X_COOKIE", ""),
        x_posting_dry_run=env_flag("X_POSTING_DRY_RUN"),
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
        brand_context=os.getenv("BRAND_CONTEXT", Config.brand_context),
        db_path=resolve_db_path(os.getenv("DB_PATH", "bot_state.db")),
    )
    if search_queries:
        cfg.search_queries = search_queries
    return cfg


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_tweets (
            tweet_id TEXT PRIMARY KEY,
            tweet_url TEXT,
            tweet_text TEXT,
            author TEXT,
            category TEXT,
            sentiment TEXT,
            status TEXT DEFAULT 'pending',
            approved_reply TEXT,
            search_query TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            replied_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_approvals (
            tweet_id TEXT PRIMARY KEY,
            reply_options TEXT,
            discord_message_id TEXT,
            discord_channel_id TEXT,
            category TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_stats (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    return conn


def is_tweet_processed(conn: sqlite3.Connection, tweet_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM processed_tweets WHERE tweet_id = ?", (tweet_id,)
    ).fetchone()
    return row is not None


def mark_tweet_processed(conn: sqlite3.Connection, tweet_id: str, url: str,
                         text: str, author: str, category: str,
                         sentiment: str, search_query: str):
    conn.execute(
        """INSERT OR IGNORE INTO processed_tweets 
           (tweet_id, tweet_url, tweet_text, author, category, sentiment, search_query) 
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (tweet_id, url, text, author, category, sentiment, search_query),
    )
    conn.commit()


def save_pending(conn: sqlite3.Connection, tweet_id: str,
                 reply_options: list[str], discord_msg_id: str,
                 discord_ch_id: str, category: str):
    conn.execute(
        """INSERT OR REPLACE INTO pending_approvals 
           (tweet_id, reply_options, discord_message_id, discord_channel_id, category) 
           VALUES (?, ?, ?, ?, ?)""",
        (tweet_id, json.dumps(reply_options), discord_msg_id, discord_ch_id, category),
    )
    conn.commit()


def get_pending(conn: sqlite3.Connection, tweet_id: str):
    row = conn.execute(
        "SELECT reply_options, discord_message_id, discord_channel_id, category FROM pending_approvals WHERE tweet_id = ?",
        (tweet_id,),
    ).fetchone()
    if row:
        return json.loads(row[0]), row[1], row[2], row[3]
    return None, None, None, None


def mark_replied(conn: sqlite3.Connection, tweet_id: str, reply_text: str):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE processed_tweets SET status='replied', approved_reply=?, replied_at=? WHERE tweet_id=?",
        (reply_text, now, tweet_id),
    )
    conn.execute("DELETE FROM pending_approvals WHERE tweet_id=?", (tweet_id,))
    conn.commit()


def mark_rejected(conn: sqlite3.Connection, tweet_id: str):
    conn.execute(
        "UPDATE processed_tweets SET status='rejected' WHERE tweet_id=?",
        (tweet_id,),
    )
    conn.execute("DELETE FROM pending_approvals WHERE tweet_id=?", (tweet_id,))
    conn.commit()


def get_stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT * FROM processed_tweets").fetchall()
    total = len(rows)
    replied = sum(1 for r in rows if r[6] == "replied")
    rejected = sum(1 for r in rows if r[6] == "rejected")
    pending = sum(1 for r in rows if r[6] == "pending")
    by_cat = {}
    for r in rows:
        cat = r[4] or "unknown"
        by_cat[cat] = by_cat.get(cat, 0) + 1
    return {
        "total_processed": total,
        "replied": replied,
        "rejected": rejected,
        "pending": pending,
        "by_category": by_cat,
    }


# ---------------------------------------------------------------------------
# X/Twitter Scraping
# ---------------------------------------------------------------------------

async def setup_twscrape(cfg: Config):
    from twscrape import API
    from twscrape.utils import parse_cookies

    target_db = Path(cfg.twscrape_db_path)
    legacy_db = Path("/app/accounts.db")
    if not target_db.exists() and legacy_db.exists() and target_db != legacy_db:
        target_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_db, target_db)
        log.info("Migrated twscrape session db to %s", cfg.twscrape_db_path)

    api = API(cfg.twscrape_db_path)

    account = await api.pool.get_account(cfg.twscrape_username)
    cookies = parse_cookies(cfg.twscrape_cookies) if cfg.twscrape_cookies else None

    if account:
        updated = False
        if cfg.twscrape_password and cfg.twscrape_password != account.password:
            account.password = cfg.twscrape_password
            updated = True
        if cfg.twscrape_email and cfg.twscrape_email != account.email:
            account.email = cfg.twscrape_email
            updated = True
        if cfg.twscrape_email_password and cfg.twscrape_email_password != account.email_password:
            account.email_password = cfg.twscrape_email_password
            updated = True
        if cookies is not None:
            account.cookies = cookies
            account.headers = {}
            account.locks = {}
            account.error_msg = None
            account.active = "ct0" in account.cookies
            updated = True
        if updated:
            await api.pool.save(account)
    else:
        await api.pool.add_account(
            cfg.twscrape_username,
            cfg.twscrape_password,
            cfg.twscrape_email,
            cfg.twscrape_email_password,
            cookies=cfg.twscrape_cookies or None,
        )

    stats = await api.pool.stats()
    if stats.get("active", 0) == 0:
        failed_accounts = [
            account.username
            for account in await api.pool.get_all()
            if account.error_msg
        ]
        if failed_accounts:
            log.info("Retrying previously failed twscrape login(s)")
            await api.pool.relogin_failed()
        elif not cfg.twscrape_cookies:
            log.info("No active twscrape session found, attempting password login")
            await api.pool.login_all()

        stats = await api.pool.stats()

    if stats.get("active", 0) > 0:
        log.info(
            "twscrape ready with %s active account(s); session db: %s",
            stats.get("active", 0),
            cfg.twscrape_db_path,
        )
    else:
        log.error(
            "twscrape has no active accounts. If password login is blocked by Cloudflare, "
            "set TWSCRAPE_COOKIES from a logged-in burner X account."
        )

    return api


async def has_active_twscrape_account(api) -> bool:
    stats = await api.pool.stats()
    return stats.get("active", 0) > 0


async def search_tweets(api, query: str, tab: str = "Top", limit: int = 30):
    from twscrape import gather
    tweets = await gather(api.search(query, limit=limit, kv={"product": tab}))
    return tweets


class TwitterApiIoAuthError(Exception):
    pass


class TwitterApiIoRateLimitError(Exception):
    def __init__(self, message: str, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class TwitterApiIoClient:
    def __init__(self, api_key: str):
        self.base_url = "https://api.twitterapi.io"
        self.headers = {"X-API-Key": api_key}

    async def _request_json(self, path: str, params: dict) -> dict:
        import httpx

        timeout = httpx.Timeout(20.0, connect=10.0)
        last_error = None

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    headers=self.headers,
                    timeout=timeout,
                ) as client:
                    resp = await client.get(path, params=params)

                if resp.status_code in (401, 403):
                    raise TwitterApiIoAuthError(
                        f"twitterapi.io auth failed ({resp.status_code})"
                    )
                if resp.status_code == 429:
                    raise TwitterApiIoRateLimitError(
                        "twitterapi.io rate limited",
                        retry_after_seconds=self._parse_retry_after_seconds(
                            resp.headers.get("Retry-After")
                        ),
                    )
                if resp.status_code >= 500 and attempt < 2:
                    await asyncio.sleep(1 + attempt)
                    continue

                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, dict):
                    raise ValueError("Unexpected response shape from twitterapi.io")
                return data
            except (TwitterApiIoAuthError, TwitterApiIoRateLimitError, ValueError):
                raise
            except httpx.HTTPStatusError:
                raise
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= 2:
                    raise
                await asyncio.sleep(1 + attempt)

        raise last_error or RuntimeError("twitterapi.io request failed")

    @staticmethod
    def _parse_retry_after_seconds(raw_value: str | None) -> int | None:
        if not raw_value:
            return None

        try:
            return max(1, int(float(raw_value)))
        except ValueError:
            pass

        try:
            parsed = parsedate_to_datetime(raw_value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            delay = int((parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
            return max(1, delay)
        except Exception:
            return None

    async def advanced_search(self, query: str, query_type: str = "Top") -> dict:
        return await self._request_json(
            "/twitter/tweet/advanced_search",
            {
                "query": query,
                "queryType": query_type,
            },
        )

    async def user_mentions(self, user_name: str) -> dict:
        return await self._request_json(
            "/twitter/user/mentions",
            {"userName": user_name},
        )


class XaiAuthError(Exception):
    pass


class XaiRateLimitError(Exception):
    def __init__(self, message: str, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class XaiClient:
    def __init__(self, api_key: str, timeout_seconds: int = 30):
        self.base_url = "https://api.x.ai"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _parse_retry_after_seconds(raw_value: str | None) -> int | None:
        if not raw_value:
            return None

        try:
            return max(1, int(float(raw_value)))
        except ValueError:
            pass

        try:
            parsed = parsedate_to_datetime(raw_value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            delay = int((parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
            return max(1, delay)
        except Exception:
            return None

    async def create_response(
        self,
        model: str,
        prompt: str,
        tool_config: dict,
        max_turns: int,
    ) -> dict:
        import httpx

        timeout = httpx.Timeout(float(self.timeout_seconds), connect=min(10.0, float(self.timeout_seconds)))
        last_error = None
        payload = {
            "model": model,
            "input": [{"role": "user", "content": prompt}],
            "tools": [tool_config],
            "include": ["no_inline_citations"],
            "max_turns": max(1, max_turns),
        }

        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    headers=self.headers,
                    timeout=timeout,
                ) as client:
                    resp = await client.post("/v1/responses", json=payload)

                if resp.status_code in (401, 403):
                    raise XaiAuthError(f"xAI auth failed ({resp.status_code})")
                if resp.status_code == 429:
                    raise XaiRateLimitError(
                        "xAI rate limited",
                        retry_after_seconds=self._parse_retry_after_seconds(
                            resp.headers.get("Retry-After")
                        ),
                    )
                if resp.status_code >= 500 and attempt < 1:
                    await asyncio.sleep(1 + attempt)
                    continue

                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, dict):
                    raise ValueError("Unexpected response shape from xAI")
                return data
            except (XaiAuthError, XaiRateLimitError, ValueError):
                raise
            except httpx.HTTPStatusError:
                raise
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= 1:
                    raise
                await asyncio.sleep(1 + attempt)

        raise last_error or RuntimeError("xAI request failed")


def _search_date_window(search_since_days: int | None) -> tuple[str | None, str | None]:
    if search_since_days is None:
        return None, None

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=search_since_days)
    return start_date.isoformat(), end_date.isoformat()


def _preferred_category_for_hint(category_hint: str) -> str:
    mapping = {
        "competitor_complaint": TweetCategory.COMPETITOR_COMPLAINT.value,
        "solution_seeker": TweetCategory.SOLUTION_SEEKER.value,
        "brand_mention": TweetCategory.BRAND_MENTION.value,
    }
    return mapping.get(category_hint, TweetCategory.BRAND_MENTION.value)


def _normalize_category_value(raw_value) -> str:
    normalized = str(raw_value or "").strip().lower().replace("_", "-")
    aliases = {
        "competitor-complaint": TweetCategory.COMPETITOR_COMPLAINT.value,
        "competitor-complaints": TweetCategory.COMPETITOR_COMPLAINT.value,
        "solution-seeker": TweetCategory.SOLUTION_SEEKER.value,
        "solution-seekers": TweetCategory.SOLUTION_SEEKER.value,
        "brand-mention": TweetCategory.BRAND_MENTION.value,
        "brand-mentions": TweetCategory.BRAND_MENTION.value,
        "irrelevant": TweetCategory.IRRELEVANT.value,
    }
    return aliases.get(normalized, TweetCategory.IRRELEVANT.value)


def _hint_for_category_value(category_value: str) -> str:
    mapping = {
        TweetCategory.COMPETITOR_COMPLAINT.value: "competitor_complaint",
        TweetCategory.SOLUTION_SEEKER.value: "solution_seeker",
        TweetCategory.BRAND_MENTION.value: "brand_mention",
    }
    return mapping.get(category_value, "brand_mention")


def build_xai_tool_config(
    cfg: Config,
    from_date: str | None,
    to_date: str | None,
) -> dict:
    tool = {"type": "x_search"}
    if from_date:
        tool["from_date"] = from_date
    if to_date:
        tool["to_date"] = to_date

    if cfg.xai_allowed_x_handles:
        tool["allowed_x_handles"] = cfg.xai_allowed_x_handles[:10]
    elif cfg.xai_excluded_x_handles:
        tool["excluded_x_handles"] = cfg.xai_excluded_x_handles[:10]

    if cfg.xai_enable_image_understanding:
        tool["enable_image_understanding"] = True
    if cfg.xai_enable_video_understanding:
        tool["enable_video_understanding"] = True

    return tool


def build_xai_search_prompt(cfg: Config, job: SearchJob) -> str:
    from_date, to_date = _search_date_window(cfg.search_since_days)
    date_window = (
        f"Use the tool's date window from {from_date} to {to_date} (UTC dates inclusive)."
        if from_date and to_date
        else "No explicit tool date window is configured."
    )
    preferred_category = _preferred_category_for_hint(job.query.category_hint)

    return f"""{cfg.brand_context}

You are curating X posts for Yara.cash. Use the x_search tool to find recent, high-signal, actionable posts that match the lane below.

Lane:
- Description: {job.query.description}
- Search intent: {job.query.query}
- Preferred category: {preferred_category}
- Query mode hint: {job.query_type}
- {date_window}

Selection rules:
- Interpret the search intent semantically even if it contains X-style operators such as lang:, since:, or -filter:.
- Ignore retweets, obvious spam, giveaways, and unrelated chatter.
- Prefer posts that are recent, specific, and worth a human reply.
- Return at most {cfg.max_discord_approvals_per_scan} candidates.
- Only include candidates that deserve review.
- Do not include irrelevant items in the final JSON.
- Preserve the exact X post URL in tweet_url.
- Produce 1 to {cfg.num_reply_options} reply options per candidate.
- Keep each reply under 280 characters and aligned with Yara.cash positioning.

Allowed categories:
- "{TweetCategory.COMPETITOR_COMPLAINT.value}"
- "{TweetCategory.SOLUTION_SEEKER.value}"
- "{TweetCategory.BRAND_MENTION.value}"
- "{TweetCategory.IRRELEVANT.value}" (do not return these in the array)

Return STRICT JSON only with no markdown and no extra prose:
{{
  "candidates": [
    {{
      "tweet_url": "https://x.com/.../status/123",
      "tweet_text": "Exact post text",
      "author_username": "handle",
      "author_name": "Display Name",
      "created_at_iso": "2026-03-04T12:34:56Z",
      "category": "{preferred_category}",
      "sentiment": "positive|negative|neutral|mixed",
      "confidence": 0.0,
      "urgency": "low|medium|high",
      "themes": ["theme1", "theme2"],
      "competitor_mentioned": "name or null",
      "yara_angle": "How Yara.cash can help",
      "why_relevant": "Why this deserves human review",
      "replies": [
        {{
          "tone": "empathetic",
          "text": "Draft reply under 280 chars",
          "strategy": "One-line reason for this draft"
        }}
      ]
    }}
  ]
}}"""


def extract_output_text_from_xai_response(payload: dict) -> str:
    def _collect_from_container(container: dict) -> list[str]:
        direct = container.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return [direct.strip()]
        elif isinstance(direct, list):
            texts: list[str] = []
            for item in direct:
                if isinstance(item, str) and item.strip():
                    texts.append(item.strip())
            if texts:
                return texts

        texts: list[str] = []

        output_items = container.get("output")
        if isinstance(output_items, list):
            for item in output_items:
                if not isinstance(item, dict):
                    continue

                content = item.get("content")
                if isinstance(content, str) and content.strip():
                    texts.append(content.strip())
                elif isinstance(content, list):
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        for key in ("text", "output_text", "value"):
                            value = part.get(key)
                            if isinstance(value, str) and value.strip():
                                texts.append(value.strip())
                                break

                for key in ("text", "output_text"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        texts.append(value.strip())
                        break
        return texts

    collected = _collect_from_container(payload)
    nested = payload.get("response")
    if not collected and isinstance(nested, dict):
        collected = _collect_from_container(nested)

    if collected:
        return "\n".join(collected)
    raise ValueError("xAI response did not include any output text")


def _extract_citation_urls(payload: dict) -> list[str]:
    candidates: list[str] = []

    def _collect(raw_value):
        if isinstance(raw_value, str):
            text = raw_value.strip()
            if text:
                candidates.append(text)
            return
        if isinstance(raw_value, list):
            for item in raw_value:
                _collect(item)
            return
        if not isinstance(raw_value, dict):
            return

        for key in ("url", "value"):
            value = raw_value.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
                break

        for nested_key in ("citation", "x_citation"):
            nested = raw_value.get(nested_key)
            if nested is not None:
                _collect(nested)

    _collect(payload.get("citations"))
    nested = payload.get("response")
    if isinstance(nested, dict):
        _collect(nested.get("citations"))

    seen: set[str] = set()
    unique: list[str] = []
    for url in candidates:
        normalized = url.strip().rstrip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def extract_tweet_id_from_x_url(url: str) -> str | None:
    match = re.search(r"/status/(\d+)", str(url or ""))
    if not match:
        return None
    return match.group(1)


def validate_candidate_citations(candidate_url: str, citations: list[str]) -> bool:
    normalized_candidate = str(candidate_url or "").strip().rstrip("/")
    if not normalized_candidate:
        return False

    candidate_tweet_id = extract_tweet_id_from_x_url(normalized_candidate)
    for citation_url in citations:
        normalized_citation = str(citation_url or "").strip().rstrip("/")
        if not normalized_citation:
            continue
        if normalized_citation == normalized_candidate:
            return True
        if candidate_tweet_id and extract_tweet_id_from_x_url(normalized_citation) == candidate_tweet_id:
            return True
    return False


def _extract_author_from_x_url(url: str) -> str:
    text = str(url or "")
    match = re.search(r"x\\.com/([^/]+)/status/", text)
    if not match:
        return "unknown"
    username = match.group(1).strip().lstrip("@")
    return username or "unknown"


def _clean_reply_options(raw_replies) -> list[dict]:
    cleaned: list[dict] = []
    if not isinstance(raw_replies, list):
        return cleaned

    for item in raw_replies:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        cleaned.append(
            {
                "tone": str(item.get("tone") or "helpful").strip()[:40] or "helpful",
                "text": text[:280],
                "strategy": str(item.get("strategy") or "").strip()[:160],
            }
        )
    return cleaned


def parse_xai_candidates(
    payload: dict,
    response_text: str,
    job: SearchJob,
) -> list["PreparedReviewCandidate"]:
    parsed = json.loads(response_text)
    if not isinstance(parsed, dict):
        raise ValueError("xAI output must be a JSON object")

    raw_candidates = parsed.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("xAI output must include a 'candidates' list")

    citation_urls = _extract_citation_urls(payload)
    if not citation_urls:
        raise ValueError("xAI output did not include citations")

    now = datetime.now(timezone.utc)
    prepared: list[PreparedReviewCandidate] = []

    for item in raw_candidates:
        if not isinstance(item, dict):
            continue

        tweet_url = str(item.get("tweet_url") or "").strip()
        tweet_text = str(item.get("tweet_text") or "").strip()
        if not tweet_url or not tweet_text:
            continue
        if not validate_candidate_citations(tweet_url, citation_urls):
            log.info("Discarded xAI candidate: URL not present in citations (%s)", tweet_url)
            continue

        tweet_id = extract_tweet_id_from_x_url(tweet_url)
        if not tweet_id:
            log.info("Discarded xAI candidate: invalid X status URL (%s)", tweet_url)
            continue

        replies = _clean_reply_options(item.get("replies"))
        if not replies:
            continue

        category = _normalize_category_value(item.get("category"))
        if category == TweetCategory.IRRELEVANT.value:
            continue

        created_at = _parse_datetime_value(item.get("created_at_iso"))
        age_minutes = max(0.0, (now - created_at).total_seconds() / 60)
        sentiment = str(item.get("sentiment") or "neutral").strip().lower()
        if sentiment not in {"positive", "negative", "neutral", "mixed"}:
            sentiment = "neutral"

        try:
            confidence = min(1.0, max(0.0, float(item.get("confidence", 0.6))))
        except (TypeError, ValueError):
            confidence = 0.6

        urgency = str(item.get("urgency") or "low").strip().lower()
        if urgency not in {"low", "medium", "high"}:
            urgency = "low"

        themes = item.get("themes")
        if not isinstance(themes, list):
            themes = []
        cleaned_themes = [str(theme).strip() for theme in themes if str(theme).strip()][:6]

        competitor = item.get("competitor_mentioned")
        competitor_text = str(competitor).strip() if competitor not in (None, "") else None
        author_username = str(
            item.get("author_username") or _extract_author_from_x_url(tweet_url)
        ).strip().lstrip("@") or "unknown"
        author_name = str(item.get("author_name") or author_username).strip() or author_username
        why_relevant = str(item.get("why_relevant") or "").strip()
        yara_angle = str(item.get("yara_angle") or why_relevant or "Relevant X post found via Grok search.").strip()

        tweet = TweetCandidate(
            tweet_id=tweet_id,
            text=tweet_text,
            author_username=author_username,
            author_name=author_name,
            author_followers=0,
            url=tweet_url,
            created_at=created_at,
            likes=0,
            retweets=0,
            replies=0,
            quotes=0,
            views=0,
            age_minutes=age_minutes,
            source_tab=f"Grok/{job.query_type}",
            search_query=job.query.query,
            category_hint=_hint_for_category_value(category),
            local_score=confidence,
        )
        analysis = {
            "category": category,
            "sentiment": sentiment,
            "confidence": confidence,
            "themes": cleaned_themes,
            "urgency": urgency,
            "competitor_mentioned": competitor_text,
            "yara_angle": yara_angle,
            "why_relevant": why_relevant,
            "replies": replies,
        }
        prepared.append(
            PreparedReviewCandidate(
                tweet=tweet,
                analysis=analysis,
                provider="xai_x_search",
                source_query=job.query.query,
            )
        )

    return prepared


def _collect_tool_call_names(payload: dict) -> list[str]:
    raw_calls = payload.get("tool_calls")
    nested = payload.get("response")
    if raw_calls is None and isinstance(nested, dict):
        raw_calls = nested.get("tool_calls")

    names: list[str] = []
    if isinstance(raw_calls, list):
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                names.append(function["name"])
                continue
            for key in ("name", "tool_name", "type"):
                value = call.get(key)
                if isinstance(value, str):
                    names.append(value)
                    break
    return names


def _collect_server_side_x_search_calls(payload: dict) -> int:
    usage = payload.get("server_side_tool_usage")
    nested = payload.get("response")
    if usage is None and isinstance(nested, dict):
        usage = nested.get("server_side_tool_usage")

    if isinstance(usage, dict):
        raw = usage.get("x_search", usage.get("x_search_calls"))
        if isinstance(raw, bool):
            return 1 if raw else 0
        if isinstance(raw, (int, float)):
            return int(raw)
        if isinstance(raw, dict):
            for key in ("count", "successful_calls", "calls"):
                value = raw.get(key)
                if isinstance(value, (int, float)):
                    return int(value)

        total = 0
        for key, value in usage.items():
            if "x_search" not in str(key):
                continue
            if isinstance(value, bool):
                total += 1 if value else 0
            elif isinstance(value, (int, float)):
                total += int(value)
            elif isinstance(value, dict):
                nested_count = value.get("count")
                if isinstance(nested_count, (int, float)):
                    total += int(nested_count)
        if total:
            return total

    return 0


def _update_xai_usage_counters(runtime: SearchRuntime, payload: dict) -> list[str]:
    usage = payload.get("usage")
    nested = payload.get("response")
    if usage is None and isinstance(nested, dict):
        usage = nested.get("usage")

    if isinstance(usage, dict):
        for key, attr in (
            ("prompt_tokens", "xai_prompt_tokens"),
            ("input_tokens", "xai_prompt_tokens"),
            ("completion_tokens", "xai_completion_tokens"),
            ("output_tokens", "xai_completion_tokens"),
            ("reasoning_tokens", "xai_reasoning_tokens"),
            ("cost_usd_ticks", "xai_cost_usd_ticks"),
            ("estimated_cost_usd_ticks", "xai_cost_usd_ticks"),
        ):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                setattr(runtime, attr, getattr(runtime, attr) + int(value))

    tool_names = _collect_tool_call_names(payload)
    x_search_calls = _collect_server_side_x_search_calls(payload)
    if not x_search_calls:
        x_search_calls = sum(
            1 for name in tool_names if name.startswith("x_") or name == "x_search"
        )
    runtime.xai_x_search_tool_calls += x_search_calls
    return tool_names


async def create_xai_response(
    client: XaiClient,
    cfg: Config,
    prompt: str,
    tool_config: dict,
) -> dict:
    return await client.create_response(
        model=cfg.xai_model,
        prompt=prompt,
        tool_config=tool_config,
        max_turns=cfg.xai_max_turns,
    )


async def fetch_candidates_from_xai_x_search(
    cfg: Config,
    client: XaiClient,
    runtime: SearchRuntime,
    discord_bot,
) -> list["PreparedReviewCandidate"]:
    now_ts = time.time()
    runtime.last_fetch_summary = ""
    if runtime.provider_paused_until > now_ts:
        wait_seconds = max(1, int(runtime.provider_paused_until - now_ts))
        runtime.last_fetch_summary = f"provider_paused:{wait_seconds}"
        log.warning(
            "Skipping xAI scan for %ss: %s",
            wait_seconds,
            runtime.provider_pause_reason or "provider paused",
        )
        return []

    runtime.provider_paused_until = 0.0
    runtime.provider_pause_reason = ""

    request_budget = max(0, cfg.max_api_requests_per_scan)
    due_jobs = select_due_queries(
        cfg,
        runtime,
        request_budget,
        brand_direct_enabled=False,
    )
    if not due_jobs:
        runtime.last_fetch_summary = "no_due_queries"
        return []

    prepared_candidates: list[PreparedReviewCandidate] = []
    from_date, to_date = _search_date_window(cfg.search_since_days)
    tool_config = build_xai_tool_config(cfg, from_date, to_date)
    remaining_budget = request_budget

    async def _pause_provider(reason: str, pause_seconds: int | None = None):
        actual_pause_seconds = max(1, pause_seconds or cfg.poll_interval)
        runtime.provider_paused_until = time.time() + actual_pause_seconds
        runtime.provider_pause_reason = reason
        runtime.last_fetch_summary = f"provider_paused:{actual_pause_seconds}"
        await discord_bot.send_status(reason)

    for job in due_jobs:
        if remaining_budget <= 0:
            break

        runtime.last_query_run[job.query.query] = time.time()
        prompt = build_xai_search_prompt(cfg, job)
        try:
            runtime.api_requests_made += 1
            runtime.xai_requests_made += 1
            remaining_budget -= 1
            log.info("Requesting Grok X Search for query '%s'", job.query.description)
            payload = await create_xai_response(client, cfg, prompt, tool_config)
            tool_names = _update_xai_usage_counters(runtime, payload)
            if cfg.xai_debug_log_tool_calls and tool_names:
                log.info("xAI tool calls: %s", ", ".join(tool_names))

            response_text = extract_output_text_from_xai_response(payload)
            try:
                job_candidates = parse_xai_candidates(payload, response_text, job)
            except (json.JSONDecodeError, ValueError) as exc:
                if remaining_budget <= 0:
                    log.error(
                        "Skipping xAI query '%s': invalid JSON and no request budget remains for a repair retry (%s)",
                        job.query.description or job.query.query,
                        exc,
                    )
                    job_candidates = []
                else:
                    log.warning("Invalid xAI JSON response, retrying once: %s", exc)
                    repair_prompt = (
                        prompt
                        + "\n\nYour previous reply was invalid. Return STRICT JSON only with the required schema."
                    )
                    runtime.api_requests_made += 1
                    runtime.xai_requests_made += 1
                    remaining_budget -= 1
                    payload = await create_xai_response(client, cfg, repair_prompt, tool_config)
                    tool_names = _update_xai_usage_counters(runtime, payload)
                    if cfg.xai_debug_log_tool_calls and tool_names:
                        log.info("xAI tool calls: %s", ", ".join(tool_names))
                    response_text = extract_output_text_from_xai_response(payload)
                    try:
                        job_candidates = parse_xai_candidates(payload, response_text, job)
                    except (json.JSONDecodeError, ValueError) as final_exc:
                        log.error(
                            "Skipping xAI query '%s': %s",
                            job.query.description or job.query.query,
                            final_exc,
                        )
                        job_candidates = []

            prepared_candidates.extend(job_candidates)
        except XaiAuthError as exc:
            log.error(str(exc))
            await _pause_provider("xAI auth failed. Check XAI_API_KEY before the next scan.")
            return prepared_candidates
        except XaiRateLimitError as exc:
            log.warning(
                "%s%s",
                str(exc),
                f" (retry after {exc.retry_after_seconds}s)"
                if exc.retry_after_seconds
                else "",
            )
            await _pause_provider(
                "xAI rate limited the bot. Search is paused until the retry window.",
                exc.retry_after_seconds,
            )
            return prepared_candidates
        except Exception as exc:
            log.error("xAI search failed '%s': %s", job.query.description or job.query.query, exc)

    if not runtime.last_fetch_summary:
        runtime.last_fetch_summary = (
            "zero_provider_results" if not prepared_candidates else f"candidates:{len(prepared_candidates)}"
        )

    return prepared_candidates


def _since_clause(search_since_days: int | None) -> str:
    if search_since_days is None:
        return ""

    start = datetime.now(timezone.utc).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    ) - timedelta(days=search_since_days)
    return f"since:{start.strftime('%Y-%m-%d_%H:%M:%S_UTC')}"


def normalize_search_query(cfg: Config, sq: SearchQuery) -> str:
    query = sq.query.strip()
    lowered = query.lower()

    if "lang:" not in lowered:
        query += " lang:en"
        lowered = query.lower()
    if "-filter:retweets" not in lowered:
        query += " -filter:retweets"
        lowered = query.lower()
    if sq.category_hint != "brand_mention" and "-filter:replies" not in lowered:
        query += " -filter:replies"
        lowered = query.lower()
    if "min_faves:" not in lowered:
        if sq.category_hint == "competitor_complaint":
            query += " min_faves:3"
        elif sq.category_hint == "solution_seeker":
            query += " min_faves:2"
        lowered = query.lower()
    if "since:" not in lowered:
        since_clause = _since_clause(cfg.search_since_days)
        if since_clause:
            query += f" {since_clause}"

    return " ".join(query.split())


def _coerce_tweet_list(payload: dict) -> list[dict]:
    tweets = payload.get("tweets")
    if isinstance(tweets, list):
        return tweets

    nested = payload.get("data")
    if isinstance(nested, list):
        return nested
    if isinstance(nested, dict):
        nested_tweets = nested.get("tweets")
        if isinstance(nested_tweets, list):
            return nested_tweets

    return []


def _parse_datetime_value(raw_value) -> datetime:
    now = datetime.now(timezone.utc)
    if raw_value is None:
        return now

    try:
        if isinstance(raw_value, (int, float)):
            return datetime.fromtimestamp(raw_value, tz=timezone.utc)

        text = str(raw_value).strip()
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc)

        try:
            parsed = parsedate_to_datetime(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            pass

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return now


def parse_twitterapi_io_tweet(
    payload: dict,
    source_tab: str,
    sq: SearchQuery,
    is_direct_mention: bool = False,
) -> "TweetCandidate | None":
    try:
        if not isinstance(payload, dict):
            return None

        tweet_id = str(payload.get("id") or "").strip()
        text = str(payload.get("text") or payload.get("fullText") or "").strip()
        if not tweet_id or not text:
            return None

        author = payload.get("author")
        if not isinstance(author, dict):
            author = {}

        author_username = str(
            author.get("userName") or author.get("screen_name") or "unknown"
        ).lstrip("@")
        created = _parse_datetime_value(payload.get("createdAt"))
        now = datetime.now(timezone.utc)
        age_minutes = max(0.0, (now - created).total_seconds() / 60)
        url = payload.get("url")
        if not url:
            if author_username and author_username != "unknown":
                url = f"https://x.com/{author_username}/status/{tweet_id}"
            else:
                url = f"https://x.com/i/status/{tweet_id}"

        return TweetCandidate(
            tweet_id=tweet_id,
            text=text,
            author_username=author_username,
            author_name=str(author.get("name") or author_username),
            author_followers=int(
                author.get("followers") or author.get("followersCount") or 0
            ),
            url=url,
            created_at=created,
            likes=int(payload.get("likeCount") or 0),
            retweets=int(payload.get("retweetCount") or 0),
            replies=int(payload.get("replyCount") or 0),
            quotes=int(payload.get("quoteCount") or 0),
            views=int(payload.get("viewCount") or 0),
            age_minutes=age_minutes,
            source_tab=source_tab,
            search_query=sq.query,
            category_hint=sq.category_hint,
            is_direct_mention=is_direct_mention,
        )
    except Exception as exc:
        log.warning(f"Failed to parse twitterapi.io tweet: {exc}")
        return None


def score_candidate(tweet: "TweetCandidate") -> float:
    score = (
        tweet.replies * 4.0
        + tweet.likes * 1.5
        + tweet.retweets * 2.0
        + tweet.quotes * 2.0
        + min(tweet.views / 500.0, 8.0)
        + min(tweet.author_followers / 5000.0, 4.0)
    )

    if tweet.category_hint == "solution_seeker":
        score += 3.0
    elif tweet.category_hint == "competitor_complaint":
        score += 2.0
    elif tweet.category_hint == "brand_mention":
        score += 1.0

    if tweet.age_minutes > 360:
        score -= 4.0
    elif tweet.age_minutes > 120:
        score -= 2.0

    return score


def _candidate_score_threshold(tweet: "TweetCandidate") -> float | None:
    if tweet.category_hint == "brand_mention" and tweet.is_direct_mention:
        return None

    thresholds = {
        "competitor_complaint": 12.0,
        "solution_seeker": 11.0,
        "brand_mention": 6.0,
    }
    return thresholds.get(tweet.category_hint, 10.0)


def _format_discarded_candidates(
    discarded: list[tuple[str, float, str]],
    limit: int = 3,
) -> list[str]:
    if not discarded:
        return []

    top = sorted(discarded, key=lambda item: item[1], reverse=True)[:limit]
    return [
        f"{tweet_id} score={score:.1f} reason={reason}"
        for tweet_id, score, reason in top
    ]


def _lane_priority(query: SearchQuery, brand_direct_enabled: bool) -> int:
    if query.category_hint == "solution_seeker":
        return 0
    if query.category_hint == "competitor_complaint":
        return 1
    if query.category_hint == "brand_mention":
        return 2 if brand_direct_enabled else 3
    return 4


def select_due_queries(
    cfg: Config,
    runtime: SearchRuntime,
    request_budget: int,
    brand_direct_enabled: bool | None = None,
) -> list[SearchJob]:
    if request_budget <= 0:
        return []

    now_ts = time.time()
    if brand_direct_enabled is None:
        brand_direct_enabled = (
            cfg.search_provider == "twitterapi_io" and bool(cfg.brand_x_username)
        )
    due_jobs: list[SearchJob] = []

    for query in cfg.search_queries:
        if not query.enabled:
            continue
        if brand_direct_enabled and query.category_hint == "brand_mention":
            continue

        last_run = runtime.last_query_run.get(query.query, 0.0)
        if now_ts - last_run < max(60, query.cooldown_seconds):
            continue

        query_type = query.query_type or "Top"
        if (
            cfg.enable_latest_fallback
            and runtime.empty_scan_counts.get(query.query, 0) >= cfg.lane_empty_scan_threshold
        ):
            query_type = "Latest"

        due_jobs.append(SearchJob(query=query, query_type=query_type))

    due_jobs.sort(
        key=lambda job: (
            _lane_priority(job.query, brand_direct_enabled),
            runtime.last_query_run.get(job.query.query, 0.0),
        )
    )
    return due_jobs[:request_budget]


async def fetch_candidates_from_twitterapi_io(
    cfg: Config,
    client: TwitterApiIoClient,
    runtime: SearchRuntime,
    discord_bot,
) -> list["TweetCandidate"]:
    now_ts = time.time()
    runtime.last_fetch_summary = ""
    if runtime.provider_paused_until > now_ts:
        wait_seconds = max(1, int(runtime.provider_paused_until - now_ts))
        runtime.last_fetch_summary = f"provider_paused:{wait_seconds}"
        log.warning(
            "Skipping twitterapi.io scan for %ss: %s",
            wait_seconds,
            runtime.provider_pause_reason or "provider paused",
        )
        return []

    runtime.provider_paused_until = 0.0
    runtime.provider_pause_reason = ""

    collected: list[TweetCandidate] = []
    request_budget = max(0, cfg.max_api_requests_per_scan)
    due_query_count = 0
    executed_requests = 0

    async def _handle_query_response(
        payload: dict,
        sq: SearchQuery,
        source_tab: str,
        is_direct_mention: bool = False,
    ):
        raw_tweets = _coerce_tweet_list(payload)[:20]
        runtime.tweets_fetched += len(raw_tweets)
        parsed_count = 0

        for item in raw_tweets:
            parsed = parse_twitterapi_io_tweet(
                item,
                source_tab,
                sq,
                is_direct_mention=is_direct_mention,
            )
            if parsed:
                collected.append(parsed)
                parsed_count += 1

        runtime.empty_scan_counts[sq.query] = (
            0 if parsed_count > 0 else runtime.empty_scan_counts.get(sq.query, 0) + 1
        )

    async def _pause_provider(reason: str, pause_seconds: int | None = None):
        actual_pause_seconds = max(1, pause_seconds or cfg.poll_interval)
        runtime.provider_paused_until = time.time() + actual_pause_seconds
        runtime.provider_pause_reason = reason
        runtime.last_fetch_summary = f"provider_paused:{actual_pause_seconds}"
        await discord_bot.send_status(reason)

    if cfg.brand_x_username and request_budget > 0:
        due_query_count += 1
        mention_query = SearchQuery(
            query=f"mentions:{cfg.brand_x_username}",
            category_hint="brand_mention",
            description="Direct brand mentions",
            cooldown_seconds=cfg.poll_interval,
        )
        try:
            executed_requests += 1
            runtime.api_requests_made += 1
            log.info("Searching direct mentions for @%s", cfg.brand_x_username)
            payload = await client.user_mentions(cfg.brand_x_username)
            await _handle_query_response(payload, mention_query, "Mentions", True)
        except TwitterApiIoAuthError as exc:
            log.error(str(exc))
            await _pause_provider(
                "twitterapi.io auth failed. Check TWITTERAPI_IO_API_KEY before the next scan."
            )
            return collected
        except TwitterApiIoRateLimitError as exc:
            log.warning(
                "%s%s",
                str(exc),
                f" (retry after {exc.retry_after_seconds}s)"
                if exc.retry_after_seconds
                else "",
            )
            await _pause_provider(
                "twitterapi.io rate limited the bot. Search is paused until the retry window.",
                exc.retry_after_seconds,
            )
            return collected
        except Exception as exc:
            log.error(f"twitterapi.io mentions failed: {exc}")
        finally:
            request_budget -= 1

    due_jobs = select_due_queries(
        cfg,
        runtime,
        request_budget,
        brand_direct_enabled=bool(cfg.brand_x_username),
    )
    due_query_count += len(due_jobs)

    for job in due_jobs:
        runtime.last_query_run[job.query.query] = time.time()
        provider_query = normalize_search_query(cfg, job.query)
        try:
            executed_requests += 1
            runtime.api_requests_made += 1
            log.info("Searching twitterapi.io: '%s' (%s)", provider_query, job.query_type)
            payload = await client.advanced_search(provider_query, query_type=job.query_type)
            await _handle_query_response(payload, job.query, job.query_type)
        except TwitterApiIoAuthError as exc:
            log.error(str(exc))
            await _pause_provider(
                "twitterapi.io auth failed. Check TWITTERAPI_IO_API_KEY before the next scan."
            )
            break
        except TwitterApiIoRateLimitError as exc:
            log.warning(
                "%s%s",
                str(exc),
                f" (retry after {exc.retry_after_seconds}s)"
                if exc.retry_after_seconds
                else "",
            )
            await _pause_provider(
                "twitterapi.io rate limited the bot. Search is paused until the retry window.",
                exc.retry_after_seconds,
            )
            break
        except Exception as exc:
            log.error(f"twitterapi.io search failed '{job.query.query}': {exc}")

    if not runtime.last_fetch_summary:
        if executed_requests == 0 and due_query_count == 0:
            runtime.last_fetch_summary = "no_due_queries"
        elif executed_requests > 0 and not collected:
            runtime.last_fetch_summary = "zero_provider_results"
        else:
            runtime.last_fetch_summary = f"candidates:{len(collected)}"

    return collected


@dataclass
class TweetCandidate:
    tweet_id: str
    text: str
    author_username: str
    author_name: str
    author_followers: int
    url: str
    created_at: datetime
    likes: int
    retweets: int
    replies: int
    quotes: int
    views: int
    age_minutes: float
    source_tab: str
    search_query: str
    category_hint: str
    is_direct_mention: bool = False
    local_score: float = 0.0


@dataclass
class PreparedReviewCandidate:
    tweet: TweetCandidate
    analysis: dict
    provider: str
    source_query: str


async def queue_candidate_for_review(
    db: sqlite3.Connection,
    discord_bot,
    tweet: TweetCandidate,
    analysis: dict,
) -> bool:
    category = analysis.get("category", "irrelevant")
    sentiment = analysis.get("sentiment", "neutral")

    if category == TweetCategory.IRRELEVANT.value:
        log.info(f"Skipping irrelevant tweet {tweet.tweet_id}")
        mark_tweet_processed(
            db,
            tweet.tweet_id,
            tweet.url,
            tweet.text,
            tweet.author_username,
            category,
            sentiment,
            tweet.search_query,
        )
        return False

    reply_texts = [r["text"] for r in analysis.get("replies", [])]

    mark_tweet_processed(
        db,
        tweet.tweet_id,
        tweet.url,
        tweet.text,
        tweet.author_username,
        category,
        sentiment,
        tweet.search_query,
    )

    result = await discord_bot.send_approval(tweet, analysis)
    if not result:
        return False

    msg_id, ch_id = result
    save_pending(db, tweet.tweet_id, reply_texts, msg_id, ch_id, category)
    log.info(f"-> Sent to Discord #{category}: {tweet.tweet_id}")
    return True


def build_manual_candidate(category: TweetCategory, text: str) -> TweetCandidate:
    now = datetime.now(timezone.utc)
    return TweetCandidate(
        tweet_id=f"manual-{int(time.time() * 1000)}",
        text=text.strip(),
        author_username="manual_ingest",
        author_name="Manual Ingest",
        author_followers=0,
        url="https://example.com/manual-ingest",
        created_at=now,
        likes=0,
        retweets=0,
        replies=0,
        quotes=0,
        views=0,
        age_minutes=0,
        source_tab="Manual",
        search_query=f"manual-ingest:{category.value}",
        category_hint=category_to_hint(category),
    )


def build_manual_ingest_analysis(category: TweetCategory, text: str) -> dict:
    snippet = text.strip().replace("\n", " ")
    if len(snippet) > 120:
        snippet = snippet[:117] + "..."

    variants = {
        TweetCategory.BRAND_MENTION: {
            "sentiment": "neutral",
            "themes": ["manual-ingest", "brand"],
            "urgency": "low",
            "competitor_mentioned": None,
            "yara_angle": "Manual brand-mention candidate injected without relying on X search.",
            "replies": [
                {
                    "tone": "friendly",
                    "text": f"Manual test reply 1: thanks for mentioning this. Context noted: \"{snippet}\"",
                    "strategy": "Verifies the default brand lane with user-provided text.",
                },
                {
                    "tone": "concise",
                    "text": f"Manual test reply 2: this routes brand mentions without waiting on X search. ({snippet})",
                    "strategy": "Verifies an alternate approval option.",
                },
            ],
        },
        TweetCategory.COMPETITOR_COMPLAINT: {
            "sentiment": "negative",
            "themes": ["manual-ingest", "competitor"],
            "urgency": "medium",
            "competitor_mentioned": "manual-test",
            "yara_angle": "Manual competitor-complaint candidate injected without twscrape.",
            "replies": [
                {
                    "tone": "empathetic",
                    "text": f"Manual test reply 1: that pain point is clear. Logged context: \"{snippet}\"",
                    "strategy": "Verifies competitor complaint routing.",
                },
                {
                    "tone": "practical",
                    "text": f"Manual test reply 2: this is a manual fallback lead, queued without X search. ({snippet})",
                    "strategy": "Verifies a second competitor-style suggestion.",
                },
            ],
        },
        TweetCategory.SOLUTION_SEEKER: {
            "sentiment": "neutral",
            "themes": ["manual-ingest", "solution-seeker"],
            "urgency": "high",
            "competitor_mentioned": None,
            "yara_angle": "Manual solution-seeker candidate injected without twscrape.",
            "replies": [
                {
                    "tone": "helpful",
                    "text": f"Manual test reply 1: this use case is queued for review. Input: \"{snippet}\"",
                    "strategy": "Verifies the solution-seekers lane.",
                },
                {
                    "tone": "direct",
                    "text": f"Manual test reply 2: this candidate bypassed X search so the workflow can still be tested. ({snippet})",
                    "strategy": "Verifies a shorter solution-led option.",
                },
            ],
        },
    }
    variant = variants[category]
    return {
        "category": category.value,
        "sentiment": variant["sentiment"],
        "confidence": 1.0,
        "themes": variant["themes"],
        "urgency": variant["urgency"],
        "competitor_mentioned": variant["competitor_mentioned"],
        "yara_angle": variant["yara_angle"],
        "replies": variant["replies"],
    }


def build_smoke_test_payload(
    category: TweetCategory = TweetCategory.BRAND_MENTION,
) -> tuple[TweetCandidate, dict]:
    """Build a synthetic candidate that exercises the Discord review workflow."""
    now = datetime.now(timezone.utc)
    tweet_id = f"smoke-{int(time.time() * 1000)}"
    smoke_variants = {
        TweetCategory.BRAND_MENTION: {
            "text": (
                "Smoke test: synthetic brand mention. Use this to verify the "
                "brand review lane, approve flow, and custom replies."
            ),
            "author_username": "yara_brand_smoke",
            "author_name": "Yara Brand Smoke",
            "search_query": "smoke-test:brand-mentions",
            "category_hint": "brand_mention",
            "sentiment": "neutral",
            "themes": ["smoke-test", "brand"],
            "urgency": "low",
            "competitor_mentioned": None,
            "yara_angle": "Synthetic brand mention for validating the default review channel.",
            "replies": [
                {
                    "tone": "helpful",
                    "text": "Smoke-test brand reply 1: approve this to verify the default flow.",
                    "strategy": "Exercises the primary approval button.",
                },
                {
                    "tone": "direct",
                    "text": "Smoke-test brand reply 2: use this to verify alternate reply choices.",
                    "strategy": "Exercises a secondary approval button.",
                },
                {
                    "tone": "custom",
                    "text": "Smoke-test brand reply 3: or use !reply for a manual response.",
                    "strategy": "Exercises the custom reply instructions.",
                },
            ],
        },
        TweetCategory.COMPETITOR_COMPLAINT: {
            "text": (
                "Smoke test: synthetic competitor complaint about slow transfers and high fees. "
                "Use this to verify the competitor review lane."
            ),
            "author_username": "competitor_smoke",
            "author_name": "Competitor Smoke",
            "search_query": "smoke-test:competitor-complaints",
            "category_hint": "competitor_complaint",
            "sentiment": "negative",
            "themes": ["smoke-test", "competitor"],
            "urgency": "medium",
            "competitor_mentioned": "ExamplePay",
            "yara_angle": "Synthetic competitor complaint for testing the competitor channel.",
            "replies": [
                {
                    "tone": "empathetic",
                    "text": "Smoke-test competitor reply 1: acknowledge the pain, then point to a smoother option.",
                    "strategy": "Tests the competitor complaint approval path.",
                },
                {
                    "tone": "practical",
                    "text": "Smoke-test competitor reply 2: offer a lower-friction alternative without bashing anyone.",
                    "strategy": "Tests a second competitor-style suggestion.",
                },
                {
                    "tone": "question",
                    "text": "Smoke-test competitor reply 3: invite them to compare a more reliable transfer flow.",
                    "strategy": "Tests a softer CTA.",
                },
            ],
        },
        TweetCategory.SOLUTION_SEEKER: {
            "text": (
                "Smoke test: synthetic solution-seeker asking for the best way to receive USD in Nigeria. "
                "Use this to verify the solution-seekers lane."
            ),
            "author_username": "seeker_smoke",
            "author_name": "Solution Seeker Smoke",
            "search_query": "smoke-test:solution-seekers",
            "category_hint": "solution_seeker",
            "sentiment": "neutral",
            "themes": ["smoke-test", "solution-seeker"],
            "urgency": "high",
            "competitor_mentioned": None,
            "yara_angle": "Synthetic demand-capture lead for testing the solution-seekers channel.",
            "replies": [
                {
                    "tone": "helpful",
                    "text": "Smoke-test seeker reply 1: answer the use case directly and mention the relevant feature.",
                    "strategy": "Tests a feature-led reply in the seekers lane.",
                },
                {
                    "tone": "concise",
                    "text": "Smoke-test seeker reply 2: position Yara.cash as a practical option for this need.",
                    "strategy": "Tests a shorter solution-oriented response.",
                },
                {
                    "tone": "clarifying",
                    "text": "Smoke-test seeker reply 3: ask a qualifying question before recommending the best path.",
                    "strategy": "Tests a consultative response pattern.",
                },
            ],
        },
    }
    variant = smoke_variants[category]
    tweet = TweetCandidate(
        tweet_id=tweet_id,
        text=variant["text"],
        author_username=variant["author_username"],
        author_name=variant["author_name"],
        author_followers=0,
        url="https://example.com/smoke-test",
        created_at=now,
        likes=12,
        retweets=3,
        replies=5,
        quotes=1,
        views=248,
        age_minutes=0,
        source_tab="Smoke",
        search_query=variant["search_query"],
        category_hint=variant["category_hint"],
    )
    analysis = {
        "category": category.value,
        "sentiment": variant["sentiment"],
        "confidence": 1.0,
        "themes": variant["themes"],
        "urgency": variant["urgency"],
        "competitor_mentioned": variant["competitor_mentioned"],
        "yara_angle": variant["yara_angle"],
        "replies": variant["replies"],
    }
    return tweet, analysis


def parse_tweet(tweet, source_tab: str, sq: SearchQuery) -> TweetCandidate | None:
    try:
        created = tweet.date
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age = (now - created).total_seconds() / 60

        return TweetCandidate(
            tweet_id=str(tweet.id),
            text=tweet.rawContent,
            author_username=tweet.user.username,
            author_name=tweet.user.displayname,
            author_followers=getattr(tweet.user, "followersCount", 0) or 0,
            url=tweet.url,
            created_at=created,
            likes=tweet.likeCount or 0,
            retweets=tweet.retweetCount or 0,
            replies=tweet.replyCount or 0,
            quotes=tweet.quoteCount or 0,
            views=tweet.viewCount or 0,
            age_minutes=age,
            source_tab=source_tab,
            search_query=sq.query,
            category_hint=sq.category_hint,
        )
    except Exception as e:
        log.warning(f"Failed to parse tweet: {e}")
        return None


def filter_candidates(tweets: list[TweetCandidate], cfg: Config) -> list[TweetCandidate]:
    filtered = []
    for t in tweets:
        if t.age_minutes > cfg.max_tweet_age_minutes:
            continue
        if t.source_tab == "Top":
            if t.replies >= cfg.min_replies_top or t.likes >= cfg.min_likes_top:
                filtered.append(t)
        else:
            if t.replies >= cfg.min_replies_latest or t.likes >= cfg.min_likes_latest:
                filtered.append(t)

    filtered.sort(key=lambda t: t.replies * 3 + t.likes + t.retweets * 2, reverse=True)
    return filtered


# ---------------------------------------------------------------------------
# Gemini - Classification + Sentiment + Reply Generation
# ---------------------------------------------------------------------------

async def classify_and_generate(cfg: Config, tweet: TweetCandidate) -> dict | None:
    """
    Gemini does three things:
    1. Classify: competitor_complaint / solution_seeker / brand_mention / irrelevant
    2. Sentiment: positive / negative / neutral / mixed
    3. Generate reply options that cleverly plug yara.cash
    """
    import google.generativeai as genai

    genai.configure(api_key=cfg.gemini_api_key)
    model = genai.GenerativeModel(cfg.gemini_model)

    prompt = f"""{cfg.brand_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK: Analyze this tweet and generate strategic replies for Yara.cash
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TWEET:
  Author: @{tweet.author_username} ({tweet.author_name})
  Text: "{tweet.text}"
  Engagement: {tweet.likes} likes, {tweet.replies} replies, {tweet.retweets} RTs, {tweet.views} views
  Age: {tweet.age_minutes:.0f} minutes old
  Search query that found it: "{tweet.search_query}"
  Category hint: {tweet.category_hint}

STEP 1 - CLASSIFY this tweet into exactly ONE category:
  - "competitor-complaints": User is frustrated/complaining about a competitor product
  - "solution-seekers": User is looking for a solution that yara.cash provides
  - "brand-mentions": User is talking about yara.cash directly
  - "irrelevant": Not useful for engagement (spam, unrelated, etc.)

STEP 2 - SENTIMENT: positive / negative / neutral / mixed

STEP 3 - GENERATE {cfg.num_reply_options} REPLY OPTIONS:
  
  For "competitor-complaints":
    - NEVER directly trash the competitor
    - Empathize with the user's frustration first
    - Subtly position yara.cash as the solution
    - Vary approaches: empathetic → helpful → cheeky-but-respectful → question-based
    - Example vibe: "That transfer anxiety is real 😤 Switched to Yara.cash last month — zero failed transfers since. Might be worth a look?"
  
  For "solution-seekers":
    - Directly address what they're looking for
    - Be helpful first, promotional second
    - Include a specific feature that solves their need
    - Example vibe: "For freelancer USD payments in Nigeria, check Yara.cash — virtual dollar cards + multi-currency wallet. No hidden fees on conversions."
  
  For "brand-mentions":
    - If positive: amplify and engage
    - If negative: address with empathy and solutions
    - If neutral: add value and personality

RULES:
  - Under 280 characters per reply
  - Sound like a real person, not a brand bot
  - Reference specifics from the tweet
  - Max 1-2 emojis per reply
  - Never start with "Hey" or "Hi there"
  - Don't use "we" excessively
  - Vary the call-to-action: some link to yara.cash, some just plant the seed

Respond ONLY in this JSON format:
{{
  "category": "competitor-complaints|solution-seekers|brand-mentions|irrelevant",
  "sentiment": "positive|negative|neutral|mixed",
  "confidence": 0.0-1.0,
  "themes": ["theme1", "theme2"],
  "urgency": "low|medium|high",
  "competitor_mentioned": "name or null",
  "yara_angle": "Brief description of how yara.cash solves this",
  "replies": [
    {{"tone": "tone_label", "text": "reply text", "strategy": "brief note on the approach"}},
    ...
  ]
}}

Return ONLY valid JSON."""

    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        text = response.text.strip()
        # Clean markdown fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

        return json.loads(text)
    except json.JSONDecodeError as e:
        log.error(f"Gemini bad JSON: {e}")
        return None
    except Exception as e:
        log.error(f"Gemini error: {e}")
        return None


# ---------------------------------------------------------------------------
# Discord Bot
# ---------------------------------------------------------------------------

class DiscordBot:
    """
    Manages Discord interactions:
    - Sends approval requests to categorized channels
    - Handles button callbacks for approve/reject/custom
    - Posts logs to #approved-log and #rejected-log
    - Sends status updates to #bot-status
    """

    def __init__(
        self,
        cfg: Config,
        db: sqlite3.Connection,
        runtime: SearchRuntime | None = None,
    ):
        self.cfg = cfg
        self.db = db
        self.runtime = runtime
        self.base_url = "https://discord.com/api/v10"
        self.headers = {
            "Authorization": f"Bot {cfg.discord_bot_token}",
            "Content-Type": "application/json",
        }

    def _get_channel_for_category(self, category: str) -> str:
        mapping = {
            TweetCategory.COMPETITOR_COMPLAINT: self.cfg.discord_channel_competitor,
            TweetCategory.SOLUTION_SEEKER: self.cfg.discord_channel_seekers,
            TweetCategory.BRAND_MENTION: self.cfg.discord_channel_brand,
        }
        return mapping.get(category, self.cfg.discord_channel_brand)

    async def send_approval(self, tweet: TweetCandidate, analysis: dict) -> tuple[str, str] | None:
        """Send approval embed to the appropriate Discord channel. Returns (message_id, channel_id)."""
        import httpx

        category = analysis.get("category", TweetCategory.BRAND_MENTION)
        channel_id = self._get_channel_for_category(category)

        if not channel_id:
            log.error(f"No Discord channel configured for category: {category}")
            return None

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

        # Build embed
        embed = {
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
                    "name": f"🎯 Sentiment",
                    "value": f"{analysis['sentiment']} ({analysis.get('confidence', 0):.0%})",
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
                    "value": analysis.get("yara_angle", "—")[:200],
                    "inline": False,
                },
            ],
            "footer": {
                "text": f"Query: {tweet.search_query} | ID: {tweet.tweet_id}",
            },
            "timestamp": tweet.created_at.isoformat(),
        }

        if analysis.get("competitor_mentioned"):
            embed["fields"].insert(3, {
                "name": "⚔️ Competitor",
                "value": analysis["competitor_mentioned"],
                "inline": True,
            })

        # Build reply option fields
        replies = analysis.get("replies", [])
        for i, r in enumerate(replies):
            strategy = r.get("strategy", "")
            strategy_note = f"\n*{strategy}*" if strategy else ""
            embed["fields"].append({
                "name": f"💬 {i+1}. [{r['tone']}]",
                "value": f"{r['text']}{strategy_note}",
                "inline": False,
            })

        # Build buttons
        components = []
        row1 = []
        row2 = []
        for i, r in enumerate(replies):
            btn = {
                "type": 2,  # button
                "style": 1 if i == 0 else 2,  # primary for first, secondary for rest
                "label": f"{i+1}. {r['tone'][:20]}",
                "custom_id": f"approve:{tweet.tweet_id}:{i}",
            }
            if len(row1) < 4:
                row1.append(btn)
            else:
                row2.append(btn)

        action_rows = [{"type": 1, "components": row1}]
        if row2:
            action_rows.append({"type": 1, "components": row2})

        # Control buttons
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

        payload = {
            "embeds": [embed],
            "components": action_rows,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/channels/{channel_id}/messages",
                headers=self.headers,
                json=payload,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["id"], channel_id
            else:
                log.error(f"Discord send failed: {resp.status_code} {resp.text[:300]}")
                return None

    async def log_to_channel(self, channel_id: str, content: str,
                              embed: dict = None):
        """Send a simple message or embed to a log channel."""
        import httpx
        payload = {"content": content}
        if embed:
            payload["embeds"] = [embed]
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.base_url}/channels/{channel_id}/messages",
                headers=self.headers,
                json=payload,
            )

    async def log_approved(self, tweet_id: str, tweet_url: str,
                            reply_text: str, author: str):
        if not self.cfg.discord_channel_approved_log:
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
        await self.log_to_channel(self.cfg.discord_channel_approved_log, "", embed)

    async def log_rejected(self, tweet_id: str, tweet_url: str, author: str):
        if not self.cfg.discord_channel_rejected_log:
            return
        await self.log_to_channel(
            self.cfg.discord_channel_rejected_log,
            f"⏭️ Skipped: [@{author}]({tweet_url}) (ID: {tweet_id})",
        )

    async def send_status(self, message: str):
        if not self.cfg.discord_channel_status:
            return
        await self.log_to_channel(self.cfg.discord_channel_status, message)

    async def send_stats(self):
        stats = get_stats(self.db)
        cat_lines = "\n".join(
            f"  {k}: {v}" for k, v in stats["by_category"].items()
        )
        search_lines = ""
        if self.runtime:
            if self.cfg.search_provider == "xai_x_search":
                search_lines = (
                    "\nSearch telemetry:\n"
                    f"  requests={self.runtime.api_requests_made} approvals={self.runtime.queued_to_discord}\n"
                    f"  duplicates={self.runtime.duplicates_dropped}\n"
                    f"  xai_requests={self.runtime.xai_requests_made} x_search_calls={self.runtime.xai_x_search_tool_calls}\n"
                    f"  prompt={self.runtime.xai_prompt_tokens} completion={self.runtime.xai_completion_tokens} reasoning={self.runtime.xai_reasoning_tokens}\n"
                    f"  cost_ticks={self.runtime.xai_cost_usd_ticks}"
                )
            else:
                estimated_request_floor = self.runtime.api_requests_made * 0.00015
                estimated_tweet_cost = (self.runtime.tweets_fetched / 1000.0) * 0.15
                estimated_total = estimated_request_floor + estimated_tweet_cost
                search_lines = (
                    "\nSearch telemetry:\n"
                    f"  requests={self.runtime.api_requests_made} tweets={self.runtime.tweets_fetched}\n"
                    f"  duplicates={self.runtime.duplicates_dropped} filtered={self.runtime.locally_filtered_out}\n"
                    f"  gemini={self.runtime.sent_to_gemini} approvals={self.runtime.queued_to_discord}\n"
                    f"  est_cost=${estimated_total:.4f}"
                )
        msg = (
            f"📊 **Bot Stats** ({datetime.now(timezone.utc).strftime('%H:%M UTC')})\n"
            f"Total processed: {stats['total_processed']}\n"
            f"Replied: {stats['replied']} | Rejected: {stats['rejected']} | Pending: {stats['pending']}\n"
            f"By category:\n{cat_lines}{search_lines}"
        )
        await self.send_status(msg)

    async def edit_message(self, channel_id: str, message_id: str,
                            content: str = None, embeds: list = None,
                            components: list = None):
        import httpx
        payload = {}
        if content is not None:
            payload["content"] = content
        if embeds is not None:
            payload["embeds"] = embeds
        if components is not None:
            payload["components"] = components
        async with httpx.AsyncClient() as client:
            await client.patch(
                f"{self.base_url}/channels/{channel_id}/messages/{message_id}",
                headers=self.headers,
                json=payload,
            )

    async def setup_channels(self):
        """Auto-create channels if they don't exist. Returns channel IDs."""
        import httpx

        guild_id = self.cfg.discord_guild_id
        if not guild_id:
            log.warning("No DISCORD_GUILD_ID set, skipping channel setup")
            return

        # First, find or create the "Yara Bot" category
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/guilds/{guild_id}/channels",
                headers=self.headers,
            )
            existing = resp.json() if resp.status_code == 200 else []

        existing_names = {ch["name"]: ch for ch in existing}

        # Create category if needed
        category_id = None
        if "yara-bot" in existing_names and existing_names["yara-bot"]["type"] == 4:
            category_id = existing_names["yara-bot"]["id"]
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/guilds/{guild_id}/channels",
                    headers=self.headers,
                    json={"name": "yara-bot", "type": 4},  # 4 = category
                )
                if resp.status_code == 201:
                    category_id = resp.json()["id"]

        # Create channels under the category
        channels_needed = {
            "competitor-complaints": "discord_channel_competitor",
            "solution-seekers": "discord_channel_seekers",
            "brand-mentions": "discord_channel_brand",
            "approved-log": "discord_channel_approved_log",
            "rejected-log": "discord_channel_rejected_log",
            "bot-status": "discord_channel_status",
        }

        for ch_name, cfg_attr in channels_needed.items():
            current_val = getattr(self.cfg, cfg_attr)
            if current_val:
                continue  # Already configured

            if ch_name in existing_names:
                setattr(self.cfg, cfg_attr, existing_names[ch_name]["id"])
            else:
                async with httpx.AsyncClient() as client:
                    payload = {"name": ch_name, "type": 0}  # 0 = text
                    if category_id:
                        payload["parent_id"] = category_id
                    resp = await client.post(
                        f"{self.base_url}/guilds/{guild_id}/channels",
                        headers=self.headers,
                        json=payload,
                    )
                    if resp.status_code in (200, 201):
                        setattr(self.cfg, cfg_attr, resp.json()["id"])
                        log.info(f"Created Discord channel: #{ch_name}")

        log.info("Discord channels ready")


# ---------------------------------------------------------------------------
# Discord Interaction Handler (via Gateway / Webhooks)
# ---------------------------------------------------------------------------

class DiscordGateway:
    """
    Connects to Discord Gateway to receive button interactions.
    Uses the interaction endpoint for button callbacks.
    """

    def __init__(
        self,
        cfg: Config,
        db: sqlite3.Connection,
        bot: DiscordBot,
        runtime: SearchRuntime | None = None,
    ):
        self.cfg = cfg
        self.db = db
        self.bot = bot
        self.runtime = runtime
        self.base_url = "https://discord.com/api/v10"
        self.headers = {
            "Authorization": f"Bot {cfg.discord_bot_token}",
            "Content-Type": "application/json",
        }

    @property
    def auth_mode(self) -> str:
        raw_mode = str(self.cfg.discord_command_auth_mode or "enforce").strip().lower()
        return raw_mode if raw_mode in {"audit", "enforce"} else "enforce"

    def _bump_runtime_counter(self, attr_name: str):
        if not self.runtime or not hasattr(self.runtime, attr_name):
            return
        setattr(self.runtime, attr_name, int(getattr(self.runtime, attr_name, 0)) + 1)

    @staticmethod
    def _safe_id(raw_value: object) -> str:
        if raw_value is None:
            return ""
        return str(raw_value).strip()

    @staticmethod
    def _extract_role_ids(actor: object) -> set[str]:
        roles = getattr(actor, "roles", None)
        if not isinstance(roles, list):
            return set()

        role_ids: set[str] = set()
        for role in roles:
            role_id = getattr(role, "id", role)
            text = str(role_id).strip()
            if text:
                role_ids.add(text)
        return role_ids

    @staticmethod
    def _coerce_id_values(raw_values: object) -> set[str]:
        if not isinstance(raw_values, (list, tuple, set)):
            return set()
        return {str(value).strip() for value in raw_values if str(value).strip()}

    def _is_authorized(self, user_id: str, role_ids: set[str], channel_id: str) -> bool:
        allowed_users = self._coerce_id_values(self.cfg.discord_allowed_user_ids)
        allowed_roles = self._coerce_id_values(self.cfg.discord_allowed_role_ids)
        allowed_channels = self._coerce_id_values(self.cfg.discord_allowed_channel_ids)

        actor_allowed = user_id in allowed_users or bool(role_ids & allowed_roles)
        channel_allowed = channel_id in allowed_channels
        return actor_allowed and channel_allowed

    async def _respond_interaction(self, interaction, text: str):
        response = getattr(interaction, "response", None)
        is_done = False
        if response is not None:
            is_done_fn = getattr(response, "is_done", None)
            if callable(is_done_fn):
                try:
                    is_done = bool(is_done_fn())
                except Exception:
                    is_done = False

        if not is_done and response is not None and hasattr(response, "send_message"):
            await response.send_message(text, ephemeral=True)
            return

        followup = getattr(interaction, "followup", None)
        if followup is not None and hasattr(followup, "send"):
            await followup.send(text, ephemeral=True)

    async def _authorize_message(self, message) -> bool:
        user_id = self._safe_id(getattr(getattr(message, "author", None), "id", None))
        role_ids = self._extract_role_ids(getattr(message, "author", None))
        channel_id = self._safe_id(
            getattr(message, "channel_id", None)
            or getattr(getattr(message, "channel", None), "id", None)
        )
        authorized = self._is_authorized(user_id, role_ids, channel_id)
        if authorized:
            return True

        self._bump_runtime_counter("auth_denied_commands")
        log.warning(
            "Discord command auth denied: mode=%s user=%s channel=%s roles=%s",
            self.auth_mode,
            user_id or "unknown",
            channel_id or "unknown",
            ",".join(sorted(role_ids)) if role_ids else "none",
        )
        if self.auth_mode == "audit":
            return True

        await message.reply("You are not authorized to run this command in this channel.")
        return False

    async def _authorize_interaction(self, interaction) -> bool:
        user_id = self._safe_id(getattr(getattr(interaction, "user", None), "id", None))
        role_ids = self._extract_role_ids(getattr(interaction, "user", None))
        channel_id = self._safe_id(
            getattr(interaction, "channel_id", None)
            or getattr(getattr(interaction, "channel", None), "id", None)
        )
        authorized = self._is_authorized(user_id, role_ids, channel_id)
        if authorized:
            return True

        self._bump_runtime_counter("auth_denied_interactions")
        log.warning(
            "Discord interaction auth denied: mode=%s user=%s channel=%s roles=%s",
            self.auth_mode,
            user_id or "unknown",
            channel_id or "unknown",
            ",".join(sorted(role_ids)) if role_ids else "none",
        )
        if self.auth_mode == "audit":
            return True

        await self._respond_interaction(
            interaction,
            "You are not authorized to perform this action in this channel.",
        )
        return False

    def _get_pending_record(self, tweet_id: str):
        pending = get_pending(self.db, tweet_id)
        if not pending or pending[0] is None:
            return None
        replies = pending[0]
        if not isinstance(replies, list) or not replies:
            return None
        return pending

    def _is_pending_context_valid(self, pending_msg_id: str | None, pending_ch_id: str | None, interaction) -> bool:
        if not self.cfg.discord_require_pending_channel_match:
            return True

        current_ch = self._safe_id(
            getattr(interaction, "channel_id", None)
            or getattr(getattr(interaction, "channel", None), "id", None)
        )
        current_msg = self._safe_id(getattr(getattr(interaction, "message", None), "id", None))
        pending_channel = self._safe_id(pending_ch_id)
        pending_message = self._safe_id(pending_msg_id)

        if pending_channel and current_ch and pending_channel != current_ch:
            self._bump_runtime_counter("pending_channel_mismatch_denied")
            return False
        if pending_message and current_msg and pending_message != current_msg:
            self._bump_runtime_counter("pending_channel_mismatch_denied")
            return False
        return True

    def _is_pending_channel_valid_for_message(self, pending_ch_id: str | None, message) -> bool:
        if not self.cfg.discord_require_pending_channel_match:
            return True

        pending_channel = self._safe_id(pending_ch_id)
        current_channel = self._safe_id(
            getattr(message, "channel_id", None)
            or getattr(getattr(message, "channel", None), "id", None)
        )
        if pending_channel and current_channel and pending_channel != current_channel:
            self._bump_runtime_counter("pending_channel_mismatch_denied")
            return False
        return True

    async def run(self):
        """Run the Discord gateway connection for receiving interactions."""
        try:
            import discord
            intents = discord.Intents.default()
            intents.message_content = True

            client = discord.Client(intents=intents)

            @client.event
            async def on_ready():
                log.info(f"Discord bot connected as {client.user}")

            @client.event
            async def on_interaction(interaction):
                if interaction.type.value == 3:  # MESSAGE_COMPONENT
                    await self._handle_component(interaction)

            @client.event
            async def on_message(message):
                if message.author.bot:
                    return
                await self._handle_message(message)

            await client.start(self.cfg.discord_bot_token)
        except ImportError:
            log.warning("discord.py not installed, falling back to HTTP polling")
            await self._poll_interactions()

    async def _handle_component(self, interaction):
        """Handle button press interactions."""
        if not await self._authorize_interaction(interaction):
            return

        custom_id = interaction.data.get("custom_id", "")
        parts = custom_id.split(":")
        action = parts[0] if parts else ""
        if not action:
            await self._respond_interaction(interaction, "Malformed action payload.")
            return

        if action == "approve":
            if len(parts) != 3:
                await self._respond_interaction(interaction, "Malformed approval payload.")
                return

            tweet_id = parts[1]
            try:
                reply_idx = int(parts[2])
            except ValueError:
                await self._respond_interaction(interaction, "Invalid reply option selected.")
                return

            pending = self._get_pending_record(tweet_id)
            if not pending:
                await self._respond_interaction(interaction, "Tweet is no longer pending approval.")
                return

            replies, msg_id, ch_id, _ = pending
            if not self._is_pending_context_valid(msg_id, ch_id, interaction):
                await self._respond_interaction(interaction, "This action is not valid from this message.")
                return
            if not (0 <= reply_idx < len(replies)):
                await self._respond_interaction(interaction, "Invalid reply option selected.")
                return

            reply_text = replies[reply_idx]
            await interaction.response.send_message("⏳ Posting reply...", ephemeral=True)
            success = await post_reply_to_x(self.cfg, tweet_id, reply_text)
            if success:
                mark_replied(self.db, tweet_id, reply_text)
                await interaction.message.edit(
                    content=f"✅ **Reply posted!**\n> {reply_text}",
                    embeds=interaction.message.embeds,
                    view=None,
                )
                row = self.db.execute(
                    "SELECT tweet_url, author FROM processed_tweets WHERE tweet_id=?",
                    (tweet_id,),
                ).fetchone()
                if row:
                    await self.bot.log_approved(tweet_id, row[0], reply_text, row[1])
                await interaction.followup.send("✅ Reply posted successfully!", ephemeral=True)
            else:
                await interaction.followup.send(
                    "❌ Failed to post reply. Check X credentials.",
                    ephemeral=True,
                )
            return

        if action == "reject":
            if len(parts) != 2:
                await self._respond_interaction(interaction, "Malformed rejection payload.")
                return

            tweet_id = parts[1]
            pending = self._get_pending_record(tweet_id)
            if not pending:
                await self._respond_interaction(interaction, "Tweet is no longer pending approval.")
                return

            _, msg_id, ch_id, _ = pending
            if not self._is_pending_context_valid(msg_id, ch_id, interaction):
                await self._respond_interaction(interaction, "This action is not valid from this message.")
                return

            mark_rejected(self.db, tweet_id)
            await interaction.response.send_message("⏭️ Skipped.", ephemeral=True)
            await interaction.message.edit(
                content="⏭️ **Skipped**",
                embeds=interaction.message.embeds,
                view=None,
            )
            row = self.db.execute(
                "SELECT tweet_url, author FROM processed_tweets WHERE tweet_id=?",
                (tweet_id,),
            ).fetchone()
            if row:
                await self.bot.log_rejected(tweet_id, row[0], row[1])
            return

        if action == "custom":
            if len(parts) != 2:
                await self._respond_interaction(interaction, "Malformed custom-reply payload.")
                return

            tweet_id = parts[1]
            pending = self._get_pending_record(tweet_id)
            if not pending:
                await self._respond_interaction(interaction, "Tweet is no longer pending approval.")
                return

            _, msg_id, ch_id, _ = pending
            if not self._is_pending_context_valid(msg_id, ch_id, interaction):
                await self._respond_interaction(interaction, "This action is not valid from this message.")
                return

            await interaction.response.send_message(
                f"✏️ Send a custom reply with:\n`!reply {tweet_id} Your reply text`",
                ephemeral=True,
            )
            return

        await self._respond_interaction(interaction, "Unknown action.")

    async def _handle_message(self, message):
        content = message.content
        is_command = (
            content.startswith("!reply ")
            or content.startswith("!ingest ")
            or content.startswith("!smoke")
            or content == "!status"
            or content == "!stats"
        )
        if not is_command:
            return

        if not await self._authorize_message(message):
            return

        if content.startswith("!reply "):
            await self._handle_custom_reply(message)
        elif content.startswith("!ingest "):
            await self._handle_manual_ingest(message)
        elif content.startswith("!smoke"):
            await self._handle_smoke_test(message)
        elif content == "!status":
            await self._handle_status(message)
        elif content == "!stats":
            await self.bot.send_stats()

    async def _handle_custom_reply(self, message):
        parts = message.content.split(" ", 2)
        if len(parts) < 3:
            await message.reply("Usage: `!reply <tweet_id> <your reply text>`")
            return

        tweet_id = parts[1]
        reply_text = parts[2]

        if len(reply_text) > 280:
            await message.reply(f"⚠️ {len(reply_text)} chars — must be ≤280")
            return

        pending = self._get_pending_record(tweet_id)
        if not pending:
            self._bump_runtime_counter("custom_reply_missing_pending")
            await message.reply("❌ Failed: no pending approval found for this tweet.")
            return

        _, _, pending_ch_id, _ = pending
        if not self._is_pending_channel_valid_for_message(pending_ch_id, message):
            await message.reply("❌ Failed: custom reply must be sent in the original review channel.")
            return

        success = await post_reply_to_x(self.cfg, tweet_id, reply_text)
        if success:
            mark_replied(self.db, tweet_id, reply_text)
            await message.reply("✅ Custom reply posted!")

            row = self.db.execute(
                "SELECT tweet_url, author FROM processed_tweets WHERE tweet_id=?",
                (tweet_id,)
            ).fetchone()
            if row:
                await self.bot.log_approved(tweet_id, row[0], reply_text, row[1])
        else:
            await message.reply("❌ Failed to post. Check X credentials.")

    async def _handle_status(self, message):
        stats = get_stats(self.db)
        cat_lines = "\n".join(f"  {k}: {v}" for k, v in stats["by_category"].items())
        await message.reply(
            f"📊 **Bot Status**\n"
            f"Total: {stats['total_processed']} | Replied: {stats['replied']} | "
            f"Rejected: {stats['rejected']} | Pending: {stats['pending']}\n"
            f"By category:\n{cat_lines}"
        )

    async def _handle_smoke_test(self, message):
        parts = message.content.split(" ", 1)
        category = parse_smoke_category(parts[1] if len(parts) > 1 else None)
        if category is None:
            await message.reply("Usage: `!smoke [brand|competitor|seekers]`")
            return

        tweet, analysis = build_smoke_test_payload(category)
        if not await queue_candidate_for_review(self.db, self.bot, tweet, analysis):
            mark_rejected(self.db, tweet.tweet_id)
            await message.reply("Could not queue the smoke test. Check the review channels.")
            return

        await message.reply(
            f"Smoke test queued in `{category.value}` as `{tweet.tweet_id}`. "
            "Approve is safe: smoke items always dry-run instead of posting to X."
        )

    async def _handle_manual_ingest(self, message):
        parts = message.content.split(" ", 2)
        if len(parts) < 3:
            await message.reply("Usage: `!ingest <brand|competitor|seekers> <tweet text>`")
            return

        category = parse_smoke_category(parts[1])
        if category is None:
            await message.reply("Usage: `!ingest <brand|competitor|seekers> <tweet text>`")
            return

        tweet = build_manual_candidate(category, parts[2])
        analysis = build_manual_ingest_analysis(category, tweet.text)

        if not await queue_candidate_for_review(self.db, self.bot, tweet, analysis):
            mark_rejected(self.db, tweet.tweet_id)
            await message.reply("Could not queue the manual ingest item. Check the review channels.")
            return

        await message.reply(
            f"Manual ingest queued in `{category.value}` as `{tweet.tweet_id}`. "
            "Approve is safe: manual items dry-run instead of posting to X."
        )

    async def _poll_interactions(self):
        """Fallback: poll for interactions if discord.py isn't available."""
        log.info("Running in HTTP poll mode (install discord.py for real-time)")
        while True:
            await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# X Reply Posting
# ---------------------------------------------------------------------------

async def post_reply_to_x(cfg: Config, tweet_id: str, reply_text: str) -> bool:
    import httpx

    if cfg.x_posting_dry_run or is_local_test_tweet_id(tweet_id):
        log.info(f"Dry-run X reply for {tweet_id}: {reply_text}")
        return True

    url = "https://x.com/i/api/graphql/znq7jUAqRjmPj7IszLem5Q/CreateTweet"
    headers = {
        "authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
        "x-csrf-token": cfg.x_csrf_token,
        "cookie": cfg.x_cookie,
        "content-type": "application/json",
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
    }
    payload = {
        "variables": {
            "tweet_text": reply_text,
            "reply": {"in_reply_to_tweet_id": tweet_id, "exclude_reply_user_ids": []},
            "dark_request": False,
            "media": {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
        },
        "features": {
            "communities_web_enable_tweet_community_results_fetch": True,
            "c9s_tweet_anatomy_moderator_badge_enabled": True,
            "tweetypie_unmention_optimization_enabled": True,
            "responsive_web_edit_tweet_api_enabled": True,
            "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
            "view_counts_everywhere_api_enabled": True,
            "longform_notetweets_consumption_enabled": True,
            "responsive_web_twitter_article_tweet_consumption_enabled": True,
            "tweet_awards_web_tipping_enabled": False,
            "creator_subscriptions_quote_tweet_preview_enabled": False,
            "longform_notetweets_rich_text_read_enabled": True,
            "longform_notetweets_inline_media_enabled": True,
            "articles_preview_enabled": True,
            "rweb_video_timestamps_enabled": True,
            "rweb_tipjar_consumption_enabled": True,
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "freedom_of_speech_not_reach_fetch_enabled": True,
            "standardized_nudges_misinfo": True,
            "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "responsive_web_graphql_timeline_navigation_enabled": True,
            "responsive_web_enhance_cards_enabled": False,
        },
        "queryId": "znq7jUAqRjmPj7IszLem5Q",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code == 200:
            data = resp.json()
            if "errors" in data:
                log.error(f"X errors: {data['errors']}")
                return False
            log.info(f"Reply posted to {tweet_id}")
            return True
        else:
            log.error(f"X reply failed: {resp.status_code}")
            return False


# ---------------------------------------------------------------------------
# Telegram Fallback Notifier
# ---------------------------------------------------------------------------

async def telegram_notify(cfg: Config, message: str):
    """Send a notification to Telegram (used as fallback/alerts)."""
    if not cfg.telegram_enabled or not cfg.telegram_bot_token:
        return
    import httpx
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage",
            json={
                "chat_id": cfg.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML",
            },
        )


# ---------------------------------------------------------------------------
# Main Scan Loop
# ---------------------------------------------------------------------------

async def scan_and_notify(cfg: Config, db: sqlite3.Connection, api,
                          discord_bot: DiscordBot):
    all_candidates = []

    for sq in cfg.search_queries:
        for tab in ["Top", "Latest"]:
            log.info(f"Searching: '{sq.query}' ({tab})")
            try:
                tweets = await search_tweets(api, sq.query, tab=tab, limit=20)
                for t in tweets:
                    parsed = parse_tweet(t, tab, sq)
                    if parsed:
                        all_candidates.append(parsed)
            except Exception as e:
                log.error(f"Search failed '{sq.query}' ({tab}): {e}")
            await asyncio.sleep(2)

    # Deduplicate
    seen = set()
    unique = []
    for c in all_candidates:
        if c.tweet_id not in seen:
            seen.add(c.tweet_id)
            unique.append(c)

    candidates = filter_candidates(unique, cfg)
    log.info(f"Found {len(unique)} tweets, {len(candidates)} pass filters")

    processed_count = 0
    for tweet in candidates:
        if is_tweet_processed(db, tweet.tweet_id):
            continue

        log.info(f"Analyzing {tweet.tweet_id} by @{tweet.author_username}")

        analysis = await classify_and_generate(cfg, tweet)
        if not analysis:
            continue

        category = analysis.get("category", "irrelevant")
        if await queue_candidate_for_review(db, discord_bot, tweet, analysis):

            log.info(f"→ Sent to Discord #{category}: {tweet.tweet_id}")
            processed_count += 1

        await asyncio.sleep(1)

    if processed_count > 0:
        await discord_bot.send_status(
            f"🔍 Scan complete: {processed_count} new tweets sent for approval"
        )


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

async def scan_and_notify_provider(
    cfg: Config,
    db: sqlite3.Connection,
    search_client,
    discord_bot: DiscordBot,
    runtime: SearchRuntime,
):
    if cfg.search_provider == "manual_only":
        log.info("Live search disabled (SEARCH_PROVIDER=manual_only)")
        return

    def _log_no_candidates() -> None:
        if runtime.last_fetch_summary == "no_due_queries":
            log.info("No live-search queries were due this scan")
        elif runtime.last_fetch_summary.startswith("provider_paused:"):
            log.info("Live search is paused: %s", runtime.provider_pause_reason)
        elif runtime.last_fetch_summary == "zero_provider_results":
            log.info("%s queries ran but returned 0 candidates", cfg.search_provider)
        else:
            log.info("No candidates returned by %s", cfg.search_provider)

    if cfg.search_provider == "xai_x_search":
        prepared_candidates = await fetch_candidates_from_xai_x_search(
            cfg,
            search_client,
            runtime,
            discord_bot,
        )
        if not prepared_candidates:
            _log_no_candidates()
            return

        seen_ids = set()
        queued_count = 0
        discarded: list[tuple[str, float, str]] = []

        for prepared in prepared_candidates:
            tweet = prepared.tweet
            if tweet.tweet_id in seen_ids:
                runtime.duplicates_dropped += 1
                discarded.append((tweet.tweet_id, tweet.local_score, "duplicate_in_scan"))
                continue
            if is_tweet_processed(db, tweet.tweet_id):
                runtime.duplicates_dropped += 1
                discarded.append((tweet.tweet_id, tweet.local_score, "already_processed"))
                continue

            seen_ids.add(tweet.tweet_id)

            if queued_count >= cfg.max_discord_approvals_per_scan:
                runtime.locally_filtered_out += 1
                discarded.append((tweet.tweet_id, tweet.local_score, "trimmed_by_cap"))
                continue

            if await queue_candidate_for_review(db, discord_bot, tweet, prepared.analysis):
                queued_count += 1
                runtime.queued_to_discord += 1

            await asyncio.sleep(1)

        log.info(
            "Grok prepared %s candidate(s), %s queued to Discord",
            len(prepared_candidates),
            queued_count,
        )

        discarded_lines = _format_discarded_candidates(discarded)
        if discarded_lines:
            log.info("Top discarded candidates: %s", " | ".join(discarded_lines))
            if cfg.debug_discarded_to_status:
                await discord_bot.send_status(
                    "Discarded sample: " + " | ".join(discarded_lines)
                )

        if queued_count > 0:
            await discord_bot.send_status(
                f"Scan complete via {cfg.search_provider}: {queued_count} new tweets sent for approval"
            )
        return

    all_candidates: list[TweetCandidate] = []
    runtime.last_fetch_summary = ""

    if cfg.search_provider == "twitterapi_io":
        all_candidates = await fetch_candidates_from_twitterapi_io(
            cfg,
            search_client,
            runtime,
            discord_bot,
        )
    else:
        search_jobs = select_due_queries(
            cfg,
            runtime,
            cfg.max_api_requests_per_scan,
            brand_direct_enabled=False,
        )
        for job in search_jobs:
            runtime.last_query_run[job.query.query] = time.time()
            provider_query = normalize_search_query(cfg, job.query)
            log.info("Searching: '%s' (%s)", provider_query, job.query_type)
            try:
                tweets = await search_tweets(
                    search_client,
                    provider_query,
                    tab=job.query_type,
                    limit=20,
                )
                tweets = tweets[:20]
                runtime.api_requests_made += 1
                runtime.tweets_fetched += len(tweets)
                parsed_count = 0
                for tweet in tweets:
                    parsed = parse_tweet(tweet, job.query_type, job.query)
                    if parsed:
                        all_candidates.append(parsed)
                        parsed_count += 1

                runtime.empty_scan_counts[job.query.query] = (
                    0
                    if parsed_count > 0
                    else runtime.empty_scan_counts.get(job.query.query, 0) + 1
                )
            except Exception as exc:
                log.error(f"Search failed '{job.query.query}' ({job.query_type}): {exc}")
            await asyncio.sleep(1)
        if not search_jobs:
            runtime.last_fetch_summary = "no_due_queries"
        elif not all_candidates:
            runtime.last_fetch_summary = "zero_provider_results"
        else:
            runtime.last_fetch_summary = f"candidates:{len(all_candidates)}"

    if not all_candidates:
        _log_no_candidates()
        return

    seen_ids = set()
    scored_candidates: list[TweetCandidate] = []
    discarded: list[tuple[str, float, str]] = []

    for tweet in all_candidates:
        tweet.local_score = score_candidate(tweet)

        if tweet.tweet_id in seen_ids or is_tweet_processed(db, tweet.tweet_id):
            runtime.duplicates_dropped += 1
            discarded.append((tweet.tweet_id, tweet.local_score, "already_processed"))
            continue

        seen_ids.add(tweet.tweet_id)

        if tweet.age_minutes > cfg.max_tweet_age_minutes:
            runtime.locally_filtered_out += 1
            discarded.append((tweet.tweet_id, tweet.local_score, "too_old"))
            continue

        score_floor = _candidate_score_threshold(tweet)
        if score_floor is not None and tweet.local_score < score_floor:
            runtime.locally_filtered_out += 1
            discarded.append((tweet.tweet_id, tweet.local_score, "below_threshold"))
            continue

        scored_candidates.append(tweet)

    scored_candidates.sort(key=lambda item: item.local_score, reverse=True)

    if len(scored_candidates) > cfg.max_local_candidates_per_scan:
        runtime.locally_filtered_out += (
            len(scored_candidates) - cfg.max_local_candidates_per_scan
        )
        for tweet in scored_candidates[cfg.max_local_candidates_per_scan:]:
            discarded.append((tweet.tweet_id, tweet.local_score, "trimmed_by_cap"))
    local_candidates = scored_candidates[:cfg.max_local_candidates_per_scan]

    if len(local_candidates) > cfg.max_ai_candidates_per_scan:
        runtime.locally_filtered_out += (
            len(local_candidates) - cfg.max_ai_candidates_per_scan
        )
        for tweet in local_candidates[cfg.max_ai_candidates_per_scan:]:
            discarded.append((tweet.tweet_id, tweet.local_score, "trimmed_by_cap"))
    ai_candidates = local_candidates[:cfg.max_ai_candidates_per_scan]

    log.info(
        "Found %s raw candidates, %s after scoring, %s sent to Gemini",
        len(all_candidates),
        len(local_candidates),
        len(ai_candidates),
    )

    discarded_lines = _format_discarded_candidates(discarded)
    if discarded_lines:
        log.info("Top discarded candidates: %s", " | ".join(discarded_lines))
        if cfg.debug_discarded_to_status:
            await discord_bot.send_status(
                "Discarded sample: " + " | ".join(discarded_lines)
            )

    queued_count = 0
    for tweet in ai_candidates:
        if queued_count >= cfg.max_discord_approvals_per_scan:
            break

        log.info(
            "Analyzing %s by @%s (score %.1f)",
            tweet.tweet_id,
            tweet.author_username,
            tweet.local_score,
        )
        runtime.sent_to_gemini += 1
        analysis = await classify_and_generate(cfg, tweet)
        if not analysis:
            continue

        if await queue_candidate_for_review(db, discord_bot, tweet, analysis):
            queued_count += 1
            runtime.queued_to_discord += 1

        await asyncio.sleep(1)

    if queued_count > 0:
        await discord_bot.send_status(
            f"Scan complete via {cfg.search_provider}: {queued_count} new tweets sent for approval"
        )

async def main():
    cfg = load_config()

    valid_providers = {"twitterapi_io", "xai_x_search", "twscrape", "manual_only"}
    if cfg.search_provider not in valid_providers:
        log.error(
            "Unsupported SEARCH_PROVIDER '%s'. Use one of: %s",
            cfg.search_provider,
            ", ".join(sorted(valid_providers)),
        )
        return

    missing = []
    if not cfg.discord_bot_token:
        missing.append("DISCORD_BOT_TOKEN")
    if cfg.search_provider not in {"manual_only", "xai_x_search"} and not cfg.gemini_api_key:
        missing.append("GEMINI_API_KEY")
    if cfg.search_provider == "twitterapi_io" and not cfg.twitterapi_io_api_key:
        missing.append("TWITTERAPI_IO_API_KEY")
    if cfg.search_provider == "xai_x_search" and not cfg.xai_api_key:
        missing.append("XAI_API_KEY")
    if cfg.search_provider == "twscrape" and not cfg.twscrape_username:
        missing.append("TWSCRAPE_USERNAME")

    auth_mode = (cfg.discord_command_auth_mode or "enforce").strip().lower()
    if auth_mode not in {"audit", "enforce"}:
        missing.append("DISCORD_COMMAND_AUTH_MODE")
    elif auth_mode == "enforce":
        if not (cfg.discord_allowed_user_ids or cfg.discord_allowed_role_ids):
            missing.append("DISCORD_ALLOWED_USER_IDS or DISCORD_ALLOWED_ROLE_IDS")
        if not cfg.discord_allowed_channel_ids:
            missing.append("DISCORD_ALLOWED_CHANNEL_IDS")

    if missing:
        log.error(f"Missing required env vars: {', '.join(missing)}")
        log.error("Copy .env.example to .env and fill in credentials")
        return

    runtime = SearchRuntime()
    db = init_db(cfg.db_path)

    # Setup Discord
    discord_bot = DiscordBot(cfg, db, runtime)
    if cfg.discord_guild_id:
        await discord_bot.setup_channels()

    search_client = None
    if cfg.search_provider == "twitterapi_io":
        log.info("Setting up twitterapi.io search provider...")
        search_client = TwitterApiIoClient(cfg.twitterapi_io_api_key)
    elif cfg.search_provider == "xai_x_search":
        log.info("Setting up xAI X Search provider...")
        search_client = XaiClient(
            cfg.xai_api_key,
            timeout_seconds=cfg.xai_request_timeout_seconds,
        )
    elif cfg.search_provider == "twscrape":
        log.info("Setting up twscrape...")
        try:
            search_client = await setup_twscrape(cfg)
        except Exception as e:
            log.error(f"twscrape failed: {e}")
    else:
        log.info("Running in manual-only mode; live search is disabled.")

    # Discord gateway (handles button interactions)
    gateway = DiscordGateway(cfg, db, discord_bot, runtime)

    await discord_bot.send_status("🚀 **Yara.cash bot started!**")

    log.info("🚀 Bot started!")
    log.info(f"   {len(cfg.search_queries)} search queries configured")
    log.info(f"   Poll interval: {cfg.poll_interval}s")
    log.info(f"   Search provider: {cfg.search_provider}")

    async def scan_loop():
        manual_mode_notified = False
        scans_paused = False
        lockout_notified = False
        last_lock_reset = 0.0
        while True:
            try:
                if cfg.search_provider == "manual_only":
                    if not manual_mode_notified:
                        manual_mode_notified = True
                        await discord_bot.send_status(
                            "Live search is disabled (`SEARCH_PROVIDER=manual_only`). "
                            "Use `!smoke` or `!ingest` to test the workflow."
                        )
                elif cfg.search_provider == "twscrape" and (
                    not search_client or not await has_active_twscrape_account(search_client)
                ):
                    if not scans_paused:
                        scans_paused = True
                        lockout_notified = False
                        log.warning("Skipping scans: no active twscrape account")
                        await discord_bot.send_status(
                            "Scans paused: no active twscrape account. "
                            "Set TWSCRAPE_COOKIES from a logged-in burner X account, "
                            "or fix the twscrape login credentials."
                        )
                else:
                    if cfg.search_provider == "twscrape":
                        if scans_paused:
                            scans_paused = False
                            await discord_bot.send_status("twscrape session detected. Scans resumed.")
                        stats = await search_client.pool.stats()
                        locked = stats.get("locked_SearchTimeline", 0)
                        active = stats.get("active", 0)
                        if active > 0 and locked >= active:
                            now_ts = time.time()
                            if (
                                cfg.twscrape_auto_reset_locks
                                and now_ts - last_lock_reset >= cfg.twscrape_lock_reset_cooldown
                            ):
                                await search_client.pool.reset_locks()
                                last_lock_reset = now_ts
                                lockout_notified = False
                                log.warning("Reset twscrape SearchTimeline locks; retrying search")
                                await discord_bot.send_status(
                                    "twscrape search locks were reset automatically after a parser failure. "
                                    "Retrying search now."
                                )
                            else:
                                if not lockout_notified:
                                    lockout_notified = True
                                    await discord_bot.send_status(
                                        "twscrape search is locked. This usually means X changed its scripts. "
                                        "The bot will keep retrying automatic lock resets."
                                    )
                                log.warning("Skipping scan: twscrape SearchTimeline is locked")
                                log.info(f"Next scan in {cfg.poll_interval}s...")
                                await asyncio.sleep(cfg.poll_interval)
                                continue
                        lockout_notified = False

                    await scan_and_notify_provider(
                        cfg,
                        db,
                        search_client,
                        discord_bot,
                        runtime,
                    )
            except Exception as e:
                log.error(f"Scan error: {e}\n{traceback.format_exc()}")
                await discord_bot.send_status(f"⚠️ Scan error: {e}")
            log.info(f"Next scan in {cfg.poll_interval}s...")
            await asyncio.sleep(cfg.poll_interval)

    async def stats_loop():
        """Post stats every 6 hours."""
        while True:
            await asyncio.sleep(21600)
            try:
                await discord_bot.send_stats()
            except Exception:
                pass

    await asyncio.gather(
        gateway.run(),
        scan_loop(),
        stats_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
