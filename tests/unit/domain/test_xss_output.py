"""
Tests for XSS output schema per SRS-YARA-XSS-2026 Section 6.1.
"""

import pytest
from datetime import datetime, timezone


class TestXSSCandidate:
    """Tests for XSSCandidate dataclass."""

    def test_create_candidate(self):
        """Should create candidate with required fields."""
        from twitter_intel.domain.entities.xss_output import XSSCandidate

        candidate = XSSCandidate(
            tweet_url="https://x.com/user/status/123",
            tweet_text="Test tweet",
            author_username="testuser",
            created_at_iso="2026-03-20T12:00:00Z",
            category="competitor_complaint",
            score=7,
            reason="issue_keyword+first_person",
        )
        assert candidate.tweet_url == "https://x.com/user/status/123"
        assert candidate.score == 7

    def test_to_dict(self):
        """Should serialize to dictionary."""
        from twitter_intel.domain.entities.xss_output import XSSCandidate

        candidate = XSSCandidate(
            tweet_url="https://x.com/user/status/123",
            tweet_text="Test",
            author_username="user",
            created_at_iso="2026-03-20T12:00:00Z",
            category="competitor_complaint",
            score=5,
            reason="test",
        )
        d = candidate.to_dict()
        assert d["tweet_url"] == "https://x.com/user/status/123"
        assert d["score"] == 5


class TestXSSSearchCycleOutput:
    """Tests for XSSSearchCycleOutput per SRS Section 6.1."""

    def test_create_output(self):
        """Should create search cycle output with defaults."""
        from twitter_intel.domain.entities.xss_output import XSSSearchCycleOutput

        output = XSSSearchCycleOutput(
            lane="competitor_complaint",
            brand_key="grey",
        )
        assert output.lane == "competitor_complaint"
        assert output.brand_key == "grey"
        assert output.candidates == []
        assert output.search_cycle_id  # UUID generated

    def test_add_candidate(self):
        """Should add candidate to output."""
        from twitter_intel.domain.entities.xss_output import XSSSearchCycleOutput

        output = XSSSearchCycleOutput(lane="competitor_complaint")
        now = datetime.now(timezone.utc)

        candidate = output.add_candidate(
            tweet_url="https://x.com/user/status/123",
            tweet_text="Test complaint",
            author_username="user",
            created_at=now,
            category="competitor_complaint",
            score=7,
            reason="test",
        )

        assert len(output.candidates) == 1
        assert candidate.score == 7

    def test_to_dict(self):
        """Should serialize to dictionary per SRS Section 6.1 schema."""
        from twitter_intel.domain.entities.xss_output import XSSSearchCycleOutput

        output = XSSSearchCycleOutput(
            lane="competitor_complaint",
            brand_key="grey",
            raw_result_count=10,
            filtered_result_count=3,
        )
        now = datetime.now(timezone.utc)
        output.add_candidate(
            tweet_url="https://x.com/user/status/123",
            tweet_text="Test",
            author_username="user",
            created_at=now,
            category="competitor_complaint",
            score=5,
            reason="test",
        )

        d = output.to_dict()

        # Verify SRS Section 6.1 schema fields
        assert "search_cycle_id" in d
        assert "search_timestamp_utc" in d
        assert d["lane"] == "competitor_complaint"
        assert d["brand_key"] == "grey"
        assert d["raw_result_count"] == 10
        assert d["filtered_result_count"] == 3
        assert len(d["candidates"]) == 1
        assert d["candidates"][0]["score"] == 5


class TestCreateSearchCycleOutput:
    """Tests for create_search_cycle_output factory function."""

    def test_creates_output_with_time_window(self):
        """Should create output with time window bounds."""
        from twitter_intel.domain.entities.xss_output import create_search_cycle_output

        restart = datetime(2026, 3, 20, 10, 0, 0, tzinfo=timezone.utc)
        lower = datetime(2026, 3, 20, 10, 30, 0, tzinfo=timezone.utc)
        upper = datetime(2026, 3, 20, 16, 0, 0, tzinfo=timezone.utc)

        output = create_search_cycle_output(
            lane="competitor_complaint",
            brand_key="grey",
            restart_time_utc=restart,
            filter_lower_bound=lower,
            filter_upper_bound=upper,
        )

        assert output.restart_time_utc is not None
        assert output.filter_lower_bound is not None
        assert output.filter_upper_bound is not None

    def test_creates_output_for_solution_seeker(self):
        """Should create output for solution seeker lane."""
        from twitter_intel.domain.entities.xss_output import create_search_cycle_output

        output = create_search_cycle_output(
            lane="solution_seeker",
            brand_key=None,
        )

        assert output.lane == "solution_seeker"
        assert output.brand_key is None
