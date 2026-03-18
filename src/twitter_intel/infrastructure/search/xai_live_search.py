"""
xAI live search helpers for the modular application.

Builds Grok x_search prompts, executes xAI requests, parses results into
PreparedReviewCandidate objects, and updates runtime telemetry.
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from twitter_intel.config import Config, SearchJob, SearchRuntime
from twitter_intel.domain.entities.category import TweetCategory
from twitter_intel.domain.entities.tweet import PreparedReviewCandidate, TweetCandidate
from twitter_intel.domain.interfaces import NotificationService
from twitter_intel.exceptions import XaiAuthError, XaiRateLimitError
from twitter_intel.infrastructure.search.xai_client import XaiClient

log = logging.getLogger(__name__)


async def fetch_candidates_from_xai_search(
    config: Config,
    client: XaiClient,
    runtime: SearchRuntime,
    notification_service: NotificationService,
) -> list[PreparedReviewCandidate]:
    """
    Fetch pre-classified review candidates from xAI's x_search tool.

    The helper mirrors the legacy provider flow: it selects due queries,
    prompts Grok to search and classify, retries once for invalid JSON,
    and pauses the provider on auth/rate-limit failures.
    """
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

    request_budget = max(0, config.max_api_requests_per_scan)
    due_jobs = select_due_queries(
        config,
        runtime,
        request_budget,
        brand_direct_enabled=False,
    )
    if not due_jobs:
        runtime.last_fetch_summary = "no_due_queries"
        return []

    from_date, to_date = _search_date_window(config.search_since_days)
    tool_config = _build_xai_tool_config(config, from_date, to_date)

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
        prompt = build_xai_search_prompt(config, job)

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
            )
            tool_names = _update_xai_usage_counters(runtime, payload)
            if config.xai_debug_log_tool_calls and tool_names:
                log.info("xAI tool calls: %s", ", ".join(tool_names))

            response_text = extract_output_text_from_xai_response(payload)

            try:
                job_candidates = parse_xai_candidates(payload, response_text, job)
            except (json.JSONDecodeError, ValueError) as exc:
                if remaining_budget <= 0:
                    log.error(
                        "Skipping xAI query '%s': invalid JSON and no request budget "
                        "remains for a repair retry (%s)",
                        job.query.description or job.query.query,
                        exc,
                    )
                    job_candidates = []
                else:
                    log.warning("Invalid xAI JSON response, retrying once: %s", exc)
                    repair_prompt = (
                        prompt
                        + "\n\nYour previous reply was invalid. Return STRICT JSON "
                        + "only with the required schema."
                    )
                    runtime.api_requests_made += 1
                    runtime.xai_requests_made += 1
                    remaining_budget -= 1
                    payload = await client.create_response(
                        model=config.xai_model,
                        prompt=repair_prompt,
                        tool_config=tool_config,
                        max_turns=config.xai_max_turns,
                    )
                    tool_names = _update_xai_usage_counters(runtime, payload)
                    if config.xai_debug_log_tool_calls and tool_names:
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
            "zero_provider_results"
            if not prepared_candidates
            else f"candidates:{len(prepared_candidates)}"
        )

    return prepared_candidates


def _search_date_window(search_since_days: int | None) -> tuple[str | None, str | None]:
    if search_since_days is None:
        return None, None

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=search_since_days)
    return start_date.isoformat(), end_date.isoformat()


def _build_xai_tool_config(
    config: Config,
    from_date: str | None,
    to_date: str | None,
) -> dict[str, object]:
    """
    Build the tool payload using the same shape as the legacy bot.
    """
    tool: dict[str, object] = {"type": "x_search"}

    if from_date:
        tool["from_date"] = from_date
    if to_date:
        tool["to_date"] = to_date

    if config.xai_allowed_x_handles:
        tool["allowed_x_handles"] = config.xai_allowed_x_handles[:10]
    elif config.xai_excluded_x_handles:
        tool["excluded_x_handles"] = config.xai_excluded_x_handles[:10]

    if config.xai_enable_image_understanding:
        tool["enable_image_understanding"] = True
    if config.xai_enable_video_understanding:
        tool["enable_video_understanding"] = True

    return tool


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


def build_xai_search_prompt(config: Config, job: SearchJob) -> str:
    """
    Build the xAI prompt for a specific due search lane.
    """
    from_date, to_date = _search_date_window(config.search_since_days)
    date_window = (
        f"Use the tool's date window from {from_date} to {to_date} (UTC dates inclusive)."
        if from_date and to_date
        else "No explicit tool date window is configured."
    )
    preferred_category = _preferred_category_for_hint(job.query.category_hint)
    freshness_window = _build_freshness_window_instruction(
        config.max_tweet_age_minutes
    )

    return f"""{config.brand_context}

You are curating X posts for Yara.cash. Use the x_search tool to find recent, high-signal, actionable posts that match the lane below.

Lane:
- Description: {job.query.description}
- Search intent: {job.query.query}
- Preferred category: {preferred_category}
- Query mode hint: {job.query_type}
- {date_window}
- {freshness_window}

Selection rules:
- Interpret the search intent semantically even if it contains X-style operators such as lang:, since:, or -filter:.
- Ignore retweets, obvious spam, giveaways, and unrelated chatter.
- Prefer posts that are recent, specific, and worth a human reply.
- If no posts meet the freshness requirement, return {{"candidates": []}}.
- Return at most {config.max_discord_approvals_per_scan} candidates.
- Only include candidates that deserve review.
- Do not include irrelevant items in the final JSON.
- Preserve the exact X post URL in tweet_url.
- Produce 1 to {config.num_reply_options} reply options per candidate.
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


def _build_freshness_window_instruction(max_tweet_age_minutes: int) -> str:
    """
    Tell xAI the exact rolling freshness window to enforce.
    """
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


def parse_xai_candidates(
    payload: dict[str, object],
    response_text: str,
    job: SearchJob,
) -> list[PreparedReviewCandidate]:
    """
    Parse strict JSON returned by xAI into prepared review candidates.
    """
    parsed = json.loads(response_text)
    if not isinstance(parsed, dict):
        raise ValueError("xAI output must be a JSON object")

    raw_candidates = parsed.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("xAI output must include a 'candidates' list")

    citation_urls = _extract_citation_urls(payload)
    if not citation_urls:
        log.warning(
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
        cleaned_themes = [
            str(theme).strip()
            for theme in themes
            if str(theme).strip()
        ][:6]

        competitor = item.get("competitor_mentioned")
        competitor_text = str(competitor).strip() if competitor not in (None, "") else None

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
            _lane_priority(job.query.category_hint, brand_direct_enabled),
            runtime.last_query_run.get(job.query.query, 0.0),
        )
    )
    return due_jobs[:request_budget]


def _lane_priority(category_hint: str, brand_direct_enabled: bool) -> int:
    if category_hint == "solution_seeker":
        return 0
    if category_hint == "competitor_complaint":
        return 1
    if category_hint == "brand_mention":
        return 2 if brand_direct_enabled else 3
    return 4


def _parse_datetime_value(raw_value: object) -> datetime:
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
