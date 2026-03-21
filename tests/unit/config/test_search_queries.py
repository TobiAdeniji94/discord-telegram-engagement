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
    """Tests for DEFAULT_SEARCH_QUERIES (SRS-YARA-XSS-2026 compliant)."""

    def test_has_default_queries(self):
        """Should have complaint-type and seeker lanes per SRS."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        # 7 complaint brand lanes + 1 solution seeker lane = 8
        assert len(DEFAULT_SEARCH_QUERIES) == 8

    def test_competitor_complaint_queries(self):
        """Should have one complaint lane per core brand per SRS Section 4.1.3."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        competitor_queries = [
            q for q in DEFAULT_SEARCH_QUERIES if q.category_hint == "competitor_complaint"
        ]
        assert len(competitor_queries) == 7
        families = {q.brand_family for q in competitor_queries}
        # SRS Section 4.1.3 requires exactly these 7 brands
        assert families == {
            "chipper",
            "grey",
            "lemfi",
            "raenest",
            "wise",
            "cleva",
            "remitly",
        }
        counts = {
            family: sum(1 for q in competitor_queries if q.brand_family == family)
            for family in families
        }
        assert all(count == 1 for count in counts.values())

    def test_solution_seeker_queries(self):
        """Should have a single solution seeker lane per SRS Section 4.2."""
        from twitter_intel.config import DEFAULT_SEARCH_QUERIES

        seeker_queries = [
            q for q in DEFAULT_SEARCH_QUERIES if q.category_hint == "solution_seeker"
        ]
        assert len(seeker_queries) == 1

        for query in seeker_queries:
            assert query.cooldown_seconds == 900
            assert query.brand_family == ""

        # Should cover the core solution-seeker discovery lane per SRS Section 4.2
        lane_ids = {q.lane_id for q in seeker_queries}
        assert "solution-seeker-usd-payments" in lane_ids

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


class TestBuildStandardSearchQuery:
    """Tests for compiling structured lanes into raw provider queries."""

    def test_compiles_competitor_lane_with_handles(self):
        from twitter_intel.config.search_queries import SearchQuery, build_standard_search_query

        query = SearchQuery(
            query="Find complaints about LemFi from real users.",
            category_hint="competitor_complaint",
            description="LemFi complaints",
            intent_summary="Find complaints about LemFi from real users.",
            brand_family="lemfi",
            brand_aliases=["LemFi", "Lemfi"],
            brand_handles=["UseLemfi"],
            issue_focus=["pending or stuck transfers", "verification or OTP problems"],
            geo_focus=["Nigeria", "Ghana", "Africa"],
        )

        compiled = build_standard_search_query(query)

        assert "@UseLemfi" in compiled
        assert "to:UseLemfi" in compiled
        assert "pending" in compiled
        assert "lang:en -is:retweet" in compiled

    def test_compiles_solution_seeker_lane(self):
        from twitter_intel.config.search_queries import SearchQuery, build_standard_search_query

        query = SearchQuery(
            query="Find solution seekers for conversion and virtual cards.",
            category_hint="solution_seeker",
            description="Seekers",
            intent_summary="Find solution seekers for conversion and virtual cards.",
            issue_focus=[
                "fiat or crypto conversion",
                "crypto off-ramp and cash-out options",
                "virtual USD cards",
            ],
            geo_focus=["Nigeria", "Ghana", "Africa"],
        )

        compiled = build_standard_search_query(query)

        assert "crypto" in compiled
        assert '"virtual USD cards"' in compiled or '"virtual card"' in compiled or "virtual" in compiled
        assert "recommend" in compiled
        assert "lang:en -is:retweet" in compiled

    def test_preserves_explicit_operator_query(self):
        from twitter_intel.config.search_queries import SearchQuery, build_standard_search_query

        query = SearchQuery(
            query='("wise" OR "@Wise") (failed OR pending) lang:en -is:retweet',
            category_hint="competitor_complaint",
            description="Wise complaints",
            intent_summary="Find complaints about Wise from real users.",
            brand_family="wise",
            brand_aliases=["Wise"],
            brand_handles=["Wise"],
        )

        compiled = build_standard_search_query(query)

        assert compiled == query.query
