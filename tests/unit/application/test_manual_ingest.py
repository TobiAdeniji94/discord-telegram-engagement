"""
Tests for manual ingest use case.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from twitter_intel.application.use_cases.manual_ingest import (
    ManualIngestUseCase,
    build_manual_candidate,
    build_manual_ingest_analysis,
)
from twitter_intel.domain.entities.category import TweetCategory


class TestBuildManualCandidate:
    """Tests for build_manual_candidate function."""

    def test_creates_manual_tweet_id(self):
        """Should create tweet ID with manual- prefix."""
        tweet = build_manual_candidate(
            TweetCategory.BRAND_MENTION,
            "Test tweet text"
        )

        assert tweet.tweet_id.startswith("manual-")

    def test_stores_text(self):
        """Should store the provided text."""
        tweet = build_manual_candidate(
            TweetCategory.BRAND_MENTION,
            "  My test tweet  "
        )

        assert tweet.text == "My test tweet"

    def test_sets_manual_author(self):
        """Should set manual ingest author."""
        tweet = build_manual_candidate(
            TweetCategory.BRAND_MENTION,
            "Test"
        )

        assert tweet.author_username == "manual_ingest"
        assert tweet.author_name == "Manual Ingest"
        assert tweet.author_followers == 0

    def test_sets_search_query_with_category(self):
        """Should set search query with category."""
        tweet = build_manual_candidate(
            TweetCategory.COMPETITOR_COMPLAINT,
            "Test"
        )

        assert tweet.search_query == "manual-ingest:competitor-complaints"

    def test_sets_category_hint(self):
        """Should set correct category hint."""
        tweet = build_manual_candidate(
            TweetCategory.SOLUTION_SEEKER,
            "Test"
        )

        assert tweet.category_hint == "solution_seeker"


class TestBuildManualIngestAnalysis:
    """Tests for build_manual_ingest_analysis function."""

    def test_brand_mention_analysis(self):
        """Should build correct brand mention analysis."""
        analysis = build_manual_ingest_analysis(
            TweetCategory.BRAND_MENTION,
            "Test text"
        )

        assert analysis["category"] == "brand-mentions"
        assert analysis["sentiment"] == "neutral"
        assert analysis["confidence"] == 1.0
        assert "manual-ingest" in analysis["themes"]
        assert len(analysis["replies"]) == 2

    def test_competitor_complaint_analysis(self):
        """Should build correct competitor complaint analysis."""
        analysis = build_manual_ingest_analysis(
            TweetCategory.COMPETITOR_COMPLAINT,
            "High fees are terrible"
        )

        assert analysis["category"] == "competitor-complaints"
        assert analysis["sentiment"] == "negative"
        assert analysis["urgency"] == "medium"
        assert analysis["competitor_mentioned"] == "manual-test"

    def test_solution_seeker_analysis(self):
        """Should build correct solution seeker analysis."""
        analysis = build_manual_ingest_analysis(
            TweetCategory.SOLUTION_SEEKER,
            "Need dollar card"
        )

        assert analysis["category"] == "solution-seekers"
        assert analysis["urgency"] == "high"

    def test_truncates_long_snippet(self):
        """Should truncate long text in replies."""
        long_text = "A" * 200
        analysis = build_manual_ingest_analysis(
            TweetCategory.BRAND_MENTION,
            long_text
        )

        # Check that snippet in replies is truncated
        reply_text = analysis["replies"][0]["text"]
        assert "..." in reply_text


class TestManualIngestUseCase:
    """Tests for ManualIngestUseCase class."""

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
        return ManualIngestUseCase(
            repository=mock_repository,
            notification_service=mock_notification_service,
        )

    async def test_successful_ingest(self, use_case, mock_repository, mock_notification_service):
        """Should queue manual ingest successfully."""
        success, message = await use_case.execute(
            TweetCategory.BRAND_MENTION,
            "Test tweet about Yara.cash"
        )

        assert success is True
        assert "manual-" in message
        assert "brand-mentions" in message
        mock_repository.mark_processed.assert_called_once()
        mock_notification_service.send_approval.assert_called_once()
        mock_repository.save_pending.assert_called_once()

    async def test_failed_notification(self, use_case, mock_repository, mock_notification_service):
        """Should handle notification failure."""
        mock_notification_service.send_approval = AsyncMock(return_value=None)

        success, message = await use_case.execute(
            TweetCategory.BRAND_MENTION,
            "Test text"
        )

        assert success is False
        assert "Could not queue" in message
        mock_repository.mark_rejected.assert_called_once()

    async def test_different_categories(self, use_case):
        """Should handle different categories."""
        for category in [
            TweetCategory.BRAND_MENTION,
            TweetCategory.COMPETITOR_COMPLAINT,
            TweetCategory.SOLUTION_SEEKER,
        ]:
            success, message = await use_case.execute(category, "Test")
            assert success is True
            assert category.value in message
