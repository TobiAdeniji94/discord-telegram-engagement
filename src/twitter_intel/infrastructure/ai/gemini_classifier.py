"""
Gemini AI classifier implementation.

Provides tweet classification and reply generation using Google's Gemini.
"""

import asyncio
import json
import logging
from typing import Any, TYPE_CHECKING

from twitter_intel.domain.interfaces.ai_classifier import AIClassifier
from twitter_intel.infrastructure.ai.prompts import (
    build_classification_prompt,
    clean_json_response,
)

if TYPE_CHECKING:
    from twitter_intel.domain.entities.tweet import TweetCandidate

log = logging.getLogger("twitter_intel.ai.gemini")


class GeminiClassifier(AIClassifier):
    """
    Gemini-based tweet classifier and reply generator.

    Uses Google's Generative AI API to:
    1. Classify tweets into categories
    2. Analyze sentiment
    3. Generate contextual reply options
    """

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        """
        Initialize the Gemini classifier.

        Args:
            api_key: Google Generative AI API key
            model: Model identifier (default: gemini-2.0-flash)
        """
        self._api_key = api_key
        self._model_name = model
        self._configured = False

    @property
    def name(self) -> str:
        """Get classifier name."""
        return "gemini"

    def _ensure_configured(self) -> None:
        """Ensure the Gemini API is configured."""
        if not self._configured:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)
            self._configured = True

    async def classify_and_generate(
        self,
        tweet: "TweetCandidate",
        brand_context: str,
        num_reply_options: int = 4,
    ) -> dict[str, Any] | None:
        """
        Classify a tweet and generate reply options.

        Args:
            tweet: The tweet to classify
            brand_context: Brand positioning and tone guidelines
            num_reply_options: Number of reply options to generate

        Returns:
            Analysis dict or None if classification fails
        """
        import google.generativeai as genai

        self._ensure_configured()
        model = genai.GenerativeModel(self._model_name)

        prompt = build_classification_prompt(
            brand_context=brand_context,
            author_username=tweet.author_username,
            author_name=tweet.author_name,
            tweet_text=tweet.text,
            likes=tweet.likes,
            replies=tweet.replies,
            retweets=tweet.retweets,
            views=tweet.views,
            age_minutes=tweet.age_minutes,
            search_query=tweet.search_query,
            category_hint=tweet.category_hint,
            num_reply_options=num_reply_options,
        )

        try:
            # Run synchronous API call in thread pool
            response = await asyncio.to_thread(model.generate_content, prompt)
            text = clean_json_response(response.text)
            return json.loads(text)

        except json.JSONDecodeError as e:
            log.error("Gemini returned invalid JSON: %s", e)
            return None

        except Exception as e:
            log.error("Gemini classification error: %s", e)
            return None


class NullClassifier(AIClassifier):
    """
    Null classifier for testing and manual-only mode.

    Returns a default classification marking tweets as irrelevant.
    """

    @property
    def name(self) -> str:
        return "null"

    async def classify_and_generate(
        self,
        tweet: "TweetCandidate",
        brand_context: str,
        num_reply_options: int = 4,
    ) -> dict[str, Any] | None:
        """Return default irrelevant classification."""
        return {
            "category": "irrelevant",
            "sentiment": "neutral",
            "confidence": 0.0,
            "themes": [],
            "urgency": "low",
            "competitor_mentioned": None,
            "yara_angle": None,
            "replies": [],
        }
