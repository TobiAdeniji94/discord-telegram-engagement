"""
Tests for twitter_intel.config.search_queries module.
"""

import pytest


class TestSearchQuery:
    """Tests for SearchQuery dataclass."""

    def test_required_fields(self):
        """SearchQuery should require query, category_hint, and description."""
        from twitter_intel.config import SearchQuery

        query = SearchQuery(
            query="test query",
            category_hint="solution_seeker",
            description="Test description",
        )
        assert query.query == "test query"
        assert query.category_hint == "solution_seeker"
        assert query.description == "Test description"

    def test_default_values(self):
        """SearchQuery should have sensible defaults."""
        from twitter_intel.config import SearchQuery

        query = SearchQuery(
            query="test",
            category_hint="brand_mention",
            description="test",
        )
        assert query.query_type == "Top"
        assert query.cooldown_seconds == 3600
        assert query.max_pages == 1
        assert query.enabled is True
        assert query.strategy_mode == "always_on"
        assert query.brand_aliases == []

    def test_custom_values(self):
        """SearchQuery should accept custom values."""
        from twitter_intel.config import SearchQuery

        query = SearchQuery(
            query="custom query",
            category_hint="competitor_complaint",
            description="Custom description",
            query_type="Latest",
            cooldown_seconds=1800,
            max_pages=3,
            enabled=False,
            brand_handles=["@BrandA", "brandb"],
            exclude_author_handles=[],
            strategy_mode="anchored_event",
        )
        assert query.query_type == "Latest"
        assert query.cooldown_seconds == 1800
        assert query.max_pages == 3
        assert query.enabled is False
        assert query.brand_handles == ["BrandA", "brandb"]
        assert query.exclude_author_handles == ["BrandA", "brandb"]
        assert query.strategy_mode == "anchored_event"


class TestDefaultSearchQueries:
    """Tests for DEFAULT_SEARCH_QUERIES (2026 core-brand set)."""

    def test_has_eight_default_queries(self):
        """Should have exactly 8 default search queries."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        assert len(DEFAULT_SEARCH_QUERIES) == 8

    def test_competitor_complaint_queries(self):
        """Should have one competitor complaint lane per core brand."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        competitor_queries = [
            q for q in DEFAULT_SEARCH_QUERIES if q.category_hint == "competitor_complaint"
        ]
        assert len(competitor_queries) == 7
        families = {q.brand_family for q in competitor_queries}
        assert families == {
            "chipper",
            "grey",
            "lemfi",
            "raenest",
            "wise",
            "cleva",
            "remitly",
        }

    def test_solution_seeker_queries(self):
        """Should have one generic solution seeker lane."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        seeker_queries = [
            q for q in DEFAULT_SEARCH_QUERIES if q.category_hint == "solution_seeker"
        ]
        assert len(seeker_queries) == 1

        for query in seeker_queries:
            assert query.cooldown_seconds == 1800
            assert query.brand_family == ""
            assert "crypto off-ramp and cash-out options" in query.issue_focus

    def test_brand_mention_queries(self):
        """Default lane set should not include separate brand mention lanes."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        brand_queries = [
            q for q in DEFAULT_SEARCH_QUERIES if q.category_hint == "brand_mention"
        ]
        assert len(brand_queries) == 0

    def test_all_queries_enabled_by_default(self):
        """All default queries should be enabled."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        for query in DEFAULT_SEARCH_QUERIES:
            assert query.enabled is True

    def test_most_queries_use_latest_type(self):
        """All default lanes should use Latest as a provider hint."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        latest_count = sum(1 for q in DEFAULT_SEARCH_QUERIES if q.query_type == "Latest")
        top_count = sum(1 for q in DEFAULT_SEARCH_QUERIES if q.query_type == "Top")

        assert latest_count == 8
        assert top_count == 0

    def test_queries_have_structured_lane_metadata(self):
        """Default queries should carry structured lane metadata."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        for query in DEFAULT_SEARCH_QUERIES:
            assert query.lane_id
            assert query.intent_summary
            assert query.priority > 0
            if query.category_hint == "competitor_complaint":
                assert query.brand_aliases
                assert query.exclude_author_handles
