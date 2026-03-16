"""
Tests for scan and notify use case.
"""

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

    async def test_xai_flow_with_candidates(
        self, mock_config, mock_repository, mock_search_provider,
        mock_classifier, mock_notification_service, runtime
    ):
        """Should process xAI candidates."""
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
                "replies": [{"text": "Reply", "tone": "friendly", "strategy": "test"}],
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
        mock_notification_service.send_approval.assert_called_once()

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

        result = await use_case.execute()

        assert result.queued_count == 0
        assert runtime.last_fetch_summary == "provider_paused:120"
        assert runtime.provider_paused_until > 0
        mock_notification_service.send_status.assert_awaited()


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
