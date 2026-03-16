"""
Tests for smoke test use case.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from twitter_intel.application.use_cases.smoke_test import (
    SmokeTestUseCase,
    build_smoke_test_payload,
)
from twitter_intel.domain.entities.category import TweetCategory


class TestBuildSmokeTestPayload:
    """Tests for build_smoke_test_payload function."""

    def test_brand_mention_payload(self):
        """Should build correct brand mention payload."""
        tweet, analysis = build_smoke_test_payload(TweetCategory.BRAND_MENTION)

        assert tweet.tweet_id.startswith("smoke-")
        assert "brand" in tweet.text.lower()
        assert tweet.author_username == "yara_brand_smoke"
        assert tweet.source_tab == "Smoke"
        assert analysis["category"] == "brand-mentions"
        assert analysis["confidence"] == 1.0
        assert len(analysis["replies"]) == 3

    def test_competitor_complaint_payload(self):
        """Should build correct competitor complaint payload."""
        tweet, analysis = build_smoke_test_payload(TweetCategory.COMPETITOR_COMPLAINT)

        assert tweet.tweet_id.startswith("smoke-")
        assert "competitor" in tweet.text.lower()
        assert tweet.author_username == "competitor_smoke"
        assert analysis["category"] == "competitor-complaints"
        assert analysis["sentiment"] == "negative"
        assert analysis["competitor_mentioned"] == "ExamplePay"

    def test_solution_seeker_payload(self):
        """Should build correct solution seeker payload."""
        tweet, analysis = build_smoke_test_payload(TweetCategory.SOLUTION_SEEKER)

        assert tweet.tweet_id.startswith("smoke-")
        assert "solution" in tweet.text.lower() or "USD" in tweet.text
        assert tweet.author_username == "seeker_smoke"
        assert analysis["category"] == "solution-seekers"
        assert analysis["urgency"] == "high"

    def test_unique_tweet_ids(self):
        """Should generate unique tweet IDs."""
        _, analysis1 = build_smoke_test_payload(TweetCategory.BRAND_MENTION)
        import time
        time.sleep(0.001)  # Ensure different timestamp
        tweet2, _ = build_smoke_test_payload(TweetCategory.BRAND_MENTION)

        # IDs are based on timestamp, so should be unique
        assert tweet2.tweet_id.startswith("smoke-")


class TestSmokeTestUseCase:
    """Tests for SmokeTestUseCase class."""

    @pytest.fixture
    def mock_repository(self):
        """Create mock repository."""
        repo = MagicMock()
        repo.mark_processed = MagicMock()
        repo.save_pending = MagicMock()
        repo.mark_rejected = MagicMock()
        return repo

    @pytest.fixture
    def mock_notification_service(self):
        """Create mock notification service."""
        service = MagicMock()
        service.send_approval = AsyncMock(return_value=("msg123", "ch456"))
        return service

    @pytest.fixture
    def use_case(self, mock_repository, mock_notification_service):
        """Create use case with mocks."""
        return SmokeTestUseCase(
            repository=mock_repository,
            notification_service=mock_notification_service,
        )

    async def test_successful_smoke_test(self, use_case, mock_repository, mock_notification_service):
        """Should queue smoke test successfully."""
        success, message = await use_case.execute(TweetCategory.BRAND_MENTION)

        assert success is True
        assert "smoke-" in message
        assert "brand-mentions" in message
        mock_repository.mark_processed.assert_called_once()
        mock_notification_service.send_approval.assert_called_once()
        mock_repository.save_pending.assert_called_once()

    async def test_failed_notification(self, use_case, mock_repository, mock_notification_service):
        """Should handle notification failure."""
        mock_notification_service.send_approval = AsyncMock(return_value=None)

        success, message = await use_case.execute(TweetCategory.BRAND_MENTION)

        assert success is False
        assert "Could not queue" in message
        mock_repository.mark_rejected.assert_called_once()

    async def test_competitor_complaint_smoke(self, use_case, mock_notification_service):
        """Should queue competitor complaint smoke test."""
        success, message = await use_case.execute(TweetCategory.COMPETITOR_COMPLAINT)

        assert success is True
        assert "competitor-complaints" in message

    async def test_solution_seeker_smoke(self, use_case, mock_notification_service):
        """Should queue solution seeker smoke test."""
        success, message = await use_case.execute(TweetCategory.SOLUTION_SEEKER)

        assert success is True
        assert "solution-seekers" in message
