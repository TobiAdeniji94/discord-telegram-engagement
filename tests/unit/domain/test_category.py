"""
Tests for twitter_intel.domain.entities.category module.
"""

import pytest
from twitter_intel.domain.entities.category import (
    TweetCategory,
    parse_smoke_category,
    category_to_hint,
    is_local_test_tweet_id,
)


class TestTweetCategory:
    """Tests for TweetCategory enum."""

    def test_category_values(self):
        """Categories should have the expected string values."""
        assert TweetCategory.COMPETITOR_COMPLAINT.value == "competitor-complaints"
        assert TweetCategory.SOLUTION_SEEKER.value == "solution-seekers"
        assert TweetCategory.BRAND_MENTION.value == "brand-mentions"
        assert TweetCategory.IRRELEVANT.value == "irrelevant"

    def test_category_is_str_enum(self):
        """Categories should be usable as strings."""
        # The .value gives the string value
        assert TweetCategory.BRAND_MENTION.value == "brand-mentions"
        # And enum should equal its string value
        assert TweetCategory.BRAND_MENTION == "brand-mentions"


class TestParseSmokeCategory:
    """Tests for parse_smoke_category function."""

    def test_none_defaults_to_brand_mention(self):
        """None input should return BRAND_MENTION."""
        result = parse_smoke_category(None)
        assert result == TweetCategory.BRAND_MENTION

    @pytest.mark.parametrize("input_value", [
        "brand", "Brand", "BRAND",
        "brand-mention", "brand_mention",
        "brand-mentions", "brand mentions",
        "mention", "mentions",
    ])
    def test_brand_mention_aliases(self, input_value):
        """Various brand mention aliases should parse correctly."""
        result = parse_smoke_category(input_value)
        assert result == TweetCategory.BRAND_MENTION

    @pytest.mark.parametrize("input_value", [
        "competitor", "Competitor",
        "competitor-complaint", "competitor_complaint",
        "competitor-complaints",
        "complaint", "complaints",
    ])
    def test_competitor_complaint_aliases(self, input_value):
        """Various competitor complaint aliases should parse correctly."""
        result = parse_smoke_category(input_value)
        assert result == TweetCategory.COMPETITOR_COMPLAINT

    @pytest.mark.parametrize("input_value", [
        "seeker", "seekers",
        "solution", "Solution",
        "solution-seeker", "solution_seeker",
        "solution-seekers",
    ])
    def test_solution_seeker_aliases(self, input_value):
        """Various solution seeker aliases should parse correctly."""
        result = parse_smoke_category(input_value)
        assert result == TweetCategory.SOLUTION_SEEKER

    def test_unknown_category_returns_none(self):
        """Unknown category should return None."""
        assert parse_smoke_category("unknown") is None
        assert parse_smoke_category("random") is None
        assert parse_smoke_category("") is None

    def test_whitespace_handling(self):
        """Should handle leading/trailing whitespace."""
        assert parse_smoke_category("  brand  ") == TweetCategory.BRAND_MENTION
        assert parse_smoke_category("\tcompetitor\n") == TweetCategory.COMPETITOR_COMPLAINT


class TestCategoryToHint:
    """Tests for category_to_hint function."""

    def test_competitor_complaint_hint(self):
        """COMPETITOR_COMPLAINT should map to 'competitor_complaint'."""
        assert category_to_hint(TweetCategory.COMPETITOR_COMPLAINT) == "competitor_complaint"

    def test_solution_seeker_hint(self):
        """SOLUTION_SEEKER should map to 'solution_seeker'."""
        assert category_to_hint(TweetCategory.SOLUTION_SEEKER) == "solution_seeker"

    def test_brand_mention_hint(self):
        """BRAND_MENTION should map to 'brand_mention'."""
        assert category_to_hint(TweetCategory.BRAND_MENTION) == "brand_mention"

    def test_irrelevant_defaults_to_brand_mention(self):
        """IRRELEVANT should default to 'brand_mention'."""
        assert category_to_hint(TweetCategory.IRRELEVANT) == "brand_mention"


class TestIsLocalTestTweetId:
    """Tests for is_local_test_tweet_id function."""

    def test_smoke_prefix(self):
        """IDs starting with 'smoke-' should be test tweets."""
        assert is_local_test_tweet_id("smoke-123456") is True
        assert is_local_test_tweet_id("smoke-abc") is True

    def test_manual_prefix(self):
        """IDs starting with 'manual-' should be test tweets."""
        assert is_local_test_tweet_id("manual-789") is True
        assert is_local_test_tweet_id("manual-xyz") is True

    def test_real_tweet_ids(self):
        """Real tweet IDs should not be test tweets."""
        assert is_local_test_tweet_id("1234567890") is False
        assert is_local_test_tweet_id("1761234567890123456") is False

    def test_partial_prefix_not_matched(self):
        """Partial prefixes should not match."""
        assert is_local_test_tweet_id("smok-123") is False
        assert is_local_test_tweet_id("manua-123") is False
        assert is_local_test_tweet_id("123-smoke") is False
