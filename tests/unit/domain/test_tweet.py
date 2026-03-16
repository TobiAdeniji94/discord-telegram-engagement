"""
Tests for twitter_intel.domain.entities.tweet module.
"""

import pytest
from datetime import datetime, timezone
from twitter_intel.domain.entities.tweet import TweetCandidate, PreparedReviewCandidate


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


@pytest.fixture
def sample_analysis():
    """Create a sample analysis dict for testing."""
    return {
        "category": "solution-seekers",
        "sentiment": "positive",
        "confidence": 0.95,
        "replies": [
            {"text": "Hey! Yara.cash can help.", "tone": "friendly"},
            {"text": "We specialize in this!", "tone": "helpful"},
        ],
    }


class TestTweetCandidate:
    """Tests for TweetCandidate dataclass."""

    def test_required_fields(self, sample_tweet):
        """TweetCandidate should have all required fields."""
        assert sample_tweet.tweet_id == "1234567890"
        assert sample_tweet.text == "Looking for the best way to send money to Nigeria"
        assert sample_tweet.author_username == "test_user"
        assert sample_tweet.author_followers == 1000
        assert sample_tweet.likes == 50

    def test_default_values(self):
        """TweetCandidate should have correct defaults."""
        tweet = TweetCandidate(
            tweet_id="123",
            text="test",
            author_username="user",
            author_name="User",
            author_followers=100,
            url="https://x.com/user/status/123",
            created_at=datetime.now(timezone.utc),
            likes=0,
            retweets=0,
            replies=0,
            quotes=0,
            views=0,
            age_minutes=5.0,
            source_tab="Top",
            search_query="test",
            category_hint="brand_mention",
        )
        assert tweet.is_direct_mention is False
        assert tweet.local_score == 0.0

    def test_engagement_total(self, sample_tweet):
        """engagement_total should sum all engagement metrics."""
        expected = 50 + 10 + 5 + 2  # likes + retweets + replies + quotes
        assert sample_tweet.engagement_total == expected

    def test_is_test_tweet_smoke(self):
        """Smoke test tweets should be identified."""
        tweet = TweetCandidate(
            tweet_id="smoke-12345",
            text="test",
            author_username="user",
            author_name="User",
            author_followers=100,
            url="https://x.com/user/status/smoke-12345",
            created_at=datetime.now(timezone.utc),
            likes=0,
            retweets=0,
            replies=0,
            quotes=0,
            views=0,
            age_minutes=0.0,
            source_tab="Top",
            search_query="test",
            category_hint="brand_mention",
        )
        assert tweet.is_test_tweet is True

    def test_is_test_tweet_manual(self):
        """Manual ingest tweets should be identified."""
        tweet = TweetCandidate(
            tweet_id="manual-67890",
            text="test",
            author_username="user",
            author_name="User",
            author_followers=100,
            url="https://x.com/user/status/manual-67890",
            created_at=datetime.now(timezone.utc),
            likes=0,
            retweets=0,
            replies=0,
            quotes=0,
            views=0,
            age_minutes=0.0,
            source_tab="Top",
            search_query="test",
            category_hint="brand_mention",
        )
        assert tweet.is_test_tweet is True

    def test_is_test_tweet_real(self, sample_tweet):
        """Real tweets should not be identified as test tweets."""
        assert sample_tweet.is_test_tweet is False


class TestPreparedReviewCandidate:
    """Tests for PreparedReviewCandidate dataclass."""

    def test_creation(self, sample_tweet, sample_analysis):
        """PreparedReviewCandidate should be created correctly."""
        candidate = PreparedReviewCandidate(
            tweet=sample_tweet,
            analysis=sample_analysis,
            provider="twitterapi_io",
            source_query="send money Nigeria",
        )
        assert candidate.tweet == sample_tweet
        assert candidate.provider == "twitterapi_io"
        assert candidate.source_query == "send money Nigeria"

    def test_category_property(self, sample_tweet, sample_analysis):
        """category property should extract from analysis."""
        candidate = PreparedReviewCandidate(
            tweet=sample_tweet,
            analysis=sample_analysis,
            provider="test",
            source_query="test",
        )
        assert candidate.category == "solution-seekers"

    def test_category_default(self, sample_tweet):
        """category should default to 'irrelevant' if missing."""
        candidate = PreparedReviewCandidate(
            tweet=sample_tweet,
            analysis={},
            provider="test",
            source_query="test",
        )
        assert candidate.category == "irrelevant"

    def test_sentiment_property(self, sample_tweet, sample_analysis):
        """sentiment property should extract from analysis."""
        candidate = PreparedReviewCandidate(
            tweet=sample_tweet,
            analysis=sample_analysis,
            provider="test",
            source_query="test",
        )
        assert candidate.sentiment == "positive"

    def test_sentiment_default(self, sample_tweet):
        """sentiment should default to 'neutral' if missing."""
        candidate = PreparedReviewCandidate(
            tweet=sample_tweet,
            analysis={},
            provider="test",
            source_query="test",
        )
        assert candidate.sentiment == "neutral"

    def test_reply_options_property(self, sample_tweet, sample_analysis):
        """reply_options should extract from analysis."""
        candidate = PreparedReviewCandidate(
            tweet=sample_tweet,
            analysis=sample_analysis,
            provider="test",
            source_query="test",
        )
        assert len(candidate.reply_options) == 2
        assert candidate.reply_options[0]["text"] == "Hey! Yara.cash can help."

    def test_reply_options_default(self, sample_tweet):
        """reply_options should default to empty list if missing."""
        candidate = PreparedReviewCandidate(
            tweet=sample_tweet,
            analysis={},
            provider="test",
            source_query="test",
        )
        assert candidate.reply_options == []

    def test_confidence_property(self, sample_tweet, sample_analysis):
        """confidence property should extract from analysis."""
        candidate = PreparedReviewCandidate(
            tweet=sample_tweet,
            analysis=sample_analysis,
            provider="test",
            source_query="test",
        )
        assert candidate.confidence == 0.95

    def test_confidence_default(self, sample_tweet):
        """confidence should default to 0.0 if missing."""
        candidate = PreparedReviewCandidate(
            tweet=sample_tweet,
            analysis={},
            provider="test",
            source_query="test",
        )
        assert candidate.confidence == 0.0
