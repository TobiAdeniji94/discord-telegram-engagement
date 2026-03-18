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
    """Tests for DEFAULT_SEARCH_QUERIES (2026 expanded set)."""

    def test_has_eleven_default_queries(self):
        """Should have exactly 11 default search queries."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        assert len(DEFAULT_SEARCH_QUERIES) == 11

    def test_competitor_complaint_queries(self):
        """Should have competitor complaint queries with expanded coverage."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        competitor_queries = [
            q for q in DEFAULT_SEARCH_QUERIES if q.category_hint == "competitor_complaint"
        ]
        assert len(competitor_queries) == 5

        # First query should have expanded competitor coverage
        main_competitor = competitor_queries[0]
        assert "chipper" in main_competitor.query.lower()
        assert "grey" in main_competitor.query.lower()
        assert "lemfi" in main_competitor.query.lower()
        assert "eversend" in main_competitor.query.lower()
        assert "geegpay" in main_competitor.query.lower()
        assert "nala" in main_competitor.query.lower()

    def test_solution_seeker_queries(self):
        """Should have solution seeker queries with fast cooldowns."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        seeker_queries = [
            q for q in DEFAULT_SEARCH_QUERIES if q.category_hint == "solution_seeker"
        ]
        assert len(seeker_queries) == 4

        # Solution seekers should have fast cooldowns (5 min or 10 min)
        for query in seeker_queries:
            assert query.cooldown_seconds <= 600  # 10 minutes max

    def test_brand_mention_queries(self):
        """Should have brand mention queries."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        brand_queries = [
            q for q in DEFAULT_SEARCH_QUERIES if q.category_hint == "brand_mention"
        ]
        assert len(brand_queries) == 2

        # Direct brand mentions
        direct = brand_queries[0]
        assert "yara.cash" in direct.query.lower()
        assert direct.cooldown_seconds == 300  # Fast brand monitoring

    def test_all_queries_enabled_by_default(self):
        """All default queries should be enabled."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        for query in DEFAULT_SEARCH_QUERIES:
            assert query.enabled is True

    def test_most_queries_use_latest_type(self):
        """Most queries should use 'Latest' for real-time monitoring."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        latest_count = sum(1 for q in DEFAULT_SEARCH_QUERIES if q.query_type == "Latest")
        top_count = sum(1 for q in DEFAULT_SEARCH_QUERIES if q.query_type == "Top")

        # 10 Latest, 1 Top (brand awareness tracking)
        assert latest_count == 10
        assert top_count == 1

    def test_queries_use_modern_operators(self):
        """Queries should use -is:retweet instead of -filter:retweets."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        for query in DEFAULT_SEARCH_QUERIES:
            assert "-filter:retweets" not in query.query
            assert "-is:retweet" in query.query
