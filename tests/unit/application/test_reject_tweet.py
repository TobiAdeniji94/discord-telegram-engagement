"""
Tests for reject tweet use case.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from twitter_intel.application.use_cases.reject_tweet import (
    RejectTweetUseCase,
    RejectionResult,
)


class TestRejectTweetUseCase:
    """Tests for RejectTweetUseCase class."""

    @pytest.fixture
    def mock_repository(self):
        """Create mock repository."""
        repo = MagicMock()
        repo.mark_rejected = MagicMock()
        repo.get_tweet_info = MagicMock(return_value={
            "url": "https://x.com/user/status/123",
            "author": "test_user",
        })
        return repo

    @pytest.fixture
    def mock_notification_service(self):
        """Create mock notification service."""
        service = MagicMock()
        service.log_rejected = AsyncMock()
        return service

    @pytest.fixture
    def use_case(self, mock_repository, mock_notification_service):
        """Create use case with mocks."""
        return RejectTweetUseCase(
            repository=mock_repository,
            notification_service=mock_notification_service,
        )

    async def test_successful_rejection(
        self, use_case, mock_repository, mock_notification_service
    ):
        """Should reject tweet successfully."""
        result = await use_case.execute("123456")

        assert result.success is True
        assert "Skipped" in result.message
        mock_repository.mark_rejected.assert_called_once_with("123456")
        mock_notification_service.log_rejected.assert_called_once()

    async def test_logs_rejection_with_tweet_info(
        self, use_case, mock_notification_service
    ):
        """Should log rejection with tweet info."""
        await use_case.execute("123456")

        mock_notification_service.log_rejected.assert_called_once_with(
            tweet_id="123456",
            tweet_url="https://x.com/user/status/123",
            author="test_user",
        )

    async def test_handles_missing_tweet_info(
        self, use_case, mock_repository, mock_notification_service
    ):
        """Should handle missing tweet info gracefully."""
        mock_repository.get_tweet_info = MagicMock(return_value=None)

        result = await use_case.execute("123456")

        assert result.success is True
        mock_repository.mark_rejected.assert_called_once()
        mock_notification_service.log_rejected.assert_not_called()
