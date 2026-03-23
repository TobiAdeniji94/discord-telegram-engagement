"""
xAI live search helpers for the modular application.

Builds Grok x_search prompts, executes xAI requests, parses results into
PreparedReviewCandidate objects, and updates runtime telemetry.

Implements SRS-YARA-XSS-2026:
- Section 4.1.2: Prompt Design Requirements
- Section 5.1: Prompt Construction (FR-01 through FR-04)
- Section 5.2: API Invocation (FR-05 through FR-09)
- Section 5.3: Response Parsing (FR-10 through FR-12)
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from twitter_intel.config import Config, SearchJob, SearchQuery, SearchRuntime
from twitter_intel.config.brand_registry import BrandConfig, get_brand
from twitter_intel.domain.entities.category import TweetCategory
from twitter_intel.domain.entities.tweet import PreparedReviewCandidate, TweetCandidate
from twitter_intel.domain.interfaces import NotificationService
from twitter_intel.exceptions import XaiAuthError, XaiRateLimitError
from twitter_intel.infrastructure.search.xai_client import (
    XaiClient,
    build_x_search_tool_config,
)

log = logging.getLogger(__name__)
X_SNOWFLAKE_EPOCH_MS = 1288834974657
XAI_USAGE_WINDOW_SECONDS = 60


def _describe_exception(exc: BaseException) -> str:
    message = str(exc).strip()
    request = getattr(exc, "request", None)
    method = str(getattr(request, "method", "") or "").strip()
    url = str(getattr(request, "url", "") or "").strip()

    if message:
        prefix = exc.__class__.__name__
        if method and url:
            return f"{prefix}: {message} ({method} {url})"
        return f"{prefix}: {message}"

    if method and url:
        return f"{exc.__class__.__name__} while calling {method} {url}"

    return exc.__class__.__name__


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.strip():
            return int(float(value.strip()))
        return default
    except (TypeError, ValueError):
        return default


def _prune_xai_usage_events(
    runtime: SearchRuntime,
    now_ts: float | None = None,
    window_seconds: int = XAI_USAGE_WINDOW_SECONDS,
) -> None:
    cutoff = float(now_ts if now_ts is not None else time.time()) - max(1, window_seconds)
    events = getattr(runtime, "xai_recent_usage_events", None)
    if not isinstance(events, list):
        runtime.xai_recent_usage_events = []
        return
    runtime.xai_recent_usage_events = [
        event
        for event in events
        if isinstance(event, dict) and float(event.get("timestamp", 0.0) or 0.0) >= cutoff
    ]


def _append_xai_usage_event(
    runtime: SearchRuntime,
    *,
    timestamp: float | None = None,
    http_attempts: int = 0,
    prompt_tokens: int = 0,
    prompt_text_tokens: int = 0,
    completion_tokens: int = 0,
    reasoning_tokens: int = 0,
    cached_prompt_tokens: int = 0,
) -> None:
    now_ts = float(timestamp if timestamp is not None else time.time())
    events = getattr(runtime, "xai_recent_usage_events", None)
    if not isinstance(events, list):
        runtime.xai_recent_usage_events = []
        events = runtime.xai_recent_usage_events

    events.append(
        {
            "timestamp": now_ts,
            "http_attempts": max(0, int(http_attempts)),
            "prompt_tokens": max(0, int(prompt_tokens)),
            "prompt_text_tokens": max(0, int(prompt_text_tokens)),
            "completion_tokens": max(0, int(completion_tokens)),
            "reasoning_tokens": max(0, int(reasoning_tokens)),
            "cached_prompt_tokens": max(0, int(cached_prompt_tokens)),
        }
    )
    _prune_xai_usage_events(runtime, now_ts)


def record_xai_http_attempt(runtime: SearchRuntime, timestamp: float | None = None) -> None:
    runtime.xai_http_attempts_made = _coerce_int(
        getattr(runtime, "xai_http_attempts_made", 0)
    ) + 1
    _append_xai_usage_event(runtime, timestamp=timestamp, http_attempts=1)


def configured_xai_requests_per_scan(config: Config) -> int:
    request_budget = max(0, _coerce_int(getattr(config, "max_api_requests_per_scan", 0)))
    if request_budget <= 0:
        return 0
    try:
        jobs = select_due_queries(
            config,
            SearchRuntime(),
            request_budget,
            brand_direct_enabled=False,
        )
        return len(jobs)
    except Exception:
        return request_budget


def configured_xai_logical_rpm_ceiling(config: Config) -> float:
    poll_interval = max(1, _coerce_int(getattr(config, "poll_interval", 0), 1))
    requests_per_scan = configured_xai_requests_per_scan(config)
    return requests_per_scan * (60.0 / poll_interval)


def build_xai_telemetry_snapshot(
    config: Config,
    runtime: SearchRuntime,
    *,
    window_seconds: int = XAI_USAGE_WINDOW_SECONDS,
    now_ts: float | None = None,
) -> dict[str, object]:
    current_ts = float(now_ts if now_ts is not None else time.time())
    _prune_xai_usage_events(runtime, current_ts, window_seconds)
    events = getattr(runtime, "xai_recent_usage_events", [])

    http_attempts = 0
    prompt_tokens = 0
    prompt_text_tokens = 0
    completion_tokens = 0
    reasoning_tokens = 0
    cached_prompt_tokens = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        http_attempts += _coerce_int(event.get("http_attempts"))
        prompt_tokens += _coerce_int(event.get("prompt_tokens"))
        prompt_text_tokens += _coerce_int(event.get("prompt_text_tokens"))
        completion_tokens += _coerce_int(event.get("completion_tokens"))
        reasoning_tokens += _coerce_int(event.get("reasoning_tokens"))
        cached_prompt_tokens += _coerce_int(event.get("cached_prompt_tokens"))

    actual_tpm = prompt_tokens + completion_tokens + reasoning_tokens
    actual_rpm = http_attempts * (60.0 / max(1, window_seconds))
    configured_requests = configured_xai_requests_per_scan(config)
    configured_rpm = configured_xai_logical_rpm_ceiling(config)

    cache_denominator = prompt_text_tokens + cached_prompt_tokens
    cache_hit_pct = (
        (cached_prompt_tokens / cache_denominator) * 100.0
        if cache_denominator > 0
        else None
    )

    pause_remaining_seconds = max(
        0,
        int(float(getattr(runtime, "provider_paused_until", 0.0) or 0.0) - current_ts),
    )
    rpm_limit_value = _coerce_int(getattr(config, "xai_requests_per_minute_limit", None), 0)
    tpm_limit_value = _coerce_int(getattr(config, "xai_tokens_per_minute_limit", None), 0)
    raw_model = getattr(config, "xai_model", "")
    model_name = raw_model.strip() if isinstance(raw_model, str) and raw_model.strip() else "xai_x_search"

    return {
        "should_render": bool(
            str(getattr(config, "search_provider", "")).strip().lower() == "xai_x_search"
            or _coerce_int(getattr(runtime, "xai_requests_made", 0)) > 0
            or _coerce_int(getattr(runtime, "xai_http_attempts_made", 0)) > 0
            or rpm_limit_value > 0
            or tpm_limit_value > 0
        ),
        "model": model_name,
        "window_seconds": max(1, window_seconds),
        "configured_requests_per_scan": configured_requests,
        "configured_logical_rpm": configured_rpm,
        "http_attempt_rpm": actual_rpm,
        "actual_tpm": actual_tpm,
        "prompt_tpm": prompt_tokens,
        "completion_tpm": completion_tokens,
        "reasoning_tpm": reasoning_tokens,
        "cached_tpm": cached_prompt_tokens,
        "cache_hit_pct": cache_hit_pct,
        "total_http_attempts": _coerce_int(getattr(runtime, "xai_http_attempts_made", 0)),
        "total_logical_requests": _coerce_int(getattr(runtime, "xai_requests_made", 0)),
        "total_x_search_calls": _coerce_int(getattr(runtime, "xai_x_search_tool_calls", 0)),
        "rate_limit_hits": _coerce_int(getattr(runtime, "xai_rate_limit_hits", 0)),
        "pause_remaining_seconds": pause_remaining_seconds,
        "pause_reason": str(getattr(runtime, "provider_pause_reason", "") or "").strip(),
        "rpm_limit": rpm_limit_value or None,
        "tpm_limit": tpm_limit_value or None,
    }


def format_xai_telemetry_lines(
    config: Config,
    runtime: SearchRuntime,
    *,
    compact: bool = False,
) -> list[str]:
    snapshot = build_xai_telemetry_snapshot(config, runtime)
    if not bool(snapshot.get("should_render")):
        return []

    cache_hit_pct = snapshot.get("cache_hit_pct")
    cache_text = "n/a" if cache_hit_pct is None else f"{float(cache_hit_pct):.1f}%"
    pause_remaining = _coerce_int(snapshot.get("pause_remaining_seconds"))
    pause_reason = str(snapshot.get("pause_reason") or "").strip()
    pause_text = "none"
    if pause_remaining > 0:
        pause_text = f"{pause_remaining}s ({pause_reason or 'provider paused'})"

    limit_bits: list[str] = []
    rpm_limit = snapshot.get("rpm_limit")
    if rpm_limit is not None:
        rpm_value = float(snapshot.get("http_attempt_rpm") or 0.0)
        limit_bits.append(
            f"RPM {rpm_value:.2f}/{int(rpm_limit)} ({(rpm_value / max(1, int(rpm_limit))) * 100:.1f}%)"
        )
    tpm_limit = snapshot.get("tpm_limit")
    if tpm_limit is not None:
        tpm_value = _coerce_int(snapshot.get("actual_tpm"))
        limit_bits.append(
            f"TPM {tpm_value}/{int(tpm_limit)} ({(tpm_value / max(1, int(tpm_limit))) * 100:.1f}%)"
        )

    if compact:
        line = (
            f"xAI: {float(snapshot.get('http_attempt_rpm') or 0.0):.2f} HTTP RPM | "
            f"{_coerce_int(snapshot.get('actual_tpm'))} TPM | "
            f"cache {cache_text} | "
            f"ceiling {float(snapshot.get('configured_logical_rpm') or 0.0):.2f} logical RPM | "
            f"pause {pause_text}"
        )
        if limit_bits:
            line += " | limits " + ", ".join(limit_bits)
        return [line]

    lines = [
        "**xAI Telemetry**",
        f"Model: {snapshot['model']}",
        (
            "Configured ceiling: "
            f"{_coerce_int(snapshot.get('configured_requests_per_scan'))} req/scan | "
            f"{float(snapshot.get('configured_logical_rpm') or 0.0):.2f} logical RPM"
        ),
        (
            f"Last {snapshot['window_seconds']}s: "
            f"{float(snapshot.get('http_attempt_rpm') or 0.0):.2f} HTTP RPM | "
            f"{_coerce_int(snapshot.get('actual_tpm'))} TPM"
        ),
        (
            f"Tokens ({snapshot['window_seconds']}s): "
            f"prompt {_coerce_int(snapshot.get('prompt_tpm'))} | "
            f"completion {_coerce_int(snapshot.get('completion_tpm'))} | "
            f"reasoning {_coerce_int(snapshot.get('reasoning_tpm'))} | "
            f"cached {_coerce_int(snapshot.get('cached_tpm'))}"
        ),
        (
            f"Cache hit: {cache_text} | "
            f"HTTP attempts total: {_coerce_int(snapshot.get('total_http_attempts'))} | "
            f"logical requests total: {_coerce_int(snapshot.get('total_logical_requests'))} | "
            f"x_search calls total: {_coerce_int(snapshot.get('total_x_search_calls'))}"
        ),
        (
            f"Rate limits hit: {_coerce_int(snapshot.get('rate_limit_hits'))} | "
            f"Provider pause: {pause_text}"
        ),
    ]
    if limit_bits:
        lines.append("Configured team limits: " + " | ".join(limit_bits))
    return lines


async def fetch_candidates_from_xai_search(
    config: Config,
    client: XaiClient,
    runtime: SearchRuntime,
    notification_service: NotificationService,
) -> list[PreparedReviewCandidate]:
    """
    Fetch pre-classified review candidates from xAI's x_search tool.

    The helper selects due queries, executes one x_search request per lane,
    parses candidate references from Grok's response, and pauses the provider
    on auth/rate-limit failures.
    """
    now_ts = time.time()
    runtime.last_fetch_summary = ""
    runtime.last_xss_due_jobs = []

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

    request_budget = max(0, config.max_api_requests_per_scan)
    due_jobs = select_due_queries(
        config,
        runtime,
        request_budget,
        brand_direct_enabled=False,
    )
    runtime.last_xss_due_jobs = list(due_jobs)
    if not due_jobs:
        runtime.last_fetch_summary = "no_due_queries"
        return []

    prepared_candidates: list[PreparedReviewCandidate] = []
    remaining_budget = request_budget

    async def _pause_provider(reason: str, pause_seconds: int | None = None) -> None:
        actual_pause_seconds = max(1, pause_seconds or config.poll_interval)
        runtime.provider_paused_until = time.time() + actual_pause_seconds
        runtime.provider_pause_reason = reason
        runtime.last_fetch_summary = f"provider_paused:{actual_pause_seconds}"
        await notification_service.send_status(reason)

    for job in due_jobs:
        if remaining_budget <= 0:
            break

        runtime.last_query_run[job.query.query] = time.time()
        prompt = build_xai_search_prompt(config, job, runtime)
        tool_config = build_xai_tool_config_for_job(config, job, runtime)

        try:
            runtime.api_requests_made += 1
            runtime.xai_requests_made += 1
            remaining_budget -= 1

            log.info("Requesting Grok X Search for query '%s'", job.query.description)
            payload = await client.create_response(
                model=config.xai_model,
                prompt=prompt,
                tool_config=tool_config,
                max_turns=config.xai_max_turns,
                cache_key=job.query.lane_id or job.query.query,
                on_request_attempt=lambda: record_xai_http_attempt(runtime),
            )
            tool_names = _update_xai_usage_counters(runtime, payload)
            if config.xai_debug_log_tool_calls and tool_names:
                log.info("xAI tool calls: %s", ", ".join(tool_names))

            response_text = extract_output_text_from_xai_response(payload)
            job_candidates = parse_xai_candidates(payload, response_text, job)

            prepared_candidates.extend(job_candidates)

        except XaiAuthError as exc:
            log.error(str(exc))
            await _pause_provider("xAI auth failed. Check XAI_API_KEY before the next scan.")
            return prepared_candidates
        except XaiRateLimitError as exc:
            runtime.xai_rate_limit_hits = _coerce_int(getattr(runtime, "xai_rate_limit_hits", 0)) + 1
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
            log.error(
                "xAI search failed '%s': %s",
                job.query.description or job.query.query,
                _describe_exception(exc),
            )

    if not runtime.last_fetch_summary:
        runtime.last_fetch_summary = (
            "zero_provider_results"
            if not prepared_candidates
            else f"candidates:{len(prepared_candidates)}"
        )

    return prepared_candidates


def _search_date_window(
    search_since_days: int | None,
    runtime: SearchRuntime | None = None,
) -> tuple[str | None, str | None]:
    end_date = datetime.now(timezone.utc).date()
    start_date = (
        end_date - timedelta(days=search_since_days)
        if search_since_days is not None
        else end_date
    )

    catchup_start = getattr(runtime, "restart_catchup_start_utc", None)
    catchup_end = getattr(runtime, "restart_catchup_end_utc", None)
    if catchup_start:
        catchup_start_date = catchup_start.astimezone(timezone.utc).date()
        start_date = catchup_start_date if start_date is None else min(start_date, catchup_start_date)
    if catchup_end:
        end_date = max(end_date, catchup_end.astimezone(timezone.utc).date())

    return start_date.isoformat(), end_date.isoformat()


def _preferred_category_for_hint(category_hint: str) -> str:
    mapping = {
        "competitor_complaint": TweetCategory.COMPETITOR_COMPLAINT.value,
        "solution_seeker": TweetCategory.SOLUTION_SEEKER.value,
        "brand_mention": TweetCategory.BRAND_MENTION.value,
    }
    return mapping.get(category_hint, TweetCategory.BRAND_MENTION.value)


def _normalize_category_value(raw_value: object) -> str:
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


def _format_list(values: list[str]) -> str:
    return ", ".join(value for value in values if str(value or "").strip())


def _get_brand_for_query(query: SearchQuery) -> Optional[BrandConfig]:
    """Get brand configuration for a search query if applicable."""
    brand_family = str(query.brand_family or "").strip().lower()
    if brand_family:
        return get_brand(brand_family)
    return None


def build_xai_tool_config_for_job(
    config: Config,
    job: SearchJob,
    runtime: SearchRuntime | None = None,
) -> dict[str, object]:
    """
    Build the x_search tool config for a single lane.

    Complaint lanes exclude the brand's official handles. Solution-seeker lanes
    must not set excluded_x_handles. The API-level date window is always set.
    """
    from_date, to_date = _search_date_window(config.search_since_days, runtime)
    brand = _get_brand_for_query(job.query)

    excluded_handles: list[str] | None = None
    allowed_handles: list[str] | None = None

    if job.query.category_hint == "competitor_complaint" and brand:
        excluded_handles = list(brand.excluded_handles)
    elif job.query.category_hint == "solution_seeker":
        allowed_handles = config.xai_allowed_x_handles or None
    else:
        excluded_handles = config.xai_excluded_x_handles or None
        if not excluded_handles:
            allowed_handles = config.xai_allowed_x_handles or None

    return build_x_search_tool_config(
        enable_image_understanding=config.xai_enable_image_understanding,
        enable_video_understanding=config.xai_enable_video_understanding,
        excluded_handles=excluded_handles,
        allowed_handles=allowed_handles,
        start_date=from_date,
        end_date=to_date,
    )


def build_srs_compliant_prompt(
    query: SearchQuery,
    brand: Optional[BrandConfig] = None,
    max_tweet_age_minutes: int = 360,
) -> str:
    """
    Build SRS-compliant natural language semantic prompt.

    Implements SRS Section 4.1.2 and 5.1 Prompt Design Requirements:
    - FR-01: Natural-language semantic prompt, no boolean operators
    - FR-02: Max 500 characters
    - FR-03: Disambiguation context for ambiguous brands (Grey, Wise)
    - FR-04: Request tweet URL, text, author, timestamp

    Args:
        query: Search query configuration
        brand: Optional brand configuration for competitor complaints

    Returns:
        Natural language prompt under 500 characters
    """
    if query.category_hint == "competitor_complaint" and brand:
        return _build_competitor_complaint_prompt(brand, max_tweet_age_minutes)
    elif query.category_hint == "solution_seeker":
        return _build_solution_seeker_prompt(query, max_tweet_age_minutes)
    else:
        # Fallback to intent summary for other categories
        return query.intent_summary or query.description


def _format_recent_window_text(max_tweet_age_minutes: int) -> str:
    minutes = max(1, int(max_tweet_age_minutes or 1))
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"the last {hours} hour" if hours == 1 else f"the last {hours} hours"
    return f"the last {minutes} minutes"


def _build_competitor_complaint_prompt(
    brand: BrandConfig,
    max_tweet_age_minutes: int,
) -> str:
    """
    Build SRS-compliant prompt for competitor complaint retrieval.

    Per SRS Section 4.1.2:
    - Natural language, not boolean operators
    - Max 500 chars
    - Names brand, aliases, handles
    - Includes disambiguation for Grey/Wise
    - Specifies complaint categories
    - Instructs to ignore retweets, promos, spam, official posts
    """
    aliases_str = ", ".join(brand.aliases)
    recent_window = _format_recent_window_text(max_tweet_age_minutes)

    # Add disambiguation context for ambiguous brands (Grey, Wise)
    context = ""
    if brand.disambiguation_context:
        context = f" ({brand.disambiguation_context})"

    prompt = (
        f"Find recent X posts from real users complaining about {aliases_str}{context}. "
        f"Focus on posts from {recent_window} about failed transfers, pending payments, blocked accounts, verification issues, unexpected fees, app failures, or poor customer support. "
        "Skip official brand posts, retweets, promos, jokes, giveaways, and spam. "
        f"Return tweet URL, full text, author username, and timestamp."
    )

    # Ensure under 500 chars per SRS FR-02
    return prompt[:500] if len(prompt) > 500 else prompt


def _build_solution_seeker_prompt(
    query: SearchQuery,
    max_tweet_age_minutes: int,
) -> str:
    """
    Build SRS-compliant prompt for solution-seeker discovery.

    Per SRS Section 4.2.2:
    - Describes target persona (freelancer, remote worker)
    - Describes information-seeking behavior
    - Mentions platforms seekers commonly reference
    - Natural language, no boolean operators
    """
    geo_focus = ", ".join(query.geo_focus) if query.geo_focus else "Nigeria, Ghana, Africa"
    recent_window = _format_recent_window_text(max_tweet_age_minutes)

    prompt = (
        f"Find recent English X posts from freelancers, remote workers, or Upwork/Fiverr users "
        f"in {geo_focus} from {recent_window} seeking advice on receiving USD payments, comparing Payoneer, Wise, "
        f"or Grey, or asking for alternatives. Ignore promotions, giveaways, bot posts, and "
        f"brand marketing. Return tweet URL, full text, author username, and timestamp."
    )

    return prompt[:500] if len(prompt) > 500 else prompt


def _active_event_window_for_query(
    config: Config,
    query: SearchQuery,
) -> tuple[datetime, datetime] | None:
    if config.search_event_mode != "anchored" or not config.search_event_anchor_utc:
        return None

    brand_family = str(query.brand_family or "").strip().lower()
    if not brand_family or brand_family not in config.search_event_brands:
        return None

    lower = config.search_event_anchor_utc + timedelta(
        minutes=max(0, config.search_event_min_offset_minutes)
    )
    upper = config.search_event_anchor_utc + timedelta(
        minutes=max(1, config.search_event_max_offset_minutes)
    )
    return lower, upper


def _build_lane_context_lines(query: SearchQuery) -> list[str]:
    lines = [
        f"- Lane ID: {query.lane_id}",
        f"- Lane goal: {query.intent_summary or query.description}",
        f"- Query mode hint: {query.query_type}",
    ]

    if query.brand_family:
        lines.append(f"- Brand family: {query.brand_family}")
    if query.brand_aliases:
        lines.append(f"- Brand aliases to consider: {_format_list(query.brand_aliases)}")
    if query.brand_handles:
        lines.append(
            "- Handle context to consider in mentions, replies, and quote posts: "
            f"{_format_list(['@' + handle for handle in query.brand_handles])}"
        )
    if query.issue_focus:
        lines.append(f"- Issues to prioritize: {_format_list(query.issue_focus)}")
    if query.geo_focus:
        lines.append(f"- Geographic context: {_format_list(query.geo_focus)}")
    if query.query:
        lines.append(f"- Legacy query hint: {query.query}")

    return lines


def build_xai_search_prompt(
    config: Config,
    job: SearchJob,
    runtime: SearchRuntime | None = None,
) -> str:
    """
    Build the xAI prompt for a specific due search lane.

    Implements SRS Section 5.1 Prompt Construction:
    - FR-01: Natural-language semantic prompt
    - FR-02: Max 500 chars for core semantic prompt
    - FR-03: Disambiguation for Grey/Wise
    - FR-04: Request tweet URL, text, author, timestamp
    """
    brand = _get_brand_for_query(job.query)
    # The production x_search prompt must stay within the SRS length bound.
    return build_srs_compliant_prompt(
        job.query,
        brand,
        config.max_tweet_age_minutes,
    )


def build_manual_grok_prompt(
    config: Config,
    job: SearchJob,
    runtime: SearchRuntime | None = None,
) -> str:
    """
    Build a manual Grok test prompt from the same structured lane definition.
    """
    from_date, to_date = _search_date_window(config.search_since_days, runtime)
    lane_context = "\n".join(_build_lane_context_lines(job.query))
    preferred_category = _preferred_category_for_hint(job.query.category_hint)
    prompt_lines = [
        "Use x_search to test this lane manually for Yara.cash.",
        "",
        "Lane:",
        lane_context,
        f"- Preferred category: {preferred_category}",
        (
            f"- If your tool runner supports it, set x_search from_date={from_date} "
            f"and to_date={to_date}."
            if from_date and to_date
            else "- Use the current day window that matches your test scenario."
        ),
        f"- {_build_freshness_window_instruction(config.max_tweet_age_minutes, runtime)}",
    ]

    event_window = _active_event_window_for_query(config, job.query)
    if event_window:
        lower, upper = event_window
        prompt_lines.append(
            "- Keep only posts in the anchored-event window: "
            f"{lower.isoformat().replace('+00:00', 'Z')} to "
            f"{upper.isoformat().replace('+00:00', 'Z')}."
        )

    prompt_lines.extend(
        [
            "",
            "Rules:",
            "- Search semantically, not just by exact keyword overlap.",
            "- Consider replies and quote posts when the user's own text carries the complaint or intent.",
            "- Ignore spam, promos, memes, and retweets.",
            "- Do not return official brand-authored posts as candidates.",
            "- Return strict JSON with a candidates array.",
        ]
    )
    return "\n".join(prompt_lines)


def _build_freshness_window_instruction(
    max_tweet_age_minutes: int,
    runtime: SearchRuntime | None = None,
) -> str:
    """
    Tell xAI the exact rolling freshness window to enforce.
    """
    catchup_start = getattr(runtime, "restart_catchup_start_utc", None)
    catchup_end = getattr(runtime, "restart_catchup_end_utc", None)
    if catchup_start and catchup_end:
        return (
            "Restart catch-up is active for this scan. Only return posts whose "
            f"created_at_iso is >= {catchup_start.isoformat().replace('+00:00', 'Z')} "
            f"and <= {catchup_end.isoformat().replace('+00:00', 'Z')}."
        )

    max_age_minutes = max(1, int(max_tweet_age_minutes))
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    min_created_at = now_utc - timedelta(minutes=max_age_minutes)
    now_text = now_utc.isoformat().replace("+00:00", "Z")
    min_created_text = min_created_at.isoformat().replace("+00:00", "Z")
    return (
        f"Current UTC time is {now_text}. Only return posts whose created_at_iso "
        f"is within the last {max_age_minutes} minutes "
        f"(created_at_iso >= {min_created_text})."
    )


def extract_output_text_from_xai_response(payload: dict[str, object]) -> str:
    """
    Normalize xAI response payloads into a single text blob.
    """
    def _collect_from_part(part: dict[str, object]) -> list[str]:
        texts: list[str] = []
        part_type = str(part.get("type") or "").strip().lower()
        candidate_keys = ("text", "output_text", "value", "content")

        if part_type in {"text", "output_text", "mcp_tool_result", "mcp_tool_use", ""}:
            for key in candidate_keys:
                value = part.get(key)
                if isinstance(value, str) and value.strip():
                    texts.append(value.strip())
                    break
        return texts

    def _collect_from_container(container: dict[str, object]) -> list[str]:
        direct = container.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return [direct.strip()]
        if isinstance(direct, list):
            texts = [item.strip() for item in direct if isinstance(item, str) and item.strip()]
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
                        texts.extend(_collect_from_part(part))

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


def _strip_markdown_fences(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    if cleaned.startswith("json"):
        cleaned = cleaned[4:]
    return cleaned.strip()


def _extract_candidate_records_from_json(response_text: str) -> list[dict[str, object]] | None:
    cleaned = _strip_markdown_fences(response_text)
    if not cleaned:
        return []

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None

    raw_candidates: object
    if isinstance(parsed, dict):
        raw_candidates = parsed.get("candidates", [])
    elif isinstance(parsed, list):
        raw_candidates = parsed
    else:
        return []

    if not isinstance(raw_candidates, list):
        return []

    return [item for item in raw_candidates if isinstance(item, dict)]


def _extract_candidate_author(segment: str, tweet_url: str) -> str:
    patterns = (
        r"(?:author(?:_username)?|username|user)\s*[:=-]\s*@?([A-Za-z0-9_]{1,15})",
        r"@([A-Za-z0-9_]{1,15})",
    )
    for pattern in patterns:
        match = re.search(pattern, segment, re.IGNORECASE)
        if match:
            return match.group(1).strip().lstrip("@")
    return _extract_author_from_x_url(tweet_url)


def _extract_candidate_timestamp(segment: str) -> str:
    patterns = (
        r"(?:created_at(?:_iso)?|timestamp|posted(?: at)?|time)\s*[:=-]\s*([^\n]+)",
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)",
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?: ?UTC)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, segment, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _extract_candidate_text(segment: str, tweet_url: str) -> str:
    labelled_patterns = (
        r"(?:tweet[_ ]?text|full text|post text|text|content)\s*[:=-]\s*(.+?)(?=(?:author(?:_username)?|username|timestamp|created_at(?:_iso)?|tweet_url|url)\s*[:=-]|$)",
    )
    for pattern in labelled_patterns:
        match = re.search(pattern, segment, re.IGNORECASE | re.DOTALL)
        if match:
            text = re.sub(r"\s+", " ", match.group(1)).strip()
            if text:
                return text[:2000]

    lines: list[str] = []
    for line in segment.splitlines():
        text = line.strip()
        if not text:
            continue
        lowered = text.lower()
        if tweet_url in text:
            continue
        if lowered.startswith(("author:", "author_username:", "username:", "timestamp:", "time:", "created_at:", "created_at_iso:", "url:", "tweet_url:")):
            continue
        lines.append(text)

    collapsed = re.sub(r"\s+", " ", " ".join(lines)).strip()
    return collapsed[:2000]


def _extract_candidate_records_from_text(response_text: str) -> list[dict[str, object]]:
    url_pattern = re.compile(r"https?://(?:x|twitter)\.com/[A-Za-z0-9_]{1,15}/status/\d+[^\s)\]}]*", re.IGNORECASE)
    matches = list(url_pattern.finditer(response_text))
    if not matches:
        return []

    records: list[dict[str, object]] = []
    seen_tweet_ids: set[str] = set()

    for index, match in enumerate(matches):
        tweet_url = match.group(0).rstrip(".,)")
        tweet_id = extract_tweet_id_from_x_url(tweet_url)
        if tweet_id and tweet_id in seen_tweet_ids:
            continue
        if tweet_id:
            seen_tweet_ids.add(tweet_id)

        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(response_text)
        window_start = max(0, match.start() - 400)
        segment = response_text[window_start:next_start]

        tweet_text = _extract_candidate_text(segment, tweet_url)
        if not tweet_text:
            continue

        records.append(
            {
                "tweet_url": tweet_url,
                "tweet_text": tweet_text,
                "author_username": _extract_candidate_author(segment, tweet_url),
                "created_at_iso": _extract_candidate_timestamp(segment),
            }
        )

    return records


def _extract_candidate_records(response_text: str) -> list[dict[str, object]]:
    parsed_json = _extract_candidate_records_from_json(response_text)
    if parsed_json is not None:
        return parsed_json
    return _extract_candidate_records_from_text(response_text)


def parse_xai_candidates(
    payload: dict[str, object],
    response_text: str,
    job: SearchJob,
) -> list[PreparedReviewCandidate]:
    """
    Parse Grok output into prepared review candidates.

    The parser accepts both structured JSON and natural-language result lists.
    """
    raw_candidates = _extract_candidate_records(response_text)
    if not raw_candidates:
        return []

    citation_urls = _extract_citation_urls(payload)
    if not citation_urls:
        log.info(
            "xAI response did not expose citation URLs; accepting candidates based "
            "on the model output only."
        )

    now = datetime.now(timezone.utc)
    prepared: list[PreparedReviewCandidate] = []

    for item in raw_candidates:
        if not isinstance(item, dict):
            continue

        tweet_url = str(item.get("tweet_url") or "").strip()
        tweet_text = str(item.get("tweet_text") or "").strip()
        if not tweet_url or not tweet_text:
            continue

        if citation_urls and not validate_candidate_citations(tweet_url, citation_urls):
            log.info("Discarded xAI candidate: URL not present in citations (%s)", tweet_url)
            continue

        tweet_id = extract_tweet_id_from_x_url(tweet_url)
        if not tweet_id:
            log.info("Discarded xAI candidate: invalid X status URL (%s)", tweet_url)
            continue

        replies = _clean_reply_options(item.get("replies"))
        category = _normalize_category_value(item.get("category"))
        if category == TweetCategory.IRRELEVANT.value:
            category = _preferred_category_for_hint(job.query.category_hint)

        created_at = _parse_datetime_value(item.get("created_at_iso"))
        if created_at is None:
            created_at = _created_at_from_tweet_id(tweet_id)
        if created_at is None:
            log.info(
                "Discarded xAI candidate: missing or invalid created_at_iso (%s)",
                tweet_url,
            )
            continue
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
        cleaned_themes = [
            str(theme).strip()
            for theme in themes
            if str(theme).strip()
        ][:6]

        competitor = item.get("competitor_mentioned")
        competitor_text = str(competitor).strip() if competitor not in (None, "") else None

        raw_reason = str(item.get("reason") or "").strip()
        author_username = (
            str(item.get("author_username") or _extract_author_from_x_url(tweet_url))
            .strip()
            .lstrip("@")
            or "unknown"
        )
        author_name = str(item.get("author_name") or author_username).strip() or author_username

        why_relevant = str(item.get("why_relevant") or "").strip()
        yara_angle = str(
            item.get("yara_angle")
            or why_relevant
            or "Relevant X post found via Grok search."
        ).strip()

        try:
            raw_score = int(round(float(item.get("score"))))
        except (TypeError, ValueError):
            raw_score = None

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
            local_score=float(raw_score if raw_score is not None else confidence),
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
            "reason": raw_reason,
            "replies": replies,
        }
        if raw_score is not None:
            analysis["score"] = raw_score
        prepared.append(
            PreparedReviewCandidate(
                tweet=tweet,
                analysis=analysis,
                provider="xai_x_search",
                source_query=job.query.query,
            )
        )

    return prepared


def select_due_queries(
    config: Config,
    runtime: SearchRuntime,
    request_budget: int,
    brand_direct_enabled: bool = False,
) -> list[SearchJob]:
    """
    Select configured search queries that are due to run.
    """
    if request_budget <= 0:
        return []

    now_ts = time.time()
    due_jobs: list[SearchJob] = []

    for query in config.search_queries:
        if not query.enabled:
            continue
        if brand_direct_enabled and query.category_hint == "brand_mention":
            continue
        if not _should_run_query(config, query):
            continue

        last_run = runtime.last_query_run.get(query.query, 0.0)
        if now_ts - last_run < max(60, query.cooldown_seconds):
            continue

        query_type = query.query_type or "Top"
        if (
            config.enable_latest_fallback
            and runtime.empty_scan_counts.get(query.query, 0) >= config.lane_empty_scan_threshold
        ):
            query_type = "Latest"

        due_jobs.append(SearchJob(query=query, query_type=query_type))

    due_jobs.sort(
        key=lambda job: (
            _lane_priority(config, job.query, brand_direct_enabled),
            runtime.last_query_run.get(job.query.query, 0.0),
            job.query.priority,
        )
    )

    selected_jobs: list[SearchJob] = []
    selected_complaint_brands: set[str] = set()
    selected_solution_lane = False

    for job in due_jobs:
        if len(selected_jobs) >= request_budget:
            break

        if job.query.category_hint == "competitor_complaint":
            brand_family = str(job.query.brand_family or "").strip().lower()
            if brand_family and brand_family in selected_complaint_brands:
                continue
            if brand_family:
                selected_complaint_brands.add(brand_family)
            selected_jobs.append(job)
            continue

        if job.query.category_hint == "solution_seeker":
            if selected_solution_lane:
                continue
            selected_solution_lane = True
            selected_jobs.append(job)
            continue

        selected_jobs.append(job)

    return selected_jobs


def _should_run_query(config: Config, query: SearchQuery) -> bool:
    if config.search_event_mode != "anchored":
        return query.strategy_mode != "anchored_event"

    brand_family = str(query.brand_family or "").strip().lower()
    if query.strategy_mode == "anchored_event":
        return not brand_family or brand_family in config.search_event_brands

    return bool(brand_family and brand_family in config.search_event_brands)


def _lane_priority(config: Config, query: SearchQuery, brand_direct_enabled: bool) -> int:
    if _active_event_window_for_query(config, query):
        return 0
    category_hint = query.category_hint
    if category_hint == "solution_seeker":
        return 2
    if category_hint == "competitor_complaint":
        return 1
    if category_hint == "brand_mention":
        return 4 if brand_direct_enabled else 3
    return 4


def _parse_datetime_value(raw_value: object) -> datetime | None:
    if raw_value is None:
        return None

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
        return None


def _created_at_from_tweet_id(tweet_id: str) -> datetime | None:
    try:
        snowflake = int(str(tweet_id or "").strip())
        if snowflake < (1 << 22):
            return None
        timestamp_ms = (snowflake >> 22) + X_SNOWFLAKE_EPOCH_MS
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _extract_citation_urls(payload: dict[str, object]) -> list[str]:
    candidates: list[str] = []

    def _collect(raw_value: object) -> None:
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

    def _collect_output_annotations(container: dict[str, object]) -> None:
        output = container.get("output")
        if not isinstance(output, list):
            return

        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                _collect(part.get("annotations"))

    _collect(payload.get("citations"))
    _collect_output_annotations(payload)

    nested = payload.get("response")
    if isinstance(nested, dict):
        _collect(nested.get("citations"))
        _collect_output_annotations(nested)

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
    """
    Extract the tweet ID from a canonical X status URL.
    """
    match = re.search(r"/status/(\d+)", str(url or ""))
    if not match:
        return None
    return match.group(1)


def validate_candidate_citations(candidate_url: str, citations: list[str]) -> bool:
    """
    Ensure the candidate URL was actually cited by xAI.
    """
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
    match = re.search(r"x\.com/([^/]+)/status/", str(url or ""))
    if not match:
        return "unknown"

    username = match.group(1).strip().lstrip("@")
    return username or "unknown"


def _clean_reply_options(raw_replies: object) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
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


def _collect_tool_call_names(payload: dict[str, object]) -> list[str]:
    raw_calls = payload.get("tool_calls")
    nested = payload.get("response")
    if raw_calls is None and isinstance(nested, dict):
        raw_calls = nested.get("tool_calls")

    names: list[str] = []
    if not isinstance(raw_calls, list):
        return names

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

    if names:
        return names

    def _collect_from_output(container: dict[str, object]) -> None:
        output = container.get("output")
        if not isinstance(output, list):
            return

        for item in output:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name:
                names.append(name)

    _collect_from_output(payload)
    if isinstance(nested, dict):
        _collect_from_output(nested)

    return names


def _collect_server_side_x_search_calls(payload: dict[str, object]) -> int:
    usage = payload.get("server_side_tool_usage")
    nested = payload.get("response")
    if usage is None and isinstance(nested, dict):
        usage = nested.get("server_side_tool_usage")

    if not isinstance(usage, dict):
        return 0

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

    return total


def _update_xai_usage_counters(
    runtime: SearchRuntime,
    payload: dict[str, object],
) -> list[str]:
    usage = payload.get("usage")
    nested = payload.get("response")
    if usage is None and isinstance(nested, dict):
        usage = nested.get("usage")

    if isinstance(usage, dict):
        prompt_tokens = _coerce_int(
            usage.get("prompt_tokens", usage.get("input_tokens")),
        )
        completion_tokens = _coerce_int(
            usage.get("completion_tokens", usage.get("output_tokens")),
        )
        prompt_details = usage.get("prompt_tokens_details")
        prompt_text_tokens = prompt_tokens
        cached_prompt_tokens = 0
        if isinstance(prompt_details, dict):
            prompt_text_tokens = _coerce_int(
                prompt_details.get("text_tokens"),
                prompt_tokens,
            )
            cached_prompt_tokens = _coerce_int(prompt_details.get("cached_tokens"))

        completion_details = usage.get("completion_tokens_details")
        reasoning_tokens = _coerce_int(usage.get("reasoning_tokens"))
        if isinstance(completion_details, dict):
            reasoning_tokens = _coerce_int(
                completion_details.get("reasoning_tokens"),
                reasoning_tokens,
            )

        runtime.xai_prompt_tokens += prompt_tokens
        runtime.xai_prompt_text_tokens += prompt_text_tokens
        runtime.xai_cached_prompt_tokens += cached_prompt_tokens
        runtime.xai_completion_tokens += completion_tokens
        runtime.xai_reasoning_tokens += reasoning_tokens

        for key in ("cost_usd_ticks", "estimated_cost_usd_ticks"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                runtime.xai_cost_usd_ticks += int(value)

        _append_xai_usage_event(
            runtime,
            prompt_tokens=prompt_tokens,
            prompt_text_tokens=prompt_text_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
        )

    tool_names = _collect_tool_call_names(payload)
    x_search_calls = _collect_server_side_x_search_calls(payload)
    if not x_search_calls:
        x_search_calls = sum(
            1 for name in tool_names if name.startswith("x_") or name == "x_search"
        )
    runtime.xai_x_search_tool_calls += x_search_calls
    return tool_names
