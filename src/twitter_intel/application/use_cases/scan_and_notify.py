"""
Scan and notify use case.

Orchestrates the main scan pipeline: fetch candidates from search provider,
score and filter locally, send to AI classifier, and queue for Discord review.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from twitter_intel.config import Config, SearchRuntime
from twitter_intel.domain.entities.category import TweetCategory
from twitter_intel.domain.entities.tweet import PreparedReviewCandidate, TweetCandidate
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
from twitter_intel.infrastructure.search.xai_client import XaiClient
from twitter_intel.infrastructure.search.xai_live_search import (
    fetch_candidates_from_xai_search,
)

log = logging.getLogger(__name__)


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

        xAI returns pre-classified candidates with replies.
        """
        prepared_candidates = await self._fetch_xai_candidates()
        self._runtime.tweets_fetched += len(prepared_candidates)

        if not prepared_candidates:
            self._log_no_candidates()
            return ScanResult(
                queued_count=0,
                total_candidates=0,
                filtered_count=0,
                message="No candidates from xAI x_search",
            )

        # Track seen IDs and process candidates
        seen_ids: set[str] = set()
        queued_count = 0
        discarded: list[tuple[str, float, str]] = []

        for prepared in prepared_candidates:
            tweet = prepared.tweet

            # If xAI keeps resurfacing the same stale post, ignore it after
            # the first rejection so the status channel does not get spammed.
            if tweet.tweet_id in self._runtime.stale_candidate_ids:
                continue

            # Skip duplicates
            if tweet.tweet_id in seen_ids:
                self._runtime.duplicates_dropped += 1
                discarded.append((tweet.tweet_id, tweet.local_score, "duplicate_in_scan"))
                continue

            # Skip already processed
            if self._repository.is_processed(tweet.tweet_id):
                self._runtime.duplicates_dropped += 1
                discarded.append((tweet.tweet_id, tweet.local_score, "already_processed"))
                continue

            seen_ids.add(tweet.tweet_id)

            # Enforce age cap parity with the standard provider flow
            if tweet.age_minutes > self._config.max_tweet_age_minutes:
                self._runtime.locally_filtered_out += 1
                self._runtime.stale_candidate_ids.add(tweet.tweet_id)
                discarded.append((tweet.tweet_id, tweet.local_score, "too_old"))
                continue

            # Check approval cap
            if queued_count >= self._config.max_discord_approvals_per_scan:
                self._runtime.locally_filtered_out += 1
                discarded.append((tweet.tweet_id, tweet.local_score, "trimmed_by_cap"))
                continue

            # Queue for review
            if await self._queue_for_review(tweet, prepared.analysis):
                queued_count += 1
                self._runtime.queued_to_discord += 1

            await asyncio.sleep(1)

        log.info(
            "Grok prepared %s candidate(s), %s queued to Discord",
            len(prepared_candidates),
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
            log.error(f"xAI search failed: {exc}")
            self._runtime.last_fetch_summary = f"error:{exc}"
            return []

    async def _fetch_standard_candidates(self) -> list[TweetCandidate]:
        """Fetch candidates from standard search provider."""
        all_candidates: list[TweetCandidate] = []
        self._runtime.last_fetch_summary = ""

        try:
            # Get search queries from config
            for query_config in self._config.search_queries:
                query = query_config.query
                category_hint = query_config.category_hint

                results = await self._search_provider.search(
                    query=query,
                    query_type="Top",
                )

                self._runtime.api_requests_made += 1
                tweets = self._coerce_tweet_list(results)
                self._runtime.tweets_fetched += len(tweets)

                # Parse into TweetCandidate objects
                for tweet_data in tweets[:20]:
                    candidate = self._parse_tweet(tweet_data, category_hint)
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
            log.error(f"Search failed: {exc}")
            self._runtime.last_fetch_summary = f"error:{exc}"

        return all_candidates

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
        self, tweet_data: dict[str, Any], category_hint: str
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
                source_tab=str(tweet_data.get("source_tab") or tweet_data.get("sourceTab") or "Top"),
                search_query=str(tweet_data.get("search_query") or tweet_data.get("query") or ""),
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
