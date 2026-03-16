"""
Unit tests for AI classifier infrastructure.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from twitter_intel.infrastructure.ai import (
    GeminiClassifier,
    NullClassifier,
    build_classification_prompt,
    clean_json_response,
)
from twitter_intel.domain.interfaces import AIClassifier
from twitter_intel.domain.entities.tweet import TweetCandidate


@pytest.fixture
def sample_tweet():
    """Create a sample TweetCandidate for testing."""
    return TweetCandidate(
        tweet_id="1234567890",
        text="Looking for the best way to send money to Nigeria",
        author_username="test_user",
        author_name="Test User",
        author_followers=1000,
        url="https://x.com/test_user/status/1234567890",
        created_at=datetime.now(timezone.utc),
        likes=50,
        retweets=10,
        replies=5,
        quotes=2,
        views=5000,
        age_minutes=30.0,
        source_tab="Top",
        search_query="send money Nigeria",
        category_hint="solution_seeker",
    )


class TestBuildClassificationPrompt:
    """Tests for build_classification_prompt function."""

    def test_includes_brand_context(self):
        """Prompt should include brand context."""
        prompt = build_classification_prompt(
            brand_context="Test brand context",
            author_username="user",
            author_name="User",
            tweet_text="Tweet text",
            likes=10,
            replies=5,
            retweets=3,
            views=100,
            age_minutes=30.0,
            search_query="test query",
            category_hint="brand_mention",
            num_reply_options=4,
        )
        assert "Test brand context" in prompt

    def test_includes_tweet_info(self):
        """Prompt should include tweet information."""
        prompt = build_classification_prompt(
            brand_context="Brand",
            author_username="testuser",
            author_name="Test User",
            tweet_text="This is the tweet content",
            likes=50,
            replies=10,
            retweets=5,
            views=1000,
            age_minutes=45.0,
            search_query="search query",
            category_hint="solution_seeker",
            num_reply_options=3,
        )
        assert "@testuser" in prompt
        assert "Test User" in prompt
        assert "This is the tweet content" in prompt
        assert "50 likes" in prompt
        assert "10 replies" in prompt
        assert "45 minutes old" in prompt
        assert "solution_seeker" in prompt

    def test_includes_reply_count(self):
        """Prompt should specify number of reply options."""
        prompt = build_classification_prompt(
            brand_context="Brand",
            author_username="user",
            author_name="User",
            tweet_text="Tweet",
            likes=0,
            replies=0,
            retweets=0,
            views=0,
            age_minutes=0.0,
            search_query="query",
            category_hint="hint",
            num_reply_options=6,
        )
        assert "GENERATE 6 REPLY OPTIONS" in prompt

    def test_includes_json_format(self):
        """Prompt should include expected JSON format."""
        prompt = build_classification_prompt(
            brand_context="Brand",
            author_username="user",
            author_name="User",
            tweet_text="Tweet",
            likes=0,
            replies=0,
            retweets=0,
            views=0,
            age_minutes=0.0,
            search_query="query",
            category_hint="hint",
            num_reply_options=4,
        )
        assert '"category"' in prompt
        assert '"sentiment"' in prompt
        assert '"confidence"' in prompt
        assert '"replies"' in prompt


class TestCleanJsonResponse:
    """Tests for clean_json_response function."""

    def test_removes_markdown_fences(self):
        """Should remove markdown code fences."""
        text = '```json\n{"key": "value"}\n```'
        result = clean_json_response(text)
        assert result == '{"key": "value"}'

    def test_removes_json_identifier(self):
        """Should remove json language identifier."""
        text = 'json\n{"key": "value"}'
        result = clean_json_response(text)
        assert result == '{"key": "value"}'

    def test_strips_whitespace(self):
        """Should strip leading/trailing whitespace."""
        text = '  \n{"key": "value"}\n  '
        result = clean_json_response(text)
        assert result == '{"key": "value"}'

    def test_handles_clean_json(self):
        """Should handle already clean JSON."""
        text = '{"key": "value"}'
        result = clean_json_response(text)
        assert result == '{"key": "value"}'

    def test_handles_only_fences(self):
        """Should handle code fences without newlines."""
        text = '```{"key": "value"}```'
        result = clean_json_response(text)
        # Should strip opening fence at least
        assert "```" not in result or result.count("```") < 2


class TestGeminiClassifier:
    """Tests for GeminiClassifier."""

    def test_implements_ai_classifier(self):
        """Classifier should implement AIClassifier interface."""
        classifier = GeminiClassifier("test_key")
        assert isinstance(classifier, AIClassifier)

    def test_name_property(self):
        """Classifier should have correct name."""
        classifier = GeminiClassifier("test_key")
        assert classifier.name == "gemini"

    def test_default_model(self):
        """Classifier should use default model."""
        classifier = GeminiClassifier("test_key")
        assert classifier._model_name == "gemini-2.0-flash"

    def test_custom_model(self):
        """Classifier should accept custom model."""
        classifier = GeminiClassifier("test_key", model="gemini-pro")
        assert classifier._model_name == "gemini-pro"


class TestNullClassifier:
    """Tests for NullClassifier."""

    def test_implements_ai_classifier(self):
        """Classifier should implement AIClassifier interface."""
        classifier = NullClassifier()
        assert isinstance(classifier, AIClassifier)

    def test_name_property(self):
        """Classifier should have correct name."""
        classifier = NullClassifier()
        assert classifier.name == "null"

    @pytest.mark.asyncio
    async def test_returns_irrelevant(self, sample_tweet):
        """Classifier should return irrelevant classification."""
        classifier = NullClassifier()
        result = await classifier.classify_and_generate(
            tweet=sample_tweet,
            brand_context="Test brand",
            num_reply_options=4,
        )
        assert result is not None
        assert result["category"] == "irrelevant"
        assert result["sentiment"] == "neutral"
        assert result["confidence"] == 0.0
        assert result["replies"] == []

    @pytest.mark.asyncio
    async def test_returns_dict(self, sample_tweet):
        """Classifier should return dict with expected keys."""
        classifier = NullClassifier()
        result = await classifier.classify_and_generate(
            tweet=sample_tweet,
            brand_context="Test brand",
        )
        assert "category" in result
        assert "sentiment" in result
        assert "confidence" in result
        assert "themes" in result
        assert "urgency" in result
        assert "replies" in result
