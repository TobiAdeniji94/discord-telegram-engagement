"""
xAI/Grok search provider implementation.

Provides tweet search capabilities using xAI's Grok model with x_search tool.
"""

import asyncio
import random
import uuid
from collections.abc import Callable
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

    def __init__(
        self,
        api_key: str,
        timeout_seconds: int = 30,
        enable_prompt_caching: bool = True,
        prompt_cache_namespace: str = "discord-telegram-engagement",
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        primary_default_model: str | None = None,
        fallback_model: str | None = None,
    ):
        """
        Initialize the xAI client.

        Args:
            api_key: xAI API key
            timeout_seconds: Request timeout in seconds
            enable_prompt_caching: Whether to send deterministic prompt-cache headers
            prompt_cache_namespace: Stable namespace used to derive cache conversation ids
            max_retries: Maximum retries for transient request or 5xx failures
            backoff_base_seconds: Base delay used for exponential retry backoff
            primary_default_model: Preferred default model identifier
            fallback_model: Supported alias to fall back to if the default model is unavailable
        """
        self._api_key = api_key
        self._base_url = "https://api.x.ai"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._timeout_seconds = timeout_seconds
        self._enable_prompt_caching = enable_prompt_caching
        self._prompt_cache_namespace = prompt_cache_namespace or "discord-telegram-engagement"
        self._max_retries = max(0, int(max_retries))
        self._backoff_base_seconds = max(0.1, float(backoff_base_seconds))
        self._primary_default_model = primary_default_model or ""
        self._fallback_model = fallback_model or ""

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

    def _cache_conversation_id(self, cache_key: str, model: str) -> str:
        raw_value = f"{self._prompt_cache_namespace}:{model}:{cache_key}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, raw_value))

    def _request_headers(
        self,
        model: str,
        cache_key: str | None = None,
    ) -> dict[str, str]:
        headers = dict(self._headers)
        if self._enable_prompt_caching and cache_key:
            headers["x-grok-conv-id"] = self._cache_conversation_id(cache_key, model)
        return headers

    def _retry_delay_seconds(self, attempt_index: int) -> float:
        base_delay = self._backoff_base_seconds * (2 ** max(0, attempt_index))
        jitter = random.uniform(0.0, self._backoff_base_seconds)
        return max(0.1, base_delay + jitter)

    def _should_fallback_model(
        self,
        response: httpx.Response,
        model: str,
    ) -> bool:
        if model != self._primary_default_model or not self._fallback_model:
            return False
        if response.status_code not in (400, 404):
            return False

        body_text = ""
        try:
            parsed = response.json()
            body_text = str(parsed)
        except Exception:
            body_text = response.text

        lowered = body_text.lower()
        return "model" in lowered and any(
            marker in lowered
            for marker in ("invalid", "unsupported", "not found", "unknown", "available")
        )

    async def create_response(
        self,
        model: str,
        prompt: str,
        tool_config: dict[str, Any],
        max_turns: int,
        cache_key: str | None = None,
        on_request_attempt: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """
        Create a response using xAI's API with tools.

        Args:
            model: Model identifier (e.g., "grok-4-1-fast-reasoning")
            prompt: User prompt
            tool_config: Tool configuration dict
            max_turns: Maximum number of turns for tool use
            cache_key: Stable per-lane key used for xAI prompt caching
            on_request_attempt: Optional callback invoked before each outbound HTTP attempt

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
        base_payload = {
            "input": [{"role": "user", "content": prompt}],
            "tools": [tool_config],
            "max_turns": max(1, max_turns),
        }
        models_to_try = [model]
        if (
            self._fallback_model
            and model == self._primary_default_model
            and self._fallback_model != model
        ):
            models_to_try.append(self._fallback_model)

        last_error: Exception | None = None
        max_attempts = self._max_retries + 1

        for model_index, current_model in enumerate(models_to_try):
            payload = dict(base_payload)
            payload["model"] = current_model

            for attempt in range(max_attempts):
                try:
                    if on_request_attempt is not None:
                        on_request_attempt()

                    async with httpx.AsyncClient(
                        base_url=self._base_url,
                        headers=self._request_headers(current_model, cache_key),
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

                    if self._should_fallback_model(resp, current_model):
                        break

                    if resp.status_code >= 500 and attempt < max_attempts - 1:
                        await asyncio.sleep(self._retry_delay_seconds(attempt))
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
                    if attempt >= max_attempts - 1:
                        raise
                    await asyncio.sleep(self._retry_delay_seconds(attempt))

            if model_index >= len(models_to_try) - 1:
                break

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
    cleaned_excluded = [
        str(handle or "").strip().lstrip("@")
        for handle in (excluded_handles or [])
        if str(handle or "").strip().lstrip("@")
    ][:10]
    cleaned_allowed = [
        str(handle or "").strip().lstrip("@")
        for handle in (allowed_handles or [])
        if str(handle or "").strip().lstrip("@")
    ][:10]

    if cleaned_excluded and cleaned_allowed:
        raise ValueError(
            "x_search tool config cannot set allowed_x_handles and excluded_x_handles together"
        )

    tool: dict[str, Any] = {"type": "x_search"}

    if enable_image_understanding:
        tool["enable_image_understanding"] = True
    if enable_video_understanding:
        tool["enable_video_understanding"] = True

    if cleaned_excluded:
        tool["excluded_x_handles"] = cleaned_excluded
    if cleaned_allowed:
        tool["allowed_x_handles"] = cleaned_allowed

    if start_date:
        tool["from_date"] = start_date
    if end_date:
        tool["to_date"] = end_date

    return tool
