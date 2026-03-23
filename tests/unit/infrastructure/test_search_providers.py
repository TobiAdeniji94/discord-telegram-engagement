"""
Unit tests for search provider infrastructure.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from twitter_intel.domain.interfaces import SearchProvider
from twitter_intel.exceptions import ConfigurationError
from twitter_intel.infrastructure.search import (
    NullSearchProvider,
    SearchProviderFactory,
    TwitterApiIoClient,
    XaiClient,
    build_x_search_tool_config,
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

    @pytest.mark.asyncio
    async def test_prompt_caching_adds_deterministic_conversation_header(self):
        request_headers: list[dict[str, str]] = []

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                request_headers.append(dict(kwargs["headers"]))

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, path, json):
                request = httpx.Request("POST", "https://api.x.ai/v1/responses")
                return httpx.Response(200, json={"output_text": "ok"}, request=request)

        client = XaiClient("test_key", enable_prompt_caching=True)
        with patch("twitter_intel.infrastructure.search.xai_client.httpx.AsyncClient", FakeAsyncClient):
            await client.create_response(
                model="grok-4.20-reasoning",
                prompt="hello",
                tool_config={"type": "x_search"},
                max_turns=1,
                cache_key="lane-one",
            )
            await client.create_response(
                model="grok-4.20-reasoning",
                prompt="hello again",
                tool_config={"type": "x_search"},
                max_turns=1,
                cache_key="lane-one",
            )

        assert request_headers[0]["x-grok-conv-id"] == request_headers[1]["x-grok-conv-id"]

    @pytest.mark.asyncio
    async def test_falls_back_to_supported_alias_when_primary_model_invalid(self):
        requested_models: list[str] = []

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, path, json):
                requested_models.append(json["model"])
                request = httpx.Request("POST", "https://api.x.ai/v1/responses")
                if len(requested_models) == 1:
                    return httpx.Response(
                        400,
                        json={"error": {"message": "Invalid model requested"}},
                        request=request,
                    )
                return httpx.Response(200, json={"output_text": "ok"}, request=request)

        client = XaiClient(
            "test_key",
            primary_default_model="grok-4.20-0309-reasoning",
            fallback_model="grok-4.20-reasoning",
        )
        with patch("twitter_intel.infrastructure.search.xai_client.httpx.AsyncClient", FakeAsyncClient):
            result = await client.create_response(
                model="grok-4.20-0309-reasoning",
                prompt="hello",
                tool_config={"type": "x_search"},
                max_turns=1,
            )

        assert result == {"output_text": "ok"}
        assert requested_models == [
            "grok-4.20-0309-reasoning",
            "grok-4.20-reasoning",
        ]

    @pytest.mark.asyncio
    async def test_request_error_retries_and_invokes_attempt_callback(self):
        attempts = 0
        callback_count = 0

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, path, json):
                nonlocal attempts
                attempts += 1
                request = httpx.Request("POST", "https://api.x.ai/v1/responses")
                if attempts == 1:
                    raise httpx.ReadTimeout("timed out", request=request)
                return httpx.Response(200, json={"output_text": "ok"}, request=request)

        async def fake_sleep(delay):
            return None

        def on_request_attempt() -> None:
            nonlocal callback_count
            callback_count += 1

        client = XaiClient("test_key", max_retries=2, backoff_base_seconds=0.1)
        with (
            patch("twitter_intel.infrastructure.search.xai_client.httpx.AsyncClient", FakeAsyncClient),
            patch("twitter_intel.infrastructure.search.xai_client.asyncio.sleep", fake_sleep),
        ):
            result = await client.create_response(
                model="grok-4.20-reasoning",
                prompt="hello",
                tool_config={"type": "x_search"},
                max_turns=1,
                on_request_attempt=on_request_attempt,
            )

        assert result == {"output_text": "ok"}
        assert attempts == 2
        assert callback_count == 2

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

    def test_rejects_allowed_and_excluded_together(self):
        """Should reject invalid mixed handle filters."""
        with pytest.raises(ValueError):
            build_x_search_tool_config(
                excluded_handles=["user1"],
                allowed_handles=["user2"],
            )

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
