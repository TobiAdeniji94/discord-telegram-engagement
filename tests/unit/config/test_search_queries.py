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
        )
        assert query.query_type == "Latest"
        assert query.cooldown_seconds == 1800
        assert query.max_pages == 3
        assert query.enabled is False


class TestDefaultSearchQueries:
    """Tests for DEFAULT_SEARCH_QUERIES."""

    def test_has_three_default_queries(self):
        """Should have exactly 3 default search queries."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        assert len(DEFAULT_SEARCH_QUERIES) == 3

    def test_competitor_complaint_query(self):
        """First query should target competitor complaints."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        query = DEFAULT_SEARCH_QUERIES[0]
        assert query.category_hint == "competitor_complaint"
        assert "chipper cash" in query.query.lower()
        assert "lemfi" in query.query.lower()
        assert "wise" in query.query.lower()
        assert query.cooldown_seconds == 3600

    def test_solution_seeker_query(self):
        """Second query should target solution seekers."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        query = DEFAULT_SEARCH_QUERIES[1]
        assert query.category_hint == "solution_seeker"
        assert "send money" in query.query.lower()
        assert "dollar card" in query.query.lower()
        assert "nigeria" in query.query.lower()
        assert query.cooldown_seconds == 2700

    def test_brand_mention_query(self):
        """Third query should target brand mentions."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        query = DEFAULT_SEARCH_QUERIES[2]
        assert query.category_hint == "brand_mention"
        assert "yara.cash" in query.query.lower()
        assert query.cooldown_seconds == 900

    def test_all_queries_enabled_by_default(self):
        """All default queries should be enabled."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        for query in DEFAULT_SEARCH_QUERIES:
            assert query.enabled is True

    def test_all_queries_use_top_type(self):
        """All default queries should use 'Top' query type."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        for query in DEFAULT_SEARCH_QUERIES:
            assert query.query_type == "Top"
