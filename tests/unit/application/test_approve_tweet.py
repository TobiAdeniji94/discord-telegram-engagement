"""
Tests for approve tweet use case.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from twitter_intel.application.use_cases.approve_tweet import (
    ApproveTweetUseCase,
    ApprovalResult,
)


class TestApproveTweetUseCase:
    """Tests for ApproveTweetUseCase class."""

    @pytest.fixture
    def mock_repository(self):
        """Create mock repository."""
        repo = MagicMock()
        repo.get_pending = MagicMock(return_value=(
            ["Reply 1", "Reply 2", "Reply 3"],
            "msg123",
            "ch456",
            "brand-mentions"
        ))
        repo.mark_replied = MagicMock()
        repo.get_tweet_info = MagicMock(return_value={
            "url": "https://x.com/user/status/123",
            "author": "test_user",
        })
        return repo

    @pytest.fixture
    def mock_x_poster(self):
        """Create mock X poster."""
        poster = MagicMock()
        poster.post_reply = AsyncMock(return_value=True)
        return poster

    @pytest.fixture
    def mock_notification_service(self):
        """Create mock notification service."""
        service = MagicMock()
        service.log_approved = AsyncMock()
        return service

    @pytest.fixture
    def use_case(self, mock_repository, mock_x_poster, mock_notification_service):
        """Create use case with mocks."""
        return ApproveTweetUseCase(
            repository=mock_repository,
            x_poster=mock_x_poster,
            notification_service=mock_notification_service,
        )

    async def test_successful_approval(
        self, use_case, mock_repository, mock_x_poster, mock_notification_service
    ):
        """Should approve tweet successfully."""
        result = await use_case.execute("123456", 0)

        assert result.success is True
        assert result.reply_text == "Reply 1"
        assert "successfully" in result.message
        mock_x_poster.post_reply.assert_called_once_with("123456", "Reply 1")
        mock_repository.mark_replied.assert_called_once_with("123456", "Reply 1")
        mock_notification_service.log_approved.assert_called_once()

    async def test_second_reply_option(self, use_case, mock_x_poster):
        """Should use correct reply index."""
        result = await use_case.execute("123456", 1)

        assert result.success is True
        assert result.reply_text == "Reply 2"
        mock_x_poster.post_reply.assert_called_once_with("123456", "Reply 2")

    async def test_no_pending_approval(self, use_case, mock_repository):
        """Should fail when no pending approval found."""
        mock_repository.get_pending = MagicMock(return_value=None)

        result = await use_case.execute("123456", 0)

        assert result.success is False
        assert "No pending" in result.message

    async def test_invalid_reply_index(self, use_case, mock_repository):
        """Should fail for invalid reply index."""
        mock_repository.get_pending = MagicMock(return_value=(
            ["Reply 1"],
            "msg123",
            "ch456",
            "brand-mentions"
        ))

        result = await use_case.execute("123456", 5)

        assert result.success is False
        assert "Invalid reply index" in result.message

    async def test_x_posting_failure(self, use_case, mock_x_poster, mock_repository):
        """Should handle X posting failure."""
        mock_x_poster.post_reply = AsyncMock(return_value=False)

        result = await use_case.execute("123456", 0)

        assert result.success is False
        assert "Failed to post" in result.message
        mock_repository.mark_replied.assert_not_called()


class TestApproveTweetCustomReply:
    """Tests for custom reply functionality."""

    @pytest.fixture
    def mock_repository(self):
        """Create mock repository."""
        repo = MagicMock()
        repo.get_pending = MagicMock(return_value=(
            ["Reply 1", "Reply 2"],
            "msg123",
            "ch456",
            "brand-mentions"
        ))
        repo.mark_replied = MagicMock()
        repo.get_tweet_info = MagicMock(return_value={
            "url": "https://x.com/user/status/123",
            "author": "test_user",
        })
        return repo

    @pytest.fixture
    def mock_x_poster(self):
        """Create mock X poster."""
        poster = MagicMock()
        poster.post_reply = AsyncMock(return_value=True)
        return poster

    @pytest.fixture
    def mock_notification_service(self):
        """Create mock notification service."""
        service = MagicMock()
        service.log_approved = AsyncMock()
        return service

    @pytest.fixture
    def use_case(self, mock_repository, mock_x_poster, mock_notification_service):
        """Create use case with mocks."""
        return ApproveTweetUseCase(
            repository=mock_repository,
            x_poster=mock_x_poster,
            notification_service=mock_notification_service,
        )

    async def test_successful_custom_reply(self, use_case, mock_x_poster):
        """Should post custom reply successfully."""
        result = await use_case.execute_custom_reply(
            "123456",
            "This is a custom reply"
        )

        assert result.success is True
        assert result.reply_text == "This is a custom reply"
        mock_x_poster.post_reply.assert_called_once_with(
            "123456",
            "This is a custom reply"
        )

    async def test_custom_reply_rejects_non_pending(self, use_case, mock_repository, mock_x_poster):
        """Should reject custom reply when tweet is not pending approval."""
        mock_repository.get_pending = MagicMock(return_value=(None, None, None, None))

        result = await use_case.execute_custom_reply("123456", "This is a custom reply")

        assert result.success is False
        assert "No pending approval" in result.message
        mock_x_poster.post_reply.assert_not_called()
        mock_repository.mark_replied.assert_not_called()

    async def test_custom_reply_too_long(self, use_case):
        """Should reject custom replies over 280 chars."""
        long_reply = "A" * 300

        result = await use_case.execute_custom_reply("123456", long_reply)

        assert result.success is False
        assert "300 chars" in result.message

    async def test_custom_reply_failure(self, use_case, mock_x_poster, mock_repository):
        """Should handle custom reply posting failure."""
        mock_x_poster.post_reply = AsyncMock(return_value=False)

        result = await use_case.execute_custom_reply("123456", "Test reply")

        assert result.success is False
        assert "Failed to post" in result.message
        mock_repository.mark_replied.assert_not_called()
