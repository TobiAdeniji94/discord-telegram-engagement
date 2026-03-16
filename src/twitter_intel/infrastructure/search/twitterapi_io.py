"""
TwitterAPI.io search provider implementation.

Provides tweet search capabilities using the TwitterAPI.io service.
"""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from twitter_intel.domain.interfaces.search_provider import SearchProvider
from twitter_intel.exceptions import (
    TwitterApiIoAuthError,
    TwitterApiIoRateLimitError,
)


class TwitterApiIoClient(SearchProvider):
    """
    TwitterAPI.io search provider implementation.

    Uses the TwitterAPI.io service for searching tweets. Includes
    automatic retry logic for transient failures and proper error
    handling for authentication and rate limit errors.
    """

    def __init__(self, api_key: str):
        """
        Initialize the TwitterAPI.io client.

        Args:
            api_key: TwitterAPI.io API key
        """
        self._api_key = api_key
        self._base_url = "https://api.twitterapi.io"
        self._headers = {"X-API-Key": api_key}

    @property
    def name(self) -> str:
        """Get provider name."""
        return "twitterapi_io"

    async def _request_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Make an authenticated JSON request to the API.

        Args:
            path: API endpoint path
            params: Query parameters

        Returns:
            JSON response as dict

        Raises:
            TwitterApiIoAuthError: If authentication fails
            TwitterApiIoRateLimitError: If rate limit is exceeded
        """
        timeout = httpx.Timeout(20.0, connect=10.0)
        last_error: Exception | None = None

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    headers=self._headers,
                    timeout=timeout,
                ) as client:
                    resp = await client.get(path, params=params)

                if resp.status_code in (401, 403):
                    raise TwitterApiIoAuthError(
                        f"twitterapi.io auth failed ({resp.status_code})"
                    )

                if resp.status_code == 429:
                    raise TwitterApiIoRateLimitError(
                        "twitterapi.io rate limited",
                        retry_after_seconds=self._parse_retry_after_seconds(
                            resp.headers.get("Retry-After")
                        ),
                    )

                # Retry on server errors
                if resp.status_code >= 500 and attempt < 2:
                    await asyncio.sleep(1 + attempt)
                    continue

                resp.raise_for_status()
                data = resp.json()

                if not isinstance(data, dict):
                    raise ValueError("Unexpected response shape from twitterapi.io")

                return data

            except (TwitterApiIoAuthError, TwitterApiIoRateLimitError, ValueError):
                raise
            except httpx.HTTPStatusError:
                raise
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= 2:
                    raise
                await asyncio.sleep(1 + attempt)

        raise last_error or RuntimeError("twitterapi.io request failed")

    @staticmethod
    def _parse_retry_after_seconds(raw_value: str | None) -> int | None:
        """
        Parse Retry-After header value.

        Args:
            raw_value: Raw header value (seconds or HTTP date)

        Returns:
            Number of seconds to wait, or None if parsing fails
        """
        if not raw_value:
            return None

        # Try parsing as integer seconds
        try:
            return max(1, int(float(raw_value)))
        except ValueError:
            pass

        # Try parsing as HTTP date
        try:
            parsed = parsedate_to_datetime(raw_value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            delay = int(
                (parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
            )
            return max(1, delay)
        except Exception:
            return None

    async def search(
        self,
        query: str,
        query_type: str = "Top",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Execute an advanced search query.

        Args:
            query: The search query string
            query_type: "Top" for popular tweets, "Latest" for recent
            **kwargs: Additional parameters (ignored)

        Returns:
            Search results from TwitterAPI.io
        """
        return await self._request_json(
            "/twitter/tweet/advanced_search",
            {
                "query": query,
                "queryType": query_type,
            },
        )

    async def get_user_mentions(self, username: str) -> dict[str, Any]:
        """
        Get mentions for a specific user.

        Args:
            username: The username to get mentions for (without @)

        Returns:
            Mention results from TwitterAPI.io
        """
        return await self._request_json(
            "/twitter/user/mentions",
            {"userName": username.lstrip("@")},
        )

    # Alias for backward compatibility
    async def advanced_search(
        self, query: str, query_type: str = "Top"
    ) -> dict[str, Any]:
        """Alias for search() for backward compatibility."""
        return await self.search(query, query_type)

    async def user_mentions(self, user_name: str) -> dict[str, Any]:
        """Alias for get_user_mentions() for backward compatibility."""
        return await self.get_user_mentions(user_name)
