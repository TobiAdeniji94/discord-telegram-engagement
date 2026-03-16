"""
Tests for scoring service.
"""

import pytest
from datetime import datetime, timezone

from twitter_intel.domain.services import (
    score_candidate,
    get_score_threshold,
    passes_score_threshold,
    filter_candidates,
    format_discarded_candidates,
)
from twitter_intel.domain.entities.tweet import TweetCandidate


@pytest.fixture
def base_tweet():
    """Create a base tweet for testing."""
    return TweetCandidate(
        tweet_id="123",
        text="Test tweet",
        author_username="user",
        author_name="User",
        author_followers=1000,
        url="https://x.com/user/status/123",
        created_at=datetime.now(timezone.utc),
        likes=10,
        retweets=5,
        replies=3,
        quotes=2,
        views=1000,
        age_minutes=30.0,
        source_tab="Top",
        search_query="test",
        category_hint="brand_mention",
    )


class TestScoreCandidate:
    """Tests for score_candidate function."""

    def test_calculates_base_score(self, base_tweet):
        """Should calculate score from engagement metrics."""
        score = score_candidate(base_tweet)
        # replies*4 + likes*1.5 + retweets*2 + quotes*2 + views/500 + followers/5000 + category bonus
        # 3*4 + 10*1.5 + 5*2 + 2*2 + 2 + 0.2 + 1 (brand_mention)
        # = 12 + 15 + 10 + 4 + 2 + 0.2 + 1 = 44.2
        assert score > 40  # Approximate check

    def test_solution_seeker_bonus(self, base_tweet):
        """Solution seekers should get +3 bonus."""
        base_tweet.category_hint = "solution_seeker"
        score_seeker = score_candidate(base_tweet)

        base_tweet.category_hint = "brand_mention"
        score_brand = score_candidate(base_tweet)

        assert score_seeker == score_brand + 2  # +3 vs +1

    def test_competitor_complaint_bonus(self, base_tweet):
        """Competitor complaints should get +2 bonus."""
        base_tweet.category_hint = "competitor_complaint"
        score_complaint = score_candidate(base_tweet)

        base_tweet.category_hint = "brand_mention"
        score_brand = score_candidate(base_tweet)

        assert score_complaint == score_brand + 1  # +2 vs +1

    def test_old_tweet_penalty(self, base_tweet):
        """Old tweets should be penalized."""
        base_tweet.age_minutes = 30
        score_fresh = score_candidate(base_tweet)

        base_tweet.age_minutes = 150  # > 120 min
        score_medium = score_candidate(base_tweet)

        base_tweet.age_minutes = 400  # > 360 min
        score_old = score_candidate(base_tweet)

        assert score_fresh > score_medium
        assert score_medium > score_old

    def test_views_capped(self, base_tweet):
        """Views contribution should be capped at 8.0."""
        base_tweet.views = 100
        score_low = score_candidate(base_tweet)

        base_tweet.views = 100000  # Very high
        score_high = score_candidate(base_tweet)

        # Difference should be at most 8 (the cap)
        assert score_high - score_low <= 8

    def test_followers_capped(self, base_tweet):
        """Followers contribution should be capped at 4.0."""
        base_tweet.author_followers = 100
        score_low = score_candidate(base_tweet)

        base_tweet.author_followers = 1000000  # Very high
        score_high = score_candidate(base_tweet)

        # Difference should be at most 4 (the cap)
        assert score_high - score_low <= 4


class TestGetScoreThreshold:
    """Tests for get_score_threshold function."""

    def test_direct_mention_has_no_threshold(self, base_tweet):
        """Direct mentions should have no threshold."""
        base_tweet.category_hint = "brand_mention"
        base_tweet.is_direct_mention = True

        threshold = get_score_threshold(base_tweet)
        assert threshold is None

    def test_competitor_complaint_threshold(self, base_tweet):
        """Competitor complaints should have threshold of 12."""
        base_tweet.category_hint = "competitor_complaint"
        threshold = get_score_threshold(base_tweet)
        assert threshold == 12.0

    def test_solution_seeker_threshold(self, base_tweet):
        """Solution seekers should have threshold of 11."""
        base_tweet.category_hint = "solution_seeker"
        threshold = get_score_threshold(base_tweet)
        assert threshold == 11.0

    def test_brand_mention_threshold(self, base_tweet):
        """Brand mentions should have threshold of 6."""
        base_tweet.category_hint = "brand_mention"
        base_tweet.is_direct_mention = False
        threshold = get_score_threshold(base_tweet)
        assert threshold == 6.0

    def test_unknown_category_default(self, base_tweet):
        """Unknown categories should default to 10."""
        base_tweet.category_hint = "unknown"
        threshold = get_score_threshold(base_tweet)
        assert threshold == 10.0


class TestPassesScoreThreshold:
    """Tests for passes_score_threshold function."""

    def test_direct_mention_always_passes(self, base_tweet):
        """Direct mentions should always pass."""
        base_tweet.category_hint = "brand_mention"
        base_tweet.is_direct_mention = True
        base_tweet.local_score = 0.0

        assert passes_score_threshold(base_tweet) is True

    def test_passes_above_threshold(self, base_tweet):
        """Tweets above threshold should pass."""
        base_tweet.category_hint = "brand_mention"
        base_tweet.local_score = 10.0  # Above 6.0

        assert passes_score_threshold(base_tweet) is True

    def test_fails_below_threshold(self, base_tweet):
        """Tweets below threshold should fail."""
        base_tweet.category_hint = "competitor_complaint"
        base_tweet.local_score = 5.0  # Below 12.0

        assert passes_score_threshold(base_tweet) is False


class TestFilterCandidates:
    """Tests for filter_candidates function."""

    def test_removes_duplicates(self):
        """Should remove duplicate tweet IDs."""
        now = datetime.now(timezone.utc)
        tweets = [
            TweetCandidate(
                tweet_id="123", text="t1", author_username="u", author_name="U",
                author_followers=100, url="url1", created_at=now, likes=10,
                retweets=5, replies=3, quotes=2, views=1000, age_minutes=30,
                source_tab="Top", search_query="q", category_hint="brand_mention",
            ),
            TweetCandidate(
                tweet_id="123", text="t2", author_username="u", author_name="U",
                author_followers=100, url="url2", created_at=now, likes=10,
                retweets=5, replies=3, quotes=2, views=1000, age_minutes=30,
                source_tab="Top", search_query="q", category_hint="brand_mention",
            ),
        ]

        filtered, discarded = filter_candidates(tweets, 120, set())

        assert len(filtered) == 1
        assert len(discarded) == 1
        assert discarded[0][2] == "duplicate_in_scan"

    def test_removes_already_processed(self):
        """Should remove already processed tweets."""
        now = datetime.now(timezone.utc)
        tweets = [
            TweetCandidate(
                tweet_id="123", text="t", author_username="u", author_name="U",
                author_followers=100, url="url", created_at=now, likes=10,
                retweets=5, replies=3, quotes=2, views=1000, age_minutes=30,
                source_tab="Top", search_query="q", category_hint="brand_mention",
            ),
        ]

        filtered, discarded = filter_candidates(tweets, 120, {"123"})

        assert len(filtered) == 0
        assert len(discarded) == 1
        assert discarded[0][2] == "already_processed"

    def test_removes_old_tweets(self):
        """Should remove tweets older than max age."""
        now = datetime.now(timezone.utc)
        tweets = [
            TweetCandidate(
                tweet_id="123", text="t", author_username="u", author_name="U",
                author_followers=100, url="url", created_at=now, likes=10,
                retweets=5, replies=3, quotes=2, views=1000, age_minutes=150,  # > 120
                source_tab="Top", search_query="q", category_hint="brand_mention",
            ),
        ]

        filtered, discarded = filter_candidates(tweets, 120, set())

        assert len(filtered) == 0
        assert len(discarded) == 1
        assert discarded[0][2] == "too_old"

    def test_sorts_by_score(self):
        """Should sort results by score descending."""
        now = datetime.now(timezone.utc)
        tweets = [
            TweetCandidate(
                tweet_id="1", text="t", author_username="u", author_name="U",
                author_followers=100, url="url", created_at=now, likes=1,
                retweets=1, replies=1, quotes=1, views=100, age_minutes=30,
                source_tab="Top", search_query="q", category_hint="brand_mention",
            ),
            TweetCandidate(
                tweet_id="2", text="t", author_username="u", author_name="U",
                author_followers=100, url="url", created_at=now, likes=100,
                retweets=50, replies=30, quotes=20, views=10000, age_minutes=30,
                source_tab="Top", search_query="q", category_hint="brand_mention",
            ),
        ]

        filtered, _ = filter_candidates(tweets, 120, set())

        assert len(filtered) == 2
        assert filtered[0].tweet_id == "2"  # Higher score first
        assert filtered[0].local_score > filtered[1].local_score


class TestFormatDiscardedCandidates:
    """Tests for format_discarded_candidates function."""

    def test_formats_correctly(self):
        """Should format discarded candidates."""
        discarded = [
            ("123456789012345", 25.5, "too_old"),
            ("987654321098765", 30.0, "duplicate"),
        ]

        result = format_discarded_candidates(discarded)

        assert len(result) == 2
        assert "30.0" in result[0]  # Sorted by score
        assert "duplicate" in result[0]

    def test_respects_limit(self):
        """Should respect the limit parameter."""
        discarded = [
            ("1", 10.0, "reason1"),
            ("2", 20.0, "reason2"),
            ("3", 30.0, "reason3"),
            ("4", 40.0, "reason4"),
        ]

        result = format_discarded_candidates(discarded, limit=2)

        assert len(result) == 2

    def test_handles_empty_list(self):
        """Should handle empty list."""
        result = format_discarded_candidates([])
        assert result == []
