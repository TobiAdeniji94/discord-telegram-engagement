"""
Tests for scan and notify use case.
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from twitter_intel.application.use_cases.scan_and_notify import (
    ScanAndNotifyUseCase,
    ScanResult,
)
from twitter_intel.config import SearchRuntime
from twitter_intel.config.search_queries import SearchQuery
from twitter_intel.exceptions import TwitterApiIoRateLimitError


class TestScanAndNotifyUseCase:
    """Tests for ScanAndNotifyUseCase class."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = MagicMock()
        config.search_provider = "twitterapi_io"
        config.max_tweet_age_minutes = 120
        config.max_local_candidates_per_scan = 50
        config.max_ai_candidates_per_scan = 10
        config.max_discord_approvals_per_scan = 5
        config.debug_discarded_to_status = False
        config.brand_context = "Test brand context"
        config.num_reply_options = 3
        config.poll_interval = 300
        config.search_queries = []
        config.search_since_days = 1
        config.search_event_mode = "off"
        config.search_event_anchor_utc = None
        config.search_event_min_offset_minutes = 30
        config.search_event_max_offset_minutes = 360
        config.search_event_brands = []
        config.xss_window_start_offset_minutes = 30
        config.xss_window_end_offset_minutes = 360
        config.xss_minimum_score_threshold = 5
        config.max_api_requests_per_scan = 8
        config.enable_latest_fallback = False
        config.lane_empty_scan_threshold = 3
        return config

    @pytest.fixture
    def mock_repository(self):
        """Create mock repository."""
        repo = MagicMock()
        repo.is_processed = MagicMock(return_value=False)
        repo.get_processed_ids = MagicMock(return_value=set())
        repo.mark_processed = MagicMock()
        repo.save_pending = MagicMock()
        return repo

    @pytest.fixture
    def mock_search_provider(self):
        """Create mock search provider."""
        provider = MagicMock()
        provider.name = "twitterapi_io"
        provider.search = AsyncMock(return_value={"tweets": []})
        return provider

    @pytest.fixture
    def mock_classifier(self):
        """Create mock classifier."""
        classifier = MagicMock()
        classifier.name = "gemini"
        classifier.classify_and_generate = AsyncMock(return_value={
            "category": "brand-mentions",
            "sentiment": "positive",
            "confidence": 0.9,
            "replies": [
                {"tone": "friendly", "text": "Reply 1", "strategy": "test"},
            ],
        })
        return classifier

    @pytest.fixture
    def mock_notification_service(self):
        """Create mock notification service."""
        service = MagicMock()
        service.send_approval = AsyncMock(return_value=("msg123", "ch456"))
        service.send_status = AsyncMock()
        return service

    @pytest.fixture
    def runtime(self):
        """Create runtime instance."""
        return SearchRuntime()

    @pytest.fixture
    def use_case(
        self,
        mock_config,
        mock_repository,
        mock_search_provider,
        mock_classifier,
        mock_notification_service,
        runtime,
    ):
        """Create use case with mocks."""
        return ScanAndNotifyUseCase(
            config=mock_config,
            repository=mock_repository,
            search_provider=mock_search_provider,
            classifier=mock_classifier,
            notification_service=mock_notification_service,
            runtime=runtime,
        )

    async def test_manual_only_mode(self, use_case, mock_config):
        """Should skip scan in manual_only mode."""
        mock_config.search_provider = "manual_only"

        result = await use_case.execute()

        assert result.queued_count == 0
        assert "disabled" in result.message.lower()

    async def test_no_candidates_from_provider(self, use_case, mock_search_provider):
        """Should handle empty results from provider."""
        mock_search_provider.search = AsyncMock(return_value={"tweets": []})

        result = await use_case.execute()

        assert result.queued_count == 0
        assert result.total_candidates == 0

    async def test_xai_flow_no_candidates(self, use_case, mock_config, mock_search_provider):
        """Should handle xAI flow with no candidates."""
        mock_config.search_provider = "xai_x_search"
        mock_search_provider.search = AsyncMock(return_value={"candidates": []})

        result = await use_case.execute()

        assert result.queued_count == 0

    async def test_xai_flow_with_candidates_without_replies(
        self, mock_config, mock_repository, mock_search_provider,
        mock_classifier, mock_notification_service, runtime
    ):
        """Should queue xAI candidates even when no reply options were generated."""
        mock_config.search_provider = "xai_x_search"

        # Create a mock PreparedReviewCandidate
        from twitter_intel.domain.entities.tweet import TweetCandidate, PreparedReviewCandidate

        tweet = TweetCandidate(
            tweet_id="123",
            text="Test tweet",
            author_username="user",
            author_name="User",
            author_followers=1000,
            url="https://x.com/user/status/123",
            created_at=datetime.now(timezone.utc),
            likes=10,
            retweets=5,
            replies=3,
            quotes=2,
            views=1000,
            age_minutes=30.0,
            source_tab="Top",
            search_query="test",
            category_hint="brand_mention",
            local_score=50.0,
        )
        prepared = PreparedReviewCandidate(
            tweet=tweet,
            analysis={
                "category": "brand-mentions",
                "sentiment": "positive",
                "confidence": 0.9,
                "replies": [],
            },
            provider="xai_x_search",
            source_query="test query",
        )

        use_case = ScanAndNotifyUseCase(
            config=mock_config,
            repository=mock_repository,
            search_provider=mock_search_provider,
            classifier=mock_classifier,
            notification_service=mock_notification_service,
            runtime=runtime,
        )
        use_case._fetch_xai_candidates = AsyncMock(return_value=[prepared])

        result = await use_case.execute()

        assert result.queued_count == 1
        assert runtime.tweets_fetched == 1
        mock_notification_service.send_approval.assert_called_once()
        mock_repository.save_pending.assert_called_once()
        assert mock_repository.save_pending.call_args.kwargs["replies"] == []

    async def test_skips_already_processed(
        self, mock_config, mock_repository, mock_search_provider,
        mock_classifier, mock_notification_service, runtime
    ):
        """Should skip already processed tweets in xAI flow."""
        mock_config.search_provider = "xai_x_search"
        mock_repository.is_processed = MagicMock(return_value=True)

        from twitter_intel.domain.entities.tweet import TweetCandidate, PreparedReviewCandidate

        tweet = TweetCandidate(
            tweet_id="123",
            text="Test tweet",
            author_username="user",
            author_name="User",
            author_followers=1000,
            url="https://x.com/user/status/123",
            created_at=datetime.now(timezone.utc),
            likes=10,
            retweets=5,
            replies=3,
            quotes=2,
            views=1000,
            age_minutes=30.0,
            source_tab="Top",
            search_query="test",
            category_hint="brand_mention",
        )
        prepared = PreparedReviewCandidate(
            tweet=tweet,
            analysis={"category": "brand-mentions", "replies": []},
            provider="xai_x_search",
            source_query="test",
        )

        use_case = ScanAndNotifyUseCase(
            config=mock_config,
            repository=mock_repository,
            search_provider=mock_search_provider,
            classifier=mock_classifier,
            notification_service=mock_notification_service,
            runtime=runtime,
        )
        use_case._fetch_xai_candidates = AsyncMock(return_value=[prepared])

        result = await use_case.execute()

        assert result.queued_count == 0
        assert runtime.duplicates_dropped == 1

    async def test_xai_flow_skips_too_old_candidates(
        self, mock_config, mock_repository, mock_search_provider,
        mock_classifier, mock_notification_service, runtime
    ):
        """Should drop stale xAI candidates using max_tweet_age_minutes."""
        mock_config.search_provider = "xai_x_search"
        mock_config.max_tweet_age_minutes = 120

        from twitter_intel.domain.entities.tweet import TweetCandidate, PreparedReviewCandidate

        tweet = TweetCandidate(
            tweet_id="123",
            text="Old candidate",
            author_username="user",
            author_name="User",
            author_followers=1000,
            url="https://x.com/user/status/123",
            created_at=datetime.now(timezone.utc),
            likes=10,
            retweets=5,
            replies=3,
            quotes=2,
            views=1000,
            age_minutes=720.0,
            source_tab="Top",
            search_query="test",
            category_hint="brand_mention",
        )
        prepared = PreparedReviewCandidate(
            tweet=tweet,
            analysis={"category": "brand-mentions", "replies": []},
            provider="xai_x_search",
            source_query="test",
        )

        use_case = ScanAndNotifyUseCase(
            config=mock_config,
            repository=mock_repository,
            search_provider=mock_search_provider,
            classifier=mock_classifier,
            notification_service=mock_notification_service,
            runtime=runtime,
        )
        use_case._fetch_xai_candidates = AsyncMock(return_value=[prepared])

        result = await use_case.execute()

        assert result.queued_count == 0
        assert runtime.locally_filtered_out == 1
        assert "123" in runtime.stale_candidate_ids
        mock_notification_service.send_approval.assert_not_called()

    async def test_xai_flow_suppresses_repeat_stale_discards(
        self, mock_config, mock_repository, mock_search_provider,
        mock_classifier, mock_notification_service, runtime
    ):
        """Should not repost the same stale discard on later scans."""
        mock_config.search_provider = "xai_x_search"
        mock_config.max_tweet_age_minutes = 120
        mock_config.debug_discarded_to_status = True

        from twitter_intel.domain.entities.tweet import TweetCandidate, PreparedReviewCandidate

        tweet = TweetCandidate(
            tweet_id="123",
            text="Old candidate",
            author_username="user",
            author_name="User",
            author_followers=1000,
            url="https://x.com/user/status/123",
            created_at=datetime.now(timezone.utc),
            likes=10,
            retweets=5,
            replies=3,
            quotes=2,
            views=1000,
            age_minutes=720.0,
            source_tab="Top",
            search_query="test",
            category_hint="brand_mention",
        )
        prepared = PreparedReviewCandidate(
            tweet=tweet,
            analysis={"category": "brand-mentions", "replies": []},
            provider="xai_x_search",
            source_query="test",
        )

        use_case = ScanAndNotifyUseCase(
            config=mock_config,
            repository=mock_repository,
            search_provider=mock_search_provider,
            classifier=mock_classifier,
            notification_service=mock_notification_service,
            runtime=runtime,
        )
        use_case._fetch_xai_candidates = AsyncMock(return_value=[prepared])

        await use_case.execute()
        await asyncio.sleep(0)
        await use_case.execute()
        await asyncio.sleep(0)

        assert runtime.locally_filtered_out == 1
        assert runtime.tweets_fetched == 2
        mock_notification_service.send_status.assert_called_once()

    async def test_xai_flow_excludes_official_lane_authors(
        self, mock_config, mock_repository, mock_search_provider,
        mock_classifier, mock_notification_service, runtime
    ):
        """Should drop xAI candidates authored by official brand handles."""
        mock_config.search_provider = "xai_x_search"
        mock_config.search_queries = [
            SearchQuery(
                query="Find Chipper complaints",
                category_hint="competitor_complaint",
                description="Chipper complaints",
                lane_id="complaint-chipper",
                intent_summary="Find complaints about Chipper from real users.",
                brand_family="chipper",
                exclude_author_handles=["chippercashapp"],
            )
        ]

        from twitter_intel.domain.entities.tweet import TweetCandidate, PreparedReviewCandidate

        tweet = TweetCandidate(
            tweet_id="123",
            text="Official support update",
            author_username="chippercashapp",
            author_name="Chipper",
            author_followers=1000,
            url="https://x.com/chippercashapp/status/123",
            created_at=datetime.now(timezone.utc),
            likes=10,
            retweets=5,
            replies=3,
            quotes=2,
            views=1000,
            age_minutes=5.0,
            source_tab="Top",
            search_query="Find Chipper complaints",
            category_hint="competitor_complaint",
        )
        prepared = PreparedReviewCandidate(
            tweet=tweet,
            analysis={"category": "competitor-complaints", "replies": []},
            provider="xai_x_search",
            source_query="Find Chipper complaints",
        )

        use_case = ScanAndNotifyUseCase(
            config=mock_config,
            repository=mock_repository,
            search_provider=mock_search_provider,
            classifier=mock_classifier,
            notification_service=mock_notification_service,
            runtime=runtime,
        )
        use_case._fetch_xai_candidates = AsyncMock(return_value=[prepared])

        result = await use_case.execute()

        assert result.queued_count == 0
        assert runtime.locally_filtered_out == 1
        mock_notification_service.send_approval.assert_not_called()

    async def test_xai_flow_enforces_anchored_event_window(
        self, mock_config, mock_repository, mock_search_provider,
        mock_classifier, mock_notification_service, runtime
    ):
        """Should drop xAI candidates outside the anchored-event window."""
        mock_config.search_provider = "xai_x_search"
        mock_config.search_event_mode = "anchored"
        mock_config.search_event_anchor_utc = datetime(2026, 3, 19, 8, 0, tzinfo=timezone.utc)
        mock_config.search_event_min_offset_minutes = 30
        mock_config.search_event_max_offset_minutes = 360
        mock_config.search_event_brands = ["chipper"]
        mock_config.search_queries = [
            SearchQuery(
                query="Find Chipper complaints",
                category_hint="competitor_complaint",
                description="Chipper complaints",
                lane_id="complaint-chipper",
                intent_summary="Find complaints about Chipper from real users.",
                brand_family="chipper",
            )
        ]

        from twitter_intel.domain.entities.tweet import TweetCandidate, PreparedReviewCandidate

        tweet = TweetCandidate(
            tweet_id="123",
            text="Too early for event window",
            author_username="realuser",
            author_name="User",
            author_followers=1000,
            url="https://x.com/realuser/status/123",
            created_at=datetime(2026, 3, 19, 8, 10, tzinfo=timezone.utc),
            likes=10,
            retweets=5,
            replies=3,
            quotes=2,
            views=1000,
            age_minutes=10.0,
            source_tab="Top",
            search_query="Find Chipper complaints",
            category_hint="competitor_complaint",
        )
        prepared = PreparedReviewCandidate(
            tweet=tweet,
            analysis={"category": "competitor-complaints", "replies": []},
            provider="xai_x_search",
            source_query="Find Chipper complaints",
        )

        use_case = ScanAndNotifyUseCase(
            config=mock_config,
            repository=mock_repository,
            search_provider=mock_search_provider,
            classifier=mock_classifier,
            notification_service=mock_notification_service,
            runtime=runtime,
        )
        use_case._fetch_xai_candidates = AsyncMock(return_value=[prepared])

        result = await use_case.execute()

        assert result.queued_count == 0
        assert runtime.locally_filtered_out == 1
        mock_notification_service.send_approval.assert_not_called()

    async def test_xai_flow_enforces_restart_catchup_window(
        self, mock_config, mock_repository, mock_search_provider,
        mock_classifier, mock_notification_service, runtime
    ):
        """Should drop xAI candidates outside the automatic restart catch-up window."""
        mock_config.search_provider = "xai_x_search"
        mock_config.search_queries = [
            SearchQuery(
                query="Find Wise complaints",
                category_hint="competitor_complaint",
                description="Wise complaints",
                lane_id="complaint-wise",
                intent_summary="Find complaints about Wise from real users.",
                brand_family="wise",
            )
        ]
        runtime.restart_catchup_start_utc = datetime(2026, 3, 19, 8, 0, tzinfo=timezone.utc)
        runtime.restart_catchup_end_utc = datetime(2026, 3, 19, 9, 0, tzinfo=timezone.utc)

        from twitter_intel.domain.entities.tweet import TweetCandidate, PreparedReviewCandidate

        tweet = TweetCandidate(
            tweet_id="123",
            text="Outside catch-up window",
            author_username="realuser",
            author_name="User",
            author_followers=1000,
            url="https://x.com/realuser/status/123",
            created_at=datetime(2026, 3, 19, 9, 30, tzinfo=timezone.utc),
            likes=10,
            retweets=5,
            replies=3,
            quotes=2,
            views=1000,
            age_minutes=10.0,
            source_tab="Top",
            search_query="Find Wise complaints",
            category_hint="competitor_complaint",
        )
        prepared = PreparedReviewCandidate(
            tweet=tweet,
            analysis={"category": "competitor-complaints", "replies": []},
            provider="xai_x_search",
            source_query="Find Wise complaints",
        )

        use_case = ScanAndNotifyUseCase(
            config=mock_config,
            repository=mock_repository,
            search_provider=mock_search_provider,
            classifier=mock_classifier,
            notification_service=mock_notification_service,
            runtime=runtime,
        )
        use_case._fetch_xai_candidates = AsyncMock(return_value=[prepared])

        result = await use_case.execute()

        assert result.queued_count == 0
        assert runtime.locally_filtered_out == 1
        mock_notification_service.send_approval.assert_not_called()

    async def test_xai_flow_enforces_local_xss_score_threshold(
        self, mock_config, mock_repository, mock_search_provider,
        mock_classifier, mock_notification_service, runtime
    ):
        """Should discard low-signal competitor complaints using local XSS scoring."""
        mock_config.search_provider = "xai_x_search"
        mock_config.search_queries = [
            SearchQuery(
                query="Find Grey complaints",
                category_hint="competitor_complaint",
                description="Grey complaints",
                lane_id="complaint-grey",
                intent_summary="Find complaints about Grey from real users.",
                brand_family="grey",
            )
        ]

        from twitter_intel.domain.entities.tweet import TweetCandidate, PreparedReviewCandidate

        tweet = TweetCandidate(
            tweet_id="123",
            text="lol grey",
            author_username="realuser",
            author_name="User",
            author_followers=1000,
            url="https://x.com/realuser/status/123",
            created_at=datetime.now(timezone.utc),
            likes=0,
            retweets=0,
            replies=0,
            quotes=0,
            views=0,
            age_minutes=5.0,
            source_tab="Top",
            search_query="Find Grey complaints",
            category_hint="competitor_complaint",
        )
        prepared = PreparedReviewCandidate(
            tweet=tweet,
            analysis={"category": "competitor-complaints", "replies": []},
            provider="xai_x_search",
            source_query="Find Grey complaints",
        )

        use_case = ScanAndNotifyUseCase(
            config=mock_config,
            repository=mock_repository,
            search_provider=mock_search_provider,
            classifier=mock_classifier,
            notification_service=mock_notification_service,
            runtime=runtime,
        )
        use_case._fetch_xai_candidates = AsyncMock(return_value=[prepared])

        result = await use_case.execute()

        assert result.queued_count == 0
        assert runtime.locally_filtered_out == 1
        mock_notification_service.send_approval.assert_not_called()

    def test_parse_twitterapi_io_payload_shape(self, use_case):
        """Should parse twitterapi.io-style payload fields without warnings."""
        tweet = use_case._parse_tweet(
            {
                "id": "123",
                "fullText": "A payment app issue",
                "createdAt": "2026-03-04T12:00:00Z",
                "likeCount": 4,
                "retweetCount": 2,
                "replyCount": 1,
                "quoteCount": 0,
                "viewCount": 50,
                "author": {
                    "userName": "example_user",
                    "name": "Example User",
                    "followersCount": 42,
                },
            },
            "competitor_complaint",
        )

        assert tweet is not None
        assert tweet.tweet_id == "123"
        assert tweet.author_username == "example_user"
        assert tweet.author_followers == 42
        assert tweet.likes == 4
        assert tweet.url == "https://x.com/example_user/status/123"

    async def test_twitterapi_rate_limit_sets_provider_pause(
        self,
        use_case,
        mock_config,
        mock_search_provider,
        mock_notification_service,
        runtime,
        monkeypatch,
    ):
        """Should pause the provider when twitterapi.io returns 429."""
        mock_config.search_queries = [
            SearchQuery(
                query="test query",
                category_hint="competitor_complaint",
                description="Test query",
                query_type="Top",
                cooldown_seconds=60,
            )
        ]
        mock_search_provider.search = AsyncMock(
            side_effect=TwitterApiIoRateLimitError(
                "twitterapi.io rate limited",
                retry_after_seconds=120,
            )
        )
        base_ts = 1760000000.0
        expected_resume_text = (
            datetime.fromtimestamp(base_ts + 120, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

        monkeypatch.setattr(
            "twitter_intel.application.use_cases.scan_and_notify.time.time",
            lambda: base_ts,
        )

        result = await use_case.execute()

        assert result.queued_count == 0
        assert runtime.last_fetch_summary == "provider_paused:120"
        assert runtime.provider_paused_until == base_ts + 120
        mock_notification_service.send_status.assert_awaited()
        assert expected_resume_text in mock_notification_service.send_status.call_args[0][0]

    async def test_standard_flow_uses_compiled_query_and_latest_hint(
        self,
        use_case,
        mock_config,
        mock_search_provider,
    ):
        """Should compile structured lanes and pass Latest to twitterapi_io."""
        mock_config.search_queries = [
            SearchQuery(
                query="Find recent complaints about Grey from real users.",
                category_hint="competitor_complaint",
                description="Grey complaints",
                query_type="Latest",
                cooldown_seconds=60,
                lane_id="complaint-grey",
                intent_summary="Find recent complaints about Grey from real users.",
                brand_family="grey",
                brand_aliases=["Grey", "greyfinance", "grey.co"],
                brand_handles=["greyfinance", "greyfinanceEA"],
                issue_focus=["pending or stuck transfers", "verification or OTP problems"],
                geo_focus=["Nigeria", "Ghana", "Africa"],
            )
        ]
        mock_search_provider.search = AsyncMock(return_value={"tweets": []})

        await use_case.execute()

        mock_search_provider.search.assert_awaited_once()
        call = mock_search_provider.search.await_args
        assert call.kwargs["query_type"] == "Latest"
        assert "@greyfinance" in call.kwargs["query"]
        assert "to:greyfinance" in call.kwargs["query"]
        assert "since:" in call.kwargs["query"]
        assert "until:" in call.kwargs["query"]

    async def test_standard_flow_excludes_official_lane_authors(
        self,
        use_case,
        mock_config,
        mock_search_provider,
        mock_classifier,
        mock_notification_service,
        runtime,
    ):
        """Should drop raw-provider candidates authored by official brand handles."""
        mock_config.search_queries = [
            SearchQuery(
                query="Find recent complaints about Chipper from real users.",
                category_hint="competitor_complaint",
                description="Chipper complaints",
                query_type="Latest",
                cooldown_seconds=60,
                lane_id="complaint-chipper",
                intent_summary="Find recent complaints about Chipper from real users.",
                brand_family="chipper",
                brand_aliases=["Chipper", "Chipper Cash"],
                brand_handles=["chippercashapp"],
                exclude_author_handles=["chippercashapp"],
                issue_focus=["pending or stuck transfers"],
                geo_focus=["Nigeria", "Ghana", "Africa"],
            )
        ]
        mock_search_provider.search = AsyncMock(return_value={
            "tweets": [
                {
                    "id": "123",
                    "text": "We are investigating this transfer issue.",
                    "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "likeCount": 10,
                    "replyCount": 3,
                    "retweetCount": 2,
                    "viewCount": 500,
                    "author": {
                        "userName": "chippercashapp",
                        "name": "Chipper",
                        "followersCount": 1000,
                    },
                }
            ]
        })

        result = await use_case.execute()

        assert result.queued_count == 0
        assert runtime.locally_filtered_out >= 1
        mock_classifier.classify_and_generate.assert_not_awaited()
        mock_notification_service.send_approval.assert_not_awaited()

    async def test_standard_flow_enforces_restart_catchup_window(
        self,
        use_case,
        mock_config,
        mock_search_provider,
        mock_classifier,
        mock_notification_service,
        runtime,
    ):
        """Should drop standard-provider candidates outside the restart catch-up window."""
        mock_config.search_queries = [
            SearchQuery(
                query="Find recent complaints about Wise from real users.",
                category_hint="competitor_complaint",
                description="Wise complaints",
                query_type="Latest",
                cooldown_seconds=60,
                lane_id="complaint-wise",
                intent_summary="Find recent complaints about Wise from real users.",
                brand_family="wise",
                brand_aliases=["Wise"],
                brand_handles=["Wise"],
                issue_focus=["pending or stuck transfers"],
                geo_focus=["Nigeria", "Ghana", "Africa"],
            )
        ]
        runtime.restart_catchup_start_utc = datetime(2026, 3, 19, 8, 0, tzinfo=timezone.utc)
        runtime.restart_catchup_end_utc = datetime(2026, 3, 19, 9, 0, tzinfo=timezone.utc)
        mock_search_provider.search = AsyncMock(return_value={
            "tweets": [
                {
                    "id": "456",
                    "text": "Wise transfer is still pending.",
                    "createdAt": "2026-03-19T09:30:00Z",
                    "likeCount": 10,
                    "replyCount": 3,
                    "retweetCount": 2,
                    "viewCount": 500,
                    "author": {
                        "userName": "realuser",
                        "name": "Real User",
                        "followersCount": 1000,
                    },
                }
            ]
        })

        result = await use_case.execute()

        assert result.queued_count == 0
        assert runtime.locally_filtered_out >= 1
        mock_classifier.classify_and_generate.assert_not_awaited()
        mock_notification_service.send_approval.assert_not_awaited()


class TestScanResultDataclass:
    """Tests for ScanResult dataclass."""

    def test_scan_result_creation(self):
        """Should create ScanResult correctly."""
        result = ScanResult(
            queued_count=5,
            total_candidates=10,
            filtered_count=3,
            message="Test message",
        )

        assert result.queued_count == 5
        assert result.total_candidates == 10
        assert result.filtered_count == 3
        assert result.message == "Test message"
