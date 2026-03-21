"""
Scan and notify use case.

Orchestrates the main scan pipeline: fetch candidates from search provider,
score and filter locally, send to AI classifier, and queue for Discord review.

Implements SRS-YARA-XSS-2026:
- Section 4.3: Time-Window Filtering
- Section 4.4: Candidate Scoring
- Section 5.4: Post-Retrieval Filtering (FR-13 through FR-15)
- Section 5.5: Scoring and Ranking (FR-16 through FR-18)
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

from twitter_intel.config import Config, SearchRuntime
from twitter_intel.config.search_queries import build_standard_search_query
from twitter_intel.config.brand_registry import get_brand, BrandConfig
from twitter_intel.domain.entities.category import TweetCategory
from twitter_intel.domain.entities.tweet import PreparedReviewCandidate, TweetCandidate
from twitter_intel.domain.entities.xss_output import (
    XSSSearchCycleOutput,
    create_search_cycle_output,
)
from twitter_intel.domain.interfaces import (
    AIClassifier,
    NotificationService,
    SearchProvider,
    TweetRepository,
)
from twitter_intel.exceptions import TwitterApiIoAuthError, TwitterApiIoRateLimitError
from twitter_intel.domain.services import (
    filter_candidates,
    format_discarded_candidates,
    score_candidate,
)
from twitter_intel.domain.services.scoring import score_candidate_xss, ScoringResult
from twitter_intel.infrastructure.search.xai_client import XaiClient
from twitter_intel.infrastructure.search.xai_live_search import (
    fetch_candidates_from_xai_search,
    select_due_queries,
)

log = logging.getLogger(__name__)


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


@dataclass
class ScanResult:
    """Result of a scan operation."""
    queued_count: int
    total_candidates: int
    filtered_count: int
    message: str


class ScanAndNotifyUseCase:
    """
    Orchestrate the scan pipeline.

    Handles the full flow: fetch -> score -> filter -> classify -> queue.
    Supports multiple search providers with different flows.
    """

    def __init__(
        self,
        config: Config,
        repository: TweetRepository,
        search_provider: SearchProvider,
        classifier: AIClassifier | None,
        notification_service: NotificationService,
        runtime: SearchRuntime,
        xai_client: XaiClient | None = None,
    ):
        self._config = config
        self._repository = repository
        self._search_provider = search_provider
        self._classifier = classifier
        self._notification_service = notification_service
        self._runtime = runtime
        self._xai_client = xai_client

    async def execute(self) -> ScanResult:
        """
        Execute a scan operation.

        Returns:
            ScanResult with statistics
        """
        if self._config.search_provider == "manual_only":
            log.info("Live search disabled (SEARCH_PROVIDER=manual_only)")
            return ScanResult(
                queued_count=0,
                total_candidates=0,
                filtered_count=0,
                message="Live search disabled",
            )

        # xAI x_search has its own flow (search + classify in one step)
        if self._config.search_provider == "xai_x_search":
            return await self._execute_xai_flow()

        # Standard flow: fetch -> score -> filter -> classify -> queue
        return await self._execute_standard_flow()

    async def _execute_xai_flow(self) -> ScanResult:
        """
        Execute scan using xAI x_search provider.

        xAI returns raw candidate references. Application code performs the
        SRS-required filtering, scoring, ranking, and output emission locally.
        """
        prepared_candidates = await self._fetch_xai_candidates()
        self._runtime.tweets_fetched += len(prepared_candidates)

        lane_lookup = {
            getattr(query, "query", ""): query
            for query in getattr(self._config, "search_queries", [])
            if getattr(query, "query", "")
        }
        output_by_query = self._build_xss_output_map(lane_lookup)

        if not prepared_candidates:
            self._runtime.last_xss_outputs = [
                output.to_dict() for output in output_by_query.values()
            ]
            self._log_no_candidates()
            return ScanResult(
                queued_count=0,
                total_candidates=0,
                filtered_count=0,
                message="No candidates from xAI x_search",
            )

        seen_urls: set[str] = set()
        discarded: list[tuple[str, float, str]] = []
        surviving_candidates: list[PreparedReviewCandidate] = []

        for prepared in prepared_candidates:
            tweet = prepared.tweet
            lane = lane_lookup.get(prepared.source_query)
            output = self._get_or_create_xss_output(output_by_query, prepared.source_query, lane)
            output.raw_result_count += 1

            # If xAI keeps resurfacing the same stale post, ignore it after
            # the first rejection so the status channel does not get spammed.
            if tweet.tweet_id in self._runtime.stale_candidate_ids:
                continue

            dedupe_key = self._normalize_tweet_url(tweet.url) or tweet.tweet_id
            if dedupe_key in seen_urls:
                self._runtime.duplicates_dropped += 1
                discarded.append((tweet.tweet_id, tweet.local_score, "duplicate_in_scan"))
                continue

            if self._repository.is_processed(tweet.tweet_id):
                self._runtime.duplicates_dropped += 1
                discarded.append((tweet.tweet_id, tweet.local_score, "already_processed"))
                continue

            seen_urls.add(dedupe_key)

            if tweet.age_minutes > self._config.max_tweet_age_minutes:
                self._runtime.locally_filtered_out += 1
                self._runtime.stale_candidate_ids.add(tweet.tweet_id)
                discarded.append((tweet.tweet_id, tweet.local_score, "too_old"))
                continue

            if self._is_official_lane_author(tweet, lane):
                self._runtime.locally_filtered_out += 1
                discarded.append((tweet.tweet_id, tweet.local_score, "official_author"))
                continue

            lower_bound, upper_bound = self._resolve_xss_filter_bounds(lane)
            if self._is_outside_xss_time_window(tweet, lower_bound, upper_bound):
                self._runtime.locally_filtered_out += 1
                discarded.append(
                    (tweet.tweet_id, tweet.local_score, self._xss_filter_reason(lane))
                )
                continue

            score_value, score_reason, passes_threshold = self._score_xai_candidate(
                prepared,
                lane,
            )
            tweet.local_score = float(score_value)
            prepared.analysis["score"] = score_value
            prepared.analysis["reason"] = score_reason

            if not passes_threshold:
                self._runtime.locally_filtered_out += 1
                discarded.append((tweet.tweet_id, tweet.local_score, "below_xss_threshold"))
                continue

            surviving_candidates.append(prepared)

        surviving_candidates.sort(
            key=lambda candidate: (
                candidate.tweet.local_score,
                candidate.tweet.created_at,
            ),
            reverse=True,
        )

        for prepared in surviving_candidates:
            lane = lane_lookup.get(prepared.source_query)
            output = self._get_or_create_xss_output(output_by_query, prepared.source_query, lane)
            output.add_candidate(
                tweet_url=prepared.tweet.url,
                tweet_text=prepared.tweet.text,
                author_username=prepared.tweet.author_username,
                created_at=prepared.tweet.created_at,
                category=self._xss_output_category(
                    prepared.analysis.get("category"),
                    lane,
                ),
                score=int(prepared.analysis.get("score", round(prepared.tweet.local_score))),
                reason=str(prepared.analysis.get("reason") or "matched_lane").strip(),
            )

        for output in output_by_query.values():
            output.filtered_result_count = len(output.candidates)
        self._runtime.last_xss_outputs = [
            output.to_dict() for output in output_by_query.values()
        ]

        queue_candidates = surviving_candidates[: self._config.max_discord_approvals_per_scan]
        for prepared in surviving_candidates[self._config.max_discord_approvals_per_scan :]:
            self._runtime.locally_filtered_out += 1
            discarded.append((prepared.tweet.tweet_id, prepared.tweet.local_score, "trimmed_by_cap"))

        queued_count = 0
        for prepared in queue_candidates:
            if await self._queue_for_review(prepared.tweet, prepared.analysis):
                queued_count += 1
                self._runtime.queued_to_discord += 1

            await asyncio.sleep(1)

        log.info(
            "Grok prepared %s candidate(s), %s survived local XSS filtering, %s queued to Discord",
            len(prepared_candidates),
            len(surviving_candidates),
            queued_count,
        )

        # Log discarded candidates
        self._log_discarded(discarded)

        if queued_count > 0:
            await self._notification_service.send_status(
                f"Scan complete via {self._config.search_provider}: "
                f"{queued_count} new tweets sent for approval"
            )

        return ScanResult(
            queued_count=queued_count,
            total_candidates=len(prepared_candidates),
            filtered_count=len(discarded),
            message=f"Queued {queued_count} from xAI x_search",
        )

    async def _execute_standard_flow(self) -> ScanResult:
        """
        Execute standard scan flow (twitterapi_io, twscrape).

        Fetches candidates, scores locally, filters, sends to AI classifier.
        """
        # Fetch candidates from search provider
        all_candidates = await self._fetch_standard_candidates()

        if not all_candidates:
            self._log_no_candidates()
            return ScanResult(
                queued_count=0,
                total_candidates=0,
                filtered_count=0,
                message="No candidates from provider",
            )

        # Get already processed IDs
        processed_ids = self._repository.get_processed_ids()

        # Filter and score candidates
        scored_candidates, discarded = filter_candidates(
            all_candidates,
            self._config.max_tweet_age_minutes,
            processed_ids,
        )

        lane_lookup = {
            getattr(query, "query", ""): query
            for query in getattr(self._config, "search_queries", [])
            if getattr(query, "query", "")
        }
        lane_filtered_candidates: list[TweetCandidate] = []
        for tweet in scored_candidates:
            lane = lane_lookup.get(tweet.search_query)

            if self._is_official_lane_author(tweet, lane):
                discarded.append((tweet.tweet_id, tweet.local_score, "official_author"))
                continue

            if self._is_outside_anchored_event_window(tweet, lane):
                discarded.append((tweet.tweet_id, tweet.local_score, "outside_event_window"))
                continue

            if self._is_outside_restart_catchup_window(tweet):
                discarded.append((tweet.tweet_id, tweet.local_score, "outside_restart_catchup"))
                continue

            lane_filtered_candidates.append(tweet)
        scored_candidates = lane_filtered_candidates

        # Update runtime stats
        for _, _, reason in discarded:
            if reason in ("duplicate_in_scan", "already_processed"):
                self._runtime.duplicates_dropped += 1
            else:
                self._runtime.locally_filtered_out += 1

        # Cap candidates for local processing
        if len(scored_candidates) > self._config.max_local_candidates_per_scan:
            extra = len(scored_candidates) - self._config.max_local_candidates_per_scan
            self._runtime.locally_filtered_out += extra
            for tweet in scored_candidates[self._config.max_local_candidates_per_scan:]:
                discarded.append((tweet.tweet_id, tweet.local_score, "trimmed_by_cap"))
        local_candidates = scored_candidates[:self._config.max_local_candidates_per_scan]

        # Cap candidates for AI
        if len(local_candidates) > self._config.max_ai_candidates_per_scan:
            extra = len(local_candidates) - self._config.max_ai_candidates_per_scan
            self._runtime.locally_filtered_out += extra
            for tweet in local_candidates[self._config.max_ai_candidates_per_scan:]:
                discarded.append((tweet.tweet_id, tweet.local_score, "trimmed_by_cap"))
        ai_candidates = local_candidates[:self._config.max_ai_candidates_per_scan]

        log.info(
            "Found %s raw candidates, %s after scoring, %s sent to classifier",
            len(all_candidates),
            len(local_candidates),
            len(ai_candidates),
        )

        # Log discarded candidates
        self._log_discarded(discarded)

        # Send to AI classifier and queue results
        queued_count = 0
        for tweet in ai_candidates:
            if queued_count >= self._config.max_discord_approvals_per_scan:
                break

            log.info(
                "Analyzing %s by @%s (score %.1f)",
                tweet.tweet_id,
                tweet.author_username,
                tweet.local_score,
            )

            self._runtime.sent_to_gemini += 1
            analysis = await self._classify_tweet(tweet)
            if not analysis:
                continue

            if await self._queue_for_review(tweet, analysis):
                queued_count += 1
                self._runtime.queued_to_discord += 1

            await asyncio.sleep(1)

        if queued_count > 0:
            await self._notification_service.send_status(
                f"Scan complete via {self._config.search_provider}: "
                f"{queued_count} new tweets sent for approval"
            )

        return ScanResult(
            queued_count=queued_count,
            total_candidates=len(all_candidates),
            filtered_count=len(discarded),
            message=f"Queued {queued_count} from {self._config.search_provider}",
        )

    async def _fetch_xai_candidates(self) -> list[PreparedReviewCandidate]:
        """Fetch pre-classified candidates from xAI x_search."""
        if not self._xai_client:
            log.error("xAI search requested but XaiClient is not configured")
            self._runtime.last_fetch_summary = "error:xai_client_not_configured"
            return []

        try:
            return await fetch_candidates_from_xai_search(
                config=self._config,
                client=self._xai_client,
                runtime=self._runtime,
                notification_service=self._notification_service,
            )
        except Exception as exc:
            detail = _describe_exception(exc)
            log.error("xAI search failed: %s", detail)
            self._runtime.last_fetch_summary = f"error:{detail}"
            return []

    def _is_official_lane_author(self, tweet: TweetCandidate, lane: Any | None) -> bool:
        if lane is None:
            return False

        official_handles = {
            str(handle or "").strip().lstrip("@").lower()
            for handle in getattr(lane, "exclude_author_handles", []) or []
        }
        if not official_handles:
            return False

        return tweet.author_username.strip().lstrip("@").lower() in official_handles

    def _is_outside_anchored_event_window(
        self,
        tweet: TweetCandidate,
        lane: Any | None,
    ) -> bool:
        if getattr(self._config, "search_event_mode", "off") != "anchored":
            return False
        if getattr(self._config, "search_event_anchor_utc", None) is None:
            return False
        if lane is None:
            return False

        brand_family = str(getattr(lane, "brand_family", "") or "").strip().lower()
        event_brands = {
            str(brand or "").strip().lower()
            for brand in getattr(self._config, "search_event_brands", []) or []
        }
        if not brand_family or brand_family not in event_brands:
            return False

        lower_bound = self._config.search_event_anchor_utc + timedelta(
            minutes=max(0, int(getattr(self._config, "search_event_min_offset_minutes", 30)))
        )
        upper_bound = self._config.search_event_anchor_utc + timedelta(
            minutes=max(1, int(getattr(self._config, "search_event_max_offset_minutes", 360)))
        )
        return not (lower_bound <= tweet.created_at <= upper_bound)

    def _is_outside_restart_catchup_window(self, tweet: TweetCandidate) -> bool:
        lower_bound = getattr(self._runtime, "restart_catchup_start_utc", None)
        upper_bound = getattr(self._runtime, "restart_catchup_end_utc", None)
        if lower_bound is None or upper_bound is None:
            return False
        return not (lower_bound <= tweet.created_at <= upper_bound)

    def _compute_xss_time_window(
        self,
        restart_time_utc: Optional[datetime] = None,
    ) -> tuple[Optional[datetime], Optional[datetime]]:
        """
        Compute SRS-compliant time-window bounds per Section 4.3.2.

        The system shall:
        - Accept a restart_time_utc parameter for each search cycle
        - Compute lower_bound = restart_time_utc + window_start_offset (default: 30 min)
        - Compute upper_bound = restart_time_utc + window_end_offset (default: 6 hours)

        Args:
            restart_time_utc: Server restart timestamp (uses catchup start if not provided)

        Returns:
            Tuple of (lower_bound, upper_bound) datetime objects, or (None, None)
        """
        anchor = restart_time_utc or getattr(
            self._runtime, "restart_catchup_start_utc", None
        )
        if anchor is None:
            return None, None

        lower_bound = anchor + timedelta(
            minutes=self._config.xss_window_start_offset_minutes
        )
        upper_bound = anchor + timedelta(
            minutes=self._config.xss_window_end_offset_minutes
        )
        return lower_bound, upper_bound

    def _is_outside_xss_time_window(
        self,
        tweet: TweetCandidate,
        lower_bound: Optional[datetime] = None,
        upper_bound: Optional[datetime] = None,
    ) -> bool:
        """
        Check if tweet is outside the XSS time window per SRS FR-14.

        The system shall parse created_at_iso timestamps as UTC datetime objects
        and compare them against the computed lower_bound and upper_bound.

        Args:
            tweet: Tweet candidate to check
            lower_bound: Time window lower bound
            upper_bound: Time window upper bound

        Returns:
            True if tweet is outside the window
        """
        if lower_bound is None or upper_bound is None:
            return False
        return not (lower_bound <= tweet.created_at <= upper_bound)

    @staticmethod
    def _normalize_tweet_url(url: str) -> str:
        normalized = str(url or "").strip().rstrip("/")
        if not normalized:
            return ""
        return normalized.replace("https://twitter.com/", "https://x.com/")

    def _resolve_xss_filter_bounds(
        self,
        lane: Any | None = None,
    ) -> tuple[Optional[datetime], Optional[datetime]]:
        if (
            getattr(self._config, "search_event_mode", "off") == "anchored"
            and getattr(self._config, "search_event_anchor_utc", None) is not None
            and lane is not None
        ):
            brand_family = str(getattr(lane, "brand_family", "") or "").strip().lower()
            event_brands = {
                str(brand or "").strip().lower()
                for brand in getattr(self._config, "search_event_brands", []) or []
            }
            if brand_family and brand_family in event_brands:
                lower_bound = self._config.search_event_anchor_utc + timedelta(
                    minutes=max(
                        0,
                        int(getattr(self._config, "search_event_min_offset_minutes", 30)),
                    )
                )
                upper_bound = self._config.search_event_anchor_utc + timedelta(
                    minutes=max(
                        1,
                        int(getattr(self._config, "search_event_max_offset_minutes", 360)),
                    )
                )
                return lower_bound, upper_bound

        lower_bound, upper_bound = self._compute_xss_time_window()
        catchup_end = getattr(self._runtime, "restart_catchup_end_utc", None)
        if lower_bound is not None and upper_bound is not None and catchup_end is not None:
            upper_bound = min(upper_bound, catchup_end)
        return lower_bound, upper_bound

    def _xss_filter_reason(self, lane: Any | None = None) -> str:
        lower_bound, upper_bound = self._resolve_xss_filter_bounds(lane)
        if lower_bound is None or upper_bound is None:
            return "outside_xss_window"
        if (
            getattr(self._config, "search_event_mode", "off") == "anchored"
            and getattr(self._config, "search_event_anchor_utc", None) is not None
        ):
            return "outside_event_window"
        return "outside_xss_window"

    def _score_candidate_xss(
        self,
        tweet: TweetCandidate,
        lane: Any | None = None,
    ) -> ScoringResult:
        """
        Score a candidate using SRS Section 4.4.2 rubric.

        Per SRS FR-16: Each candidate shall be scored using the rubric.
        Per SRS FR-17: Candidates below minimum threshold (default: 5) shall be discarded.

        Args:
            tweet: Tweet candidate to score
            lane: Search lane configuration for brand context

        Returns:
            ScoringResult with score and breakdown
        """
        brand_key = None
        brand_config = None

        if lane is not None:
            brand_key = str(getattr(lane, "brand_family", "") or "").strip().lower()
            if brand_key:
                brand_config = get_brand(brand_key)

        return score_candidate_xss(
            tweet_text=tweet.text,
            author_username=tweet.author_username,
            brand_config=brand_config,
            brand_key=brand_key,
        )

    def _passes_xss_score_threshold(self, scoring_result: ScoringResult) -> bool:
        """
        Check if score meets minimum threshold per SRS FR-17.

        The system shall discard candidates with a score below the
        configurable minimum threshold (default: 5).

        Args:
            scoring_result: Result from score_candidate_xss

        Returns:
            True if score meets or exceeds threshold
        """
        minimum_threshold = int(getattr(self._config, "xss_minimum_score_threshold", 5) or 5)
        return scoring_result.total_score >= minimum_threshold

    @staticmethod
    def _coerce_analysis_score(analysis: dict[str, Any]) -> int | None:
        raw_score = analysis.get("score")
        try:
            if raw_score is not None:
                return int(round(float(raw_score)))
        except (TypeError, ValueError):
            pass

        raw_confidence = analysis.get("confidence")
        try:
            if raw_confidence is not None:
                confidence = min(1.0, max(0.0, float(raw_confidence)))
                return int(round(confidence * 10))
        except (TypeError, ValueError):
            pass
        return None

    def _score_xai_candidate(
        self,
        prepared: PreparedReviewCandidate,
        lane: Any | None = None,
    ) -> tuple[int, str, bool]:
        lane_hint = str(
            getattr(lane, "category_hint", "") or prepared.tweet.category_hint or ""
        ).strip().lower()

        if lane_hint == "competitor_complaint":
            scoring_result = self._score_candidate_xss(prepared.tweet, lane)
            return (
                scoring_result.total_score,
                scoring_result.reason or "no_signals",
                self._passes_xss_score_threshold(scoring_result),
            )

        score_value = self._coerce_analysis_score(prepared.analysis)
        if score_value is None:
            score_value = 0
        reason = str(prepared.analysis.get("reason") or "lane_match").strip() or "lane_match"
        minimum_threshold = int(getattr(self._config, "xss_minimum_score_threshold", 5) or 5)
        return score_value, reason, score_value >= minimum_threshold

    @staticmethod
    def _xss_output_lane_name(category_hint: str) -> str:
        normalized = str(category_hint or "").strip().lower()
        if normalized == "solution_seeker":
            return "solution_seeker"
        return "competitor_complaint"

    def _xss_output_category(self, raw_category: Any, lane: Any | None = None) -> str:
        normalized = str(raw_category or "").strip().lower().replace("_", "-")
        if normalized in {"solution-seeker", "solution-seekers"}:
            return "solution_seeker"
        if normalized in {"competitor-complaint", "competitor-complaints"}:
            return "competitor_complaint"

        lane_hint = str(getattr(lane, "category_hint", "") or "").strip().lower()
        return self._xss_output_lane_name(lane_hint)

    def _build_xss_output_map(
        self,
        lane_lookup: dict[str, Any],
    ) -> dict[str, XSSSearchCycleOutput]:
        outputs: dict[str, XSSSearchCycleOutput] = {}
        for job in getattr(self._runtime, "last_xss_due_jobs", []) or []:
            query = getattr(getattr(job, "query", None), "query", "")
            lane = lane_lookup.get(query) or getattr(job, "query", None)
            outputs[query] = self._get_or_create_xss_output(outputs, query, lane)
        return outputs

    def _get_or_create_xss_output(
        self,
        outputs: dict[str, XSSSearchCycleOutput],
        source_query: str,
        lane: Any | None = None,
    ) -> XSSSearchCycleOutput:
        if source_query in outputs:
            return outputs[source_query]

        lower_bound, upper_bound = self._resolve_xss_filter_bounds(lane)
        output = create_search_cycle_output(
            lane=self._xss_output_lane_name(getattr(lane, "category_hint", "")),
            brand_key=str(getattr(lane, "brand_family", "") or "").strip().lower() or None,
            restart_time_utc=getattr(self._runtime, "restart_catchup_start_utc", None),
            filter_lower_bound=lower_bound,
            filter_upper_bound=upper_bound,
        )
        outputs[source_query] = output
        return output

    async def _fetch_standard_candidates(self) -> list[TweetCandidate]:
        """Fetch candidates from standard search provider."""
        all_candidates: list[TweetCandidate] = []
        self._runtime.last_fetch_summary = ""
        now_ts = time.time()

        if self._runtime.provider_paused_until > now_ts:
            wait_seconds = max(1, int(self._runtime.provider_paused_until - now_ts))
            self._runtime.last_fetch_summary = f"provider_paused:{wait_seconds}"
            log.warning(
                "Skipping %s scan for %ss: %s",
                self._config.search_provider,
                wait_seconds,
                self._runtime.provider_pause_reason or "provider paused",
            )
            return []

        self._runtime.provider_paused_until = 0.0
        self._runtime.provider_pause_reason = ""

        request_budget = max(0, int(getattr(self._config, "max_api_requests_per_scan", 0) or 0))
        due_jobs = select_due_queries(
            self._config,
            self._runtime,
            request_budget,
            brand_direct_enabled=False,
        )
        if not due_jobs:
            self._runtime.last_fetch_summary = "no_due_queries"
            return []

        try:
            for job in due_jobs:
                query_config = job.query
                category_hint = query_config.category_hint
                compiled_query = self._build_standard_provider_query(query_config)
                self._runtime.last_query_run[query_config.query] = time.time()

                results = await self._search_provider.search(
                    query=compiled_query,
                    query_type=job.query_type,
                )

                self._runtime.api_requests_made += 1
                tweets = self._coerce_tweet_list(results)
                self._runtime.tweets_fetched += len(tweets)
                if tweets:
                    self._runtime.empty_scan_counts[query_config.query] = 0
                else:
                    self._runtime.empty_scan_counts[query_config.query] = (
                        self._runtime.empty_scan_counts.get(query_config.query, 0) + 1
                    )

                # Parse into TweetCandidate objects
                for tweet_data in tweets[:20]:
                    candidate = self._parse_tweet(
                        tweet_data,
                        category_hint,
                        search_query=query_config.query,
                        source_tab=job.query_type,
                    )
                    if candidate:
                        all_candidates.append(candidate)

                await asyncio.sleep(1)

            if not all_candidates:
                self._runtime.last_fetch_summary = "zero_provider_results"
            else:
                self._runtime.last_fetch_summary = f"candidates:{len(all_candidates)}"

        except TwitterApiIoRateLimitError as exc:
            pause_seconds = max(1, exc.retry_after_seconds or self._config.poll_interval)
            self._runtime.provider_paused_until = time.time() + pause_seconds
            self._runtime.provider_pause_reason = "twitterapi.io rate limited"
            self._runtime.last_fetch_summary = f"provider_paused:{pause_seconds}"
            log.warning(
                "Pausing twitterapi.io provider for %ss after rate limit",
                pause_seconds,
            )
            await self._notification_service.send_status(
                "twitterapi.io rate limited the bot. Search is paused until the retry window."
            )
        except TwitterApiIoAuthError as exc:
            self._runtime.last_fetch_summary = f"error:{exc}"
            log.error("Search failed: %s", exc)
            await self._notification_service.send_status(
                "twitterapi.io auth failed. Check TWITTERAPI_IO_API_KEY."
            )
        except Exception as exc:
            detail = _describe_exception(exc)
            log.error("Search failed: %s", detail)
            self._runtime.last_fetch_summary = f"error:{detail}"

        return all_candidates

    def _build_standard_provider_query(self, query_config: Any) -> str:
        query = build_standard_search_query(query_config)
        if not query:
            return query

        lower_date, upper_date = self._resolve_standard_search_date_window()
        extra_terms: list[str] = []

        if lower_date and "since:" not in query and "since_time:" not in query:
            extra_terms.append(f"since:{lower_date}")

        if upper_date and "until:" not in query and "until_time:" not in query:
            exclusive_upper = (
                datetime.fromisoformat(upper_date).date() + timedelta(days=1)
            ).isoformat()
            extra_terms.append(f"until:{exclusive_upper}")

        if not extra_terms:
            return query
        return f"{query} {' '.join(extra_terms)}"

    def _resolve_standard_search_date_window(self) -> tuple[str | None, str | None]:
        end_date = datetime.now(timezone.utc).date()
        start_date = None

        if getattr(self._config, "search_since_days", None) is not None:
            start_date = end_date - timedelta(days=int(self._config.search_since_days))

        catchup_start = getattr(self._runtime, "restart_catchup_start_utc", None)
        catchup_end = getattr(self._runtime, "restart_catchup_end_utc", None)
        if catchup_start:
            catchup_start_date = catchup_start.astimezone(timezone.utc).date()
            start_date = catchup_start_date if start_date is None else min(start_date, catchup_start_date)
        if catchup_end:
            end_date = max(end_date, catchup_end.astimezone(timezone.utc).date())

        if start_date is None:
            return None, None
        return start_date.isoformat(), end_date.isoformat()

    @staticmethod
    def _coerce_tweet_list(results: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize provider responses into a flat tweet list."""
        if not isinstance(results, dict):
            return []

        for key in ("tweets", "data", "results"):
            value = results.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested_tweets = value.get("tweets")
                if isinstance(nested_tweets, list):
                    return [item for item in nested_tweets if isinstance(item, dict)]

        return []

    def _parse_tweet(
        self,
        tweet_data: dict[str, Any],
        category_hint: str,
        *,
        search_query: str = "",
        source_tab: str = "",
    ) -> TweetCandidate | None:
        """Parse raw tweet data into TweetCandidate."""
        try:
            if not isinstance(tweet_data, dict):
                return None

            tweet_id = str(tweet_data.get("id") or tweet_data.get("tweet_id") or "").strip()
            text = str(tweet_data.get("text") or tweet_data.get("fullText") or "").strip()
            if not tweet_id or not text:
                return None

            author = tweet_data.get("author")
            if not isinstance(author, dict):
                author = {}

            author_username = str(
                tweet_data.get("author_username")
                or author.get("userName")
                or author.get("screen_name")
                or "unknown"
            ).strip().lstrip("@") or "unknown"
            author_name = str(
                tweet_data.get("author_name")
                or author.get("name")
                or author_username
            ).strip() or author_username

            created = self._parse_datetime_value(
                tweet_data.get("created_at")
                or tweet_data.get("createdAt")
                or tweet_data.get("tweetDate")
                or tweet_data.get("created")
            )

            now = datetime.now(timezone.utc)
            age_minutes = (now - created).total_seconds() / 60

            url = str(tweet_data.get("url") or "").strip()
            if not url:
                if author_username and author_username != "unknown":
                    url = f"https://x.com/{author_username}/status/{tweet_id}"
                else:
                    url = f"https://x.com/i/status/{tweet_id}"

            return TweetCandidate(
                tweet_id=tweet_id,
                text=text,
                author_username=author_username,
                author_name=author_name,
                author_followers=self._coerce_int(
                    tweet_data.get("author_followers")
                    or author.get("followers")
                    or author.get("followersCount")
                ),
                url=url,
                created_at=created,
                likes=self._coerce_int(tweet_data.get("likes") or tweet_data.get("likeCount")),
                retweets=self._coerce_int(
                    tweet_data.get("retweets") or tweet_data.get("retweetCount")
                ),
                replies=self._coerce_int(
                    tweet_data.get("replies") or tweet_data.get("replyCount")
                ),
                quotes=self._coerce_int(tweet_data.get("quotes") or tweet_data.get("quoteCount")),
                views=self._coerce_int(tweet_data.get("views") or tweet_data.get("viewCount")),
                age_minutes=age_minutes,
                source_tab=str(
                    tweet_data.get("source_tab")
                    or tweet_data.get("sourceTab")
                    or source_tab
                    or "Top"
                ),
                search_query=str(
                    tweet_data.get("search_query")
                    or tweet_data.get("query")
                    or search_query
                    or ""
                ),
                category_hint=category_hint,
            )
        except Exception as exc:
            log.warning(f"Failed to parse tweet: {exc}")
            return None

    @staticmethod
    def _coerce_int(raw_value: Any) -> int:
        """Coerce provider metric values into integers."""
        try:
            return int(raw_value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _parse_datetime_value(raw_value: Any) -> datetime:
        """Parse multiple provider timestamp formats with a safe fallback."""
        now = datetime.now(timezone.utc)
        if raw_value in (None, ""):
            return now

        try:
            if isinstance(raw_value, (int, float)):
                return datetime.fromtimestamp(raw_value, tz=timezone.utc)

            text = str(raw_value).strip()
            if not text:
                return now
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

    async def _classify_tweet(self, tweet: TweetCandidate) -> dict[str, Any] | None:
        """Classify a tweet using the AI classifier."""
        if not self._classifier:
            log.warning("No classifier configured")
            return None

        try:
            return await self._classifier.classify_and_generate(
                tweet=tweet,
                brand_context=self._config.brand_context,
                num_reply_options=self._config.num_reply_options,
            )
        except Exception as exc:
            log.error(f"Classification failed for {tweet.tweet_id}: {exc}")
            return None

    async def _queue_for_review(
        self, tweet: TweetCandidate, analysis: dict[str, Any]
    ) -> bool:
        """Queue a candidate for Discord review."""
        category = analysis.get("category", "irrelevant")
        sentiment = analysis.get("sentiment", "neutral")

        if category == TweetCategory.IRRELEVANT.value:
            log.info(f"Skipping irrelevant tweet {tweet.tweet_id}")
            self._repository.mark_processed(
                tweet_id=tweet.tweet_id,
                url=tweet.url,
                text=tweet.text,
                author=tweet.author_username,
                category=category,
                sentiment=sentiment,
                search_query=tweet.search_query,
            )
            return False

        reply_texts = [r["text"] for r in analysis.get("replies", [])]

        # Mark as processed
        self._repository.mark_processed(
            tweet_id=tweet.tweet_id,
            url=tweet.url,
            text=tweet.text,
            author=tweet.author_username,
            category=category,
            sentiment=sentiment,
            search_query=tweet.search_query,
        )

        # Send to Discord for review
        result = await self._notification_service.send_approval(tweet, analysis)
        if not result:
            return False

        msg_id, ch_id = result
        self._repository.save_pending(
            tweet_id=tweet.tweet_id,
            replies=reply_texts,
            message_id=msg_id,
            channel_id=ch_id,
            category=category,
        )

        log.info(f"-> Sent to Discord #{category}: {tweet.tweet_id}")
        return True

    def _log_no_candidates(self) -> None:
        """Log reason for no candidates."""
        summary = self._runtime.last_fetch_summary
        if summary == "no_due_queries":
            log.info("No live-search queries were due this scan")
        elif summary.startswith("provider_paused:"):
            log.info("Live search is paused: %s", self._runtime.provider_pause_reason)
        elif summary == "zero_provider_results":
            log.info("%s queries ran but returned 0 candidates", self._config.search_provider)
        else:
            log.info("No candidates returned by %s", self._config.search_provider)

    def _log_discarded(self, discarded: list[tuple[str, float, str]]) -> None:
        """Log discarded candidates."""
        discarded_lines = format_discarded_candidates(discarded)
        if discarded_lines:
            log.info("Top discarded candidates: %s", " | ".join(discarded_lines))
            if self._config.debug_discarded_to_status:
                asyncio.create_task(
                    self._notification_service.send_status(
                        "Discarded sample: " + " | ".join(discarded_lines)
                    )
                )
