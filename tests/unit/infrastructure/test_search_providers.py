"""
Unit tests for search provider infrastructure.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from twitter_intel.infrastructure.search import (
    TwitterApiIoClient,
    XaiClient,
    SearchProviderFactory,
    NullSearchProvider,
    build_x_search_tool_config,
)
from twitter_intel.domain.interfaces import SearchProvider
from twitter_intel.exceptions import (
    TwitterApiIoAuthError,
    TwitterApiIoRateLimitError,
    XaiAuthError,
    XaiRateLimitError,
    ConfigurationError,
)


class TestTwitterApiIoClient:
    """Tests for TwitterApiIoClient."""

    def test_implements_search_provider(self):
        """Client should implement SearchProvider interface."""
        client = TwitterApiIoClient("test_key")
        assert isinstance(client, SearchProvider)

    def test_name_property(self):
        """Client should have correct name."""
        client = TwitterApiIoClient("test_key")
        assert client.name == "twitterapi_io"

    def test_parse_retry_after_seconds_integer(self):
        """Should parse integer retry-after value."""
        result = TwitterApiIoClient._parse_retry_after_seconds("300")
        assert result == 300

    def test_parse_retry_after_seconds_float(self):
        """Should parse float retry-after value."""
        result = TwitterApiIoClient._parse_retry_after_seconds("60.5")
        assert result == 60

    def test_parse_retry_after_seconds_none(self):
        """Should return None for None input."""
        result = TwitterApiIoClient._parse_retry_after_seconds(None)
        assert result is None

    def test_parse_retry_after_seconds_empty(self):
        """Should return None for empty string."""
        result = TwitterApiIoClient._parse_retry_after_seconds("")
        assert result is None

    def test_parse_retry_after_seconds_minimum_one(self):
        """Should return minimum of 1 second."""
        result = TwitterApiIoClient._parse_retry_after_seconds("0")
        assert result == 1

    @pytest.mark.asyncio
    async def test_advanced_search_alias(self):
        """advanced_search should be alias for search."""
        client = TwitterApiIoClient("test_key")
        with patch.object(client, 'search', new_callable=AsyncMock) as mock_search:
            mock_search.return_value = {"tweets": []}
            await client.advanced_search("test query", "Latest")
            mock_search.assert_called_once_with("test query", "Latest")

    @pytest.mark.asyncio
    async def test_user_mentions_alias(self):
        """user_mentions should be alias for get_user_mentions."""
        client = TwitterApiIoClient("test_key")
        with patch.object(client, 'get_user_mentions', new_callable=AsyncMock) as mock:
            mock.return_value = {"tweets": []}
            await client.user_mentions("testuser")
            mock.assert_called_once_with("testuser")


class TestXaiClient:
    """Tests for XaiClient."""

    def test_name_property(self):
        """Client should have correct name."""
        client = XaiClient("test_key")
        assert client.name == "xai_x_search"

    def test_default_timeout(self):
        """Client should use default timeout of 30 seconds."""
        client = XaiClient("test_key")
        assert client._timeout_seconds == 30

    def test_custom_timeout(self):
        """Client should accept custom timeout."""
        client = XaiClient("test_key", timeout_seconds=60)
        assert client._timeout_seconds == 60

    def test_parse_retry_after_seconds_integer(self):
        """Should parse integer retry-after value."""
        result = XaiClient._parse_retry_after_seconds("120")
        assert result == 120

    def test_parse_retry_after_seconds_none(self):
        """Should return None for None input."""
        result = XaiClient._parse_retry_after_seconds(None)
        assert result is None


class TestBuildXSearchToolConfig:
    """Tests for build_x_search_tool_config function."""

    def test_basic_config(self):
        """Should build basic x_search config."""
        config = build_x_search_tool_config()
        assert config["type"] == "x_search"
        assert "from_date" not in config

    def test_image_understanding(self):
        """Should include image understanding when enabled."""
        config = build_x_search_tool_config(enable_image_understanding=True)
        assert config["enable_image_understanding"] is True

    def test_video_understanding(self):
        """Should include video understanding when enabled."""
        config = build_x_search_tool_config(enable_video_understanding=True)
        assert config["enable_video_understanding"] is True

    def test_excluded_handles(self):
        """Should include excluded handles."""
        config = build_x_search_tool_config(excluded_handles=["user1", "user2"])
        assert config["excluded_x_handles"] == ["user1", "user2"]

    def test_allowed_handles(self):
        """Should include allowed handles."""
        config = build_x_search_tool_config(allowed_handles=["user1"])
        assert config["allowed_x_handles"] == ["user1"]

    def test_date_range(self):
        """Should include date range."""
        config = build_x_search_tool_config(
            start_date="2024-01-01",
            end_date="2024-01-31",
        )
        assert config["from_date"] == "2024-01-01"
        assert config["to_date"] == "2024-01-31"


class TestNullSearchProvider:
    """Tests for NullSearchProvider."""

    def test_implements_search_provider(self):
        """NullSearchProvider should implement SearchProvider interface."""
        provider = NullSearchProvider()
        assert isinstance(provider, SearchProvider)

    def test_name_property(self):
        """Provider should have correct name."""
        provider = NullSearchProvider()
        assert provider.name == "manual_only"

    @pytest.mark.asyncio
    async def test_search_returns_empty(self):
        """search should return empty results."""
        provider = NullSearchProvider()
        result = await provider.search("any query")
        assert result == {"tweets": []}

    @pytest.mark.asyncio
    async def test_get_user_mentions_returns_empty(self):
        """get_user_mentions should return empty results."""
        provider = NullSearchProvider()
        result = await provider.get_user_mentions("anyuser")
        assert result == {"tweets": []}


class TestSearchProviderFactory:
    """Tests for SearchProviderFactory."""

    def test_get_supported_providers(self):
        """Should return list of supported providers."""
        providers = SearchProviderFactory.get_supported_providers()
        assert "twitterapi_io" in providers
        assert "xai_x_search" in providers
        assert "twscrape" in providers
        assert "manual_only" in providers

    def test_create_twitterapi_io(self):
        """Should create TwitterApiIoClient for twitterapi_io."""
        config = MagicMock()
        config.search_provider = "twitterapi_io"
        config.twitterapi_io_api_key = "test_key"

        provider = SearchProviderFactory.create(config)

        assert isinstance(provider, TwitterApiIoClient)

    def test_create_twitterapi_io_requires_key(self):
        """Should raise error if API key is missing for twitterapi_io."""
        config = MagicMock()
        config.search_provider = "twitterapi_io"
        config.twitterapi_io_api_key = ""

        with pytest.raises(ConfigurationError) as exc_info:
            SearchProviderFactory.create(config)

        assert "TWITTERAPI_IO_API_KEY" in str(exc_info.value)

    def test_create_manual_only(self):
        """Should create NullSearchProvider for manual_only."""
        config = MagicMock()
        config.search_provider = "manual_only"

        provider = SearchProviderFactory.create(config)

        assert isinstance(provider, NullSearchProvider)

    def test_create_unknown_provider(self):
        """Should raise error for unknown provider."""
        config = MagicMock()
        config.search_provider = "unknown_provider"

        with pytest.raises(ConfigurationError) as exc_info:
            SearchProviderFactory.create(config)

        assert "Unknown search provider" in str(exc_info.value)

    def test_create_handles_whitespace(self):
        """Should handle whitespace in provider name."""
        config = MagicMock()
        config.search_provider = "  manual_only  "

        provider = SearchProviderFactory.create(config)

        assert isinstance(provider, NullSearchProvider)

    def test_create_handles_case(self):
        """Should handle case in provider name."""
        config = MagicMock()
        config.search_provider = "MANUAL_ONLY"

        provider = SearchProviderFactory.create(config)

        assert isinstance(provider, NullSearchProvider)
