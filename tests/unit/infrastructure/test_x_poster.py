"""
Tests for X/Twitter poster.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from twitter_intel.infrastructure.twitter.x_poster import (
    XPoster,
    is_local_test_tweet_id,
    X_CREATE_TWEET_URL,
    X_CREATE_TWEET_QUERY_ID,
    X_TWEET_FEATURES,
)


class TestIsLocalTestTweetId:
    """Tests for is_local_test_tweet_id function."""

    def test_smoke_tweet_id(self):
        """Should identify smoke test tweet IDs."""
        assert is_local_test_tweet_id("smoke-123456") is True
        assert is_local_test_tweet_id("smoke-brand-test") is True

    def test_manual_tweet_id(self):
        """Should identify manual ingest tweet IDs."""
        assert is_local_test_tweet_id("manual-123456") is True
        assert is_local_test_tweet_id("manual-competitor") is True

    def test_real_tweet_id(self):
        """Should not identify real tweet IDs."""
        assert is_local_test_tweet_id("1234567890") is False
        assert is_local_test_tweet_id("1876543210987654321") is False

    def test_empty_string(self):
        """Should handle empty string."""
        assert is_local_test_tweet_id("") is False

    def test_similar_but_not_prefix(self):
        """Should not match if smoke/manual is not prefix."""
        assert is_local_test_tweet_id("test-smoke-123") is False
        assert is_local_test_tweet_id("my-manual-test") is False


class TestXPoster:
    """Tests for XPoster class."""

    def test_init(self):
        """Should initialize with credentials."""
        poster = XPoster(
            csrf_token="test_csrf",
            cookie="test_cookie",
            dry_run=False,
        )
        assert poster._csrf_token == "test_csrf"
        assert poster._cookie == "test_cookie"
        assert poster._dry_run is False

    def test_init_dry_run(self):
        """Should initialize with dry run enabled."""
        poster = XPoster(
            csrf_token="test_csrf",
            cookie="test_cookie",
            dry_run=True,
        )
        assert poster._dry_run is True

    def test_is_configured_with_credentials(self):
        """Should be configured when both credentials present."""
        poster = XPoster(csrf_token="token", cookie="cookie")
        assert poster.is_configured is True

    def test_is_configured_missing_csrf(self):
        """Should not be configured without CSRF token."""
        poster = XPoster(csrf_token="", cookie="cookie")
        assert poster.is_configured is False

    def test_is_configured_missing_cookie(self):
        """Should not be configured without cookie."""
        poster = XPoster(csrf_token="token", cookie="")
        assert poster.is_configured is False

    def test_is_configured_both_missing(self):
        """Should not be configured without any credentials."""
        poster = XPoster(csrf_token="", cookie="")
        assert poster.is_configured is False

    def test_build_headers(self):
        """Should build correct headers."""
        poster = XPoster(csrf_token="my_csrf", cookie="my_cookie")
        headers = poster._build_headers()

        assert "Bearer" in headers["authorization"]
        assert headers["x-csrf-token"] == "my_csrf"
        assert headers["cookie"] == "my_cookie"
        assert headers["content-type"] == "application/json"
        assert headers["x-twitter-active-user"] == "yes"
        assert headers["x-twitter-auth-type"] == "OAuth2Session"

    def test_build_payload(self):
        """Should build correct GraphQL payload."""
        poster = XPoster(csrf_token="token", cookie="cookie")
        payload = poster._build_payload("123456", "Test reply")

        assert payload["variables"]["tweet_text"] == "Test reply"
        assert payload["variables"]["reply"]["in_reply_to_tweet_id"] == "123456"
        assert payload["queryId"] == X_CREATE_TWEET_QUERY_ID
        assert payload["features"] == X_TWEET_FEATURES


class TestXPosterPostReply:
    """Tests for XPoster.post_reply method."""

    @pytest.fixture
    def poster(self):
        """Create a poster with credentials."""
        return XPoster(csrf_token="token", cookie="cookie", dry_run=False)

    @pytest.fixture
    def dry_run_poster(self):
        """Create a poster in dry-run mode."""
        return XPoster(csrf_token="token", cookie="cookie", dry_run=True)

    async def test_dry_run_mode(self, dry_run_poster):
        """Should return True without posting in dry-run mode."""
        result = await dry_run_poster.post_reply("123456", "Test reply")
        assert result is True

    async def test_smoke_tweet_id(self, poster):
        """Should not actually post for smoke test tweet IDs."""
        result = await poster.post_reply("smoke-123", "Test reply")
        assert result is True

    async def test_manual_tweet_id(self, poster):
        """Should not actually post for manual ingest tweet IDs."""
        result = await poster.post_reply("manual-456", "Test reply")
        assert result is True

    async def test_unconfigured_poster(self):
        """Should fail if not configured."""
        poster = XPoster(csrf_token="", cookie="")
        result = await poster.post_reply("123456", "Test reply")
        assert result is False

    async def test_successful_post(self, poster):
        """Should return True on successful post."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"create_tweet": {"tweet_results": {}}}}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await poster.post_reply("123456789", "Test reply")
            assert result is True

    async def test_api_errors_in_response(self, poster):
        """Should return False when API returns errors."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"errors": [{"message": "Rate limited"}]}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await poster.post_reply("123456789", "Test reply")
            assert result is False

    async def test_http_error(self, poster):
        """Should return False on HTTP error."""
        mock_response = MagicMock()
        mock_response.status_code = 403

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await poster.post_reply("123456789", "Test reply")
            assert result is False

    async def test_request_exception(self, poster):
        """Should return False on request exception."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.RequestError("Connection failed")
            )

            result = await poster.post_reply("123456789", "Test reply")
            assert result is False
