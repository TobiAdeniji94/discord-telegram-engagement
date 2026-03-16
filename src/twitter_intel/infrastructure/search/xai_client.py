"""
xAI/Grok search provider implementation.

Provides tweet search capabilities using xAI's Grok model with x_search tool.
"""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from twitter_intel.exceptions import XaiAuthError, XaiRateLimitError


class XaiClient:
    """
    xAI API client for Grok model interactions.

    Uses xAI's responses API with the x_search tool for searching tweets.
    This client handles authentication, retries, and rate limiting.

    Note: This is not a direct SearchProvider implementation because
    xAI combines search and classification in a single API call.
    """

    def __init__(self, api_key: str, timeout_seconds: int = 30):
        """
        Initialize the xAI client.

        Args:
            api_key: xAI API key
            timeout_seconds: Request timeout in seconds
        """
        self._api_key = api_key
        self._base_url = "https://api.x.ai"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        """Get provider name."""
        return "xai_x_search"

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

    async def create_response(
        self,
        model: str,
        prompt: str,
        tool_config: dict[str, Any],
        max_turns: int,
    ) -> dict[str, Any]:
        """
        Create a response using xAI's API with tools.

        Args:
            model: Model identifier (e.g., "grok-4-1-fast-reasoning")
            prompt: User prompt
            tool_config: Tool configuration dict
            max_turns: Maximum number of turns for tool use

        Returns:
            Response from xAI API

        Raises:
            XaiAuthError: If authentication fails
            XaiRateLimitError: If rate limit is exceeded
        """
        timeout = httpx.Timeout(
            float(self._timeout_seconds),
            connect=min(10.0, float(self._timeout_seconds)),
        )
        last_error: Exception | None = None

        payload = {
            "model": model,
            "input": [{"role": "user", "content": prompt}],
            "tools": [tool_config],
            "include": ["no_inline_citations"],
            "max_turns": max(1, max_turns),
        }

        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    headers=self._headers,
                    timeout=timeout,
                ) as client:
                    resp = await client.post("/v1/responses", json=payload)

                if resp.status_code in (401, 403):
                    raise XaiAuthError(f"xAI auth failed ({resp.status_code})")

                if resp.status_code == 429:
                    raise XaiRateLimitError(
                        "xAI rate limited",
                        retry_after_seconds=self._parse_retry_after_seconds(
                            resp.headers.get("Retry-After")
                        ),
                    )

                # Retry on server errors
                if resp.status_code >= 500 and attempt < 1:
                    await asyncio.sleep(1 + attempt)
                    continue

                resp.raise_for_status()
                data = resp.json()

                if not isinstance(data, dict):
                    raise ValueError("Unexpected response shape from xAI")

                return data

            except (XaiAuthError, XaiRateLimitError, ValueError):
                raise
            except httpx.HTTPStatusError:
                raise
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= 1:
                    raise
                await asyncio.sleep(1 + attempt)

        raise last_error or RuntimeError("xAI request failed")


def build_x_search_tool_config(
    enable_image_understanding: bool = False,
    enable_video_understanding: bool = False,
    excluded_handles: list[str] | None = None,
    allowed_handles: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """
    Build the x_search tool configuration for xAI API.

    Args:
        enable_image_understanding: Whether to include image analysis
        enable_video_understanding: Whether to include video analysis
        excluded_handles: List of handles to exclude from results
        allowed_handles: List of handles to restrict results to
        start_date: Start date for search (ISO format)
        end_date: End date for search (ISO format)

    Returns:
        Tool configuration dict for xAI API
    """
    tool = {
        "type": "x_search",
        "x_search": {
            "enabled": True,
        },
    }

    if enable_image_understanding:
        tool["x_search"]["image_understanding"] = True
    if enable_video_understanding:
        tool["x_search"]["video_understanding"] = True

    if excluded_handles:
        tool["x_search"]["excluded_accounts"] = excluded_handles
    if allowed_handles:
        tool["x_search"]["accounts"] = allowed_handles

    if start_date:
        tool["x_search"]["start_date"] = start_date
    if end_date:
        tool["x_search"]["end_date"] = end_date

    return tool
