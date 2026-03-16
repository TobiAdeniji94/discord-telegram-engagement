# Yara.cash Twitter Intelligence Bot

A Discord-driven social listening and response bot for Yara.cash.

The bot monitors X/Twitter for high-signal tweets, routes candidates into Discord for human review, and can post approved replies back to X. Depending on the search provider, it either uses Gemini after raw search (`twitterapi_io`, `twscrape`) or uses Grok end-to-end (`xai_x_search`).

## What It Does

- Watches X/Twitter for:
  - competitor complaints
  - solution-seeker intent
  - brand mentions
- Scores tweets locally before sending them to Gemini
- Sends approved candidates into Discord review channels
- Lets an operator approve, skip, or send a custom reply
- Posts approved replies to X
- Stores state in SQLite so tweets are not reprocessed after review

## Current Search Providers

The bot supports four search modes:

- `twitterapi_io`
  - Default production read path
  - Uses `twitterapi.io` keyword search and optional direct mention polling
- `xai_x_search`
  - Uses xAI Responses + the `x_search` tool
  - Grok handles discovery, classification, and reply drafting
  - The bot validates cited X URLs before queueing candidates
- `twscrape`
  - Legacy fallback scraper
  - More brittle and kept mainly for compatibility
- `manual_only`
  - Disables live search
  - Useful for workflow testing with Discord commands

## Project Layout

```
twitter_intel/
├── bot_legacy.py              # Production entry point (monolith)
├── pyproject.toml             # Python packaging and dependencies
├── Dockerfile                 # Container image
├── docker-compose.yml         # Local deployment
├── .env.example               # Environment template
│
├── src/twitter_intel/         # Modular architecture (v2)
│   ├── main.py                # New entry point (WIP)
│   ├── config/                # Settings, search queries, env utilities
│   ├── domain/
│   │   ├── entities/          # TweetCandidate, TweetCategory
│   │   ├── interfaces/        # Abstract base classes
│   │   └── services/          # Scoring, filtering
│   ├── infrastructure/
│   │   ├── database/          # SQLite repository
│   │   ├── search/            # TwitterAPI.io, xAI, twscrape
│   │   ├── ai/                # Gemini classifier, prompts
│   │   └── notifications/     # Discord, Telegram
│   ├── application/           # Container (dependency injection)
│   └── exceptions/            # Error hierarchy
│
└── tests/                     # Unit and integration tests
    ├── unit/
    └── integration/
```

### Architecture Status

The codebase has been refactored from a 3,400-line monolith into a clean modular architecture:

| Component | Status | Location |
|-----------|--------|----------|
| Configuration | ✅ Extracted | `src/twitter_intel/config/` |
| Domain entities | ✅ Extracted | `src/twitter_intel/domain/entities/` |
| Interfaces | ✅ Extracted | `src/twitter_intel/domain/interfaces/` |
| Scoring service | ✅ Extracted | `src/twitter_intel/domain/services/` |
| Database layer | ✅ Extracted | `src/twitter_intel/infrastructure/database/` |
| Search providers | ✅ Extracted | `src/twitter_intel/infrastructure/search/` |
| AI classifiers | ✅ Extracted | `src/twitter_intel/infrastructure/ai/` |
| Notifications | ✅ Extracted | `src/twitter_intel/infrastructure/notifications/` |
| Scan loop | ⏳ In bot_legacy.py | Orchestration not yet migrated |
| Discord Gateway | ⏳ In bot_legacy.py | Button interactions not yet migrated |

**For production use:** Run `bot_legacy.py` (the Dockerfile does this by default).

**For development:** The modular components are fully tested (216 tests) and can be imported from `twitter_intel.*`.

## Requirements

- Python 3.11+ (for local development) or Docker
- A Discord bot token
- A Gemini API key for live classification/reply generation unless you use `xai_x_search`
- One of:
  - a `twitterapi.io` API key
  - an xAI API key
  - `twscrape` credentials/cookies
- X session cookies if you want approved replies posted back to X

### Dependencies

Dependencies are managed in `pyproject.toml`. For Docker deployment, they're installed automatically. For local development:

```bash
pip install -e .          # Production dependencies
pip install -e ".[dev]"   # Include dev/test dependencies
```

## Configuration

Copy the env template:

```powershell
Copy-Item .env.example .env
```

The main settings are:

- `SEARCH_PROVIDER`
  - `twitterapi_io`, `xai_x_search`, `twscrape`, or `manual_only`
- `TWITTERAPI_IO_API_KEY`
  - Required when `SEARCH_PROVIDER=twitterapi_io`
- `XAI_API_KEY`
  - Required when `SEARCH_PROVIDER=xai_x_search`
- `GEMINI_API_KEY`
  - Required for live classification unless `manual_only` or `xai_x_search`
- `DISCORD_BOT_TOKEN`
  - Required
- `DISCORD_GUILD_ID`
  - Optional but recommended for auto-creating channels
- `DISCORD_COMMAND_AUTH_MODE`
  - `enforce` blocks unauthorized command/button use (recommended)
  - `audit` logs denied attempts but allows execution
- `DISCORD_ALLOWED_USER_IDS` / `DISCORD_ALLOWED_ROLE_IDS`
  - At least one actor allowlist is required when `DISCORD_COMMAND_AUTH_MODE=enforce`
- `DISCORD_ALLOWED_CHANNEL_IDS`
  - Required when `DISCORD_COMMAND_AUTH_MODE=enforce`
- `DISCORD_REQUIRE_PENDING_CHANNEL_MATCH`
  - Keep `true` to bind button/custom-reply actions to original review context
- `X_CSRF_TOKEN` and `X_COOKIE`
  - Required if you want real posting to X

Useful search controls:

- `SEARCH_QUERIES`
- `SEARCH_SINCE_DAYS`
- `POLL_INTERVAL`
- `MAX_API_REQUESTS_PER_SCAN`
- `MAX_LOCAL_CANDIDATES_PER_SCAN`
- `MAX_AI_CANDIDATES_PER_SCAN`
- `MAX_DISCORD_APPROVALS_PER_SCAN`
- `DEBUG_DISCARDED_TO_STATUS`
- `XAI_MODEL`
- `XAI_MAX_TURNS`
- `XAI_REQUEST_TIMEOUT_SECONDS`

## Run With Docker

Build and start:

```powershell
docker compose up -d --build
```

Check logs:

```powershell
docker compose logs -f
```

Check status:

```powershell
docker compose ps
```

The container stores SQLite data in the named Docker volume mounted at `/app/data`.

## How The Bot Works

1. The scan loop selects due search queries based on each query's `cooldown_seconds`.
2. The provider returns up to 20 tweets per request. The bot does not paginate.
3. The bot parses and scores candidates locally.
   - In `xai_x_search`, Grok returns already-prepared candidates instead, so the bot skips local scoring and Gemini.
4. Tweets are discarded if they are:
   - too old
   - below the lane threshold
   - already processed
   - trimmed by candidate caps
5. Only the top candidates go to Gemini.
6. Only the top approved-to-review items are sent to Discord.
7. A human approves, skips, or writes a custom reply.
8. Approved replies are posted to X unless the item is a smoke/manual test or posting is in dry-run mode.

## Discord Commands

The bot responds to these commands in channels it can read:

- `!smoke`
  - Queue a synthetic test candidate in the default brand lane
- `!smoke brand`
- `!smoke competitor`
- `!smoke seekers`
  - Queue a synthetic test candidate in a specific lane
- `!ingest <brand|competitor|seekers> <tweet text>`
  - Inject a manual candidate without live search
- `!reply <tweet_id> <reply text>`
  - Send a custom reply for a pending item
- `!status`
  - Show high-level bot status from stored state
- `!stats`
  - Show database stats and search telemetry

### Discord Authorization Controls

The bot enforces authorization for commands and button interactions.

- Actor rule: `(allowed user OR allowed role) AND allowed channel`
- In strict mode, custom replies and button actions must match the original
  pending approval message/channel context.

Recommended production settings:

```env
DISCORD_COMMAND_AUTH_MODE=enforce
DISCORD_ALLOWED_USER_IDS=123456789012345678
DISCORD_ALLOWED_ROLE_IDS=234567890123456789
DISCORD_ALLOWED_CHANNEL_IDS=345678901234567890,456789012345678901
DISCORD_REQUIRE_PENDING_CHANNEL_MATCH=true
```

## Recommended Modes

### Local workflow testing

Use:

```env
SEARCH_PROVIDER=manual_only
```

Then test with:

- `!smoke`
- `!ingest competitor Example transfer is stuck`

### Production-style live search

Use:

```env
SEARCH_PROVIDER=twitterapi_io
TWITTERAPI_IO_API_KEY=...
GEMINI_API_KEY=...
```

### Grok X Search

Use:

```env
SEARCH_PROVIDER=xai_x_search
XAI_API_KEY=...
```

Notes:

- Grok uses xAI's server-side `x_search` tool, not a raw tweet API.
- The bot validates every `tweet_url` against xAI citations before queueing it.
- `GEMINI_API_KEY` is not required in this mode.

For lower rate-limit pressure, start with:

```env
POLL_INTERVAL=300
MAX_API_REQUESTS_PER_SCAN=1
SEARCH_SINCE_DAYS=0
```

## Rate Limiting

The bot now backs off on `twitterapi.io` `429` responses and respects `Retry-After` if the provider returns it.

To reduce rate-limit pressure:

- increase `POLL_INTERVAL`
- reduce `MAX_API_REQUESTS_PER_SCAN`
- increase per-query `cooldown_seconds`
- keep `SEARCH_SINCE_DAYS` narrow
- avoid overly broad competitor queries

## Troubleshooting

### `Running in manual-only mode; live search is disabled.`

Your `.env` has:

```env
SEARCH_PROVIDER=manual_only
```

Switch to `twitterapi_io`, `xai_x_search`, or `twscrape` and restart.

### `xAI auth failed`

Your xAI key is missing or invalid. Check:

- `XAI_API_KEY`

### `xAI rate limited`

The bot hit xAI Responses API limits.

Reduce request volume:

```env
POLL_INTERVAL=300
MAX_API_REQUESTS_PER_SCAN=1
XAI_MAX_TURNS=2
```

### `twitterapi.io rate limited`

Your scan cadence is too aggressive for the current plan limits.

Reduce request volume:

```env
POLL_INTERVAL=300
MAX_API_REQUESTS_PER_SCAN=1
```

### `X reply failed: 403`

Your X posting cookies are invalid or expired. Check:

- `X_CSRF_TOKEN`
- `X_COOKIE`

### `No live-search queries were due this scan`

This is normal. It means the scheduler skipped work because none of the queries had reached their `cooldown_seconds` yet.

### `queries ran but returned 0 candidates`

The provider was called, but no tweets matched after the current query and date window.

## Development

### Setup

```bash
# Create virtual environment
python -m venv .venv

# Activate (Windows PowerShell)
.venv\Scripts\Activate.ps1

# Activate (Unix)
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=twitter_intel

# Run specific test file
pytest tests/unit/domain/test_scoring.py
```

### Using Modular Components

The extracted components can be imported directly:

```python
from twitter_intel.config import load_config
from twitter_intel.domain.entities import TweetCandidate, TweetCategory
from twitter_intel.domain.services import score_candidate, filter_candidates
from twitter_intel.infrastructure.search import SearchProviderFactory
from twitter_intel.infrastructure.ai import GeminiClassifier
from twitter_intel.application.container import Container
```

### Code Quality

```bash
# Lint with ruff
ruff check src/ tests/

# Type check with mypy
mypy src/
```

### Secret Scanning

```bash
# Install and run pre-commit hooks (includes gitleaks)
pre-commit install
pre-commit run --all-files
```

```powershell
# Run secret scan manually
scripts\scan-secrets.ps1
```

Security operations checklist: see [`SECURITY.md`](SECURITY.md).

## Notes

- `manual` and `smoke` items always dry-run on approval and never post to X.
- The bot logs the top 3 discarded candidates with score and discard reason.
- If `DEBUG_DISCARDED_TO_STATUS=true`, the bot also sends that sample to `#bot-status`.
- In `xai_x_search`, Grok replaces Gemini for discovery, classification, and reply drafting.
- The current Gemini integration uses `google-generativeai`, which emits a deprecation warning at runtime. The code still works, but migration to `google.genai` is a future maintenance task.
