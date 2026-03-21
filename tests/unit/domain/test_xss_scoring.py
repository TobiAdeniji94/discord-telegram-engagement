"""
Tests for SRS-YARA-XSS-2026 Section 4.4.2 Scoring Rubric.

Tests the score_candidate_xss function and ScoringResult.
"""

import pytest


class TestScoreCandidateXSS:
    """Tests for score_candidate_xss function per SRS Section 4.4.2."""

    def test_issue_keyword_scores_3_points(self):
        """Concrete issue keyword should add +3 points."""
        from twitter_intel.domain.services.scoring import score_candidate_xss

        result = score_candidate_xss(
            tweet_text="My transfer has been pending for days!",
            author_username="user123",
        )
        assert result.issue_keyword_points == 3

    def test_first_person_scores_2_points(self):
        """First-person language should add +2 points."""
        from twitter_intel.domain.services.scoring import score_candidate_xss

        result = score_candidate_xss(
            tweet_text="I have a problem with the app",
            author_username="user123",
        )
        assert result.first_person_points == 2

    def test_brand_named_scores_2_points(self):
        """Brand clearly named should add +2 points."""
        from twitter_intel.domain.services.scoring import score_candidate_xss
        from twitter_intel.config.brand_registry import get_brand

        brand = get_brand("grey")
        result = score_candidate_xss(
            tweet_text="Grey app is not working again",
            author_username="user123",
            brand_config=brand,
        )
        assert result.brand_named_points == 2

    def test_recovery_timing_scores_2_points(self):
        """Recovery timing language should add +2 points."""
        from twitter_intel.domain.services.scoring import score_candidate_xss

        result = score_candidate_xss(
            tweet_text="Still waiting for my money after maintenance",
            author_username="user123",
        )
        assert result.recovery_timing_points == 2

    def test_frustration_scores_1_point(self):
        """Frustration/urgency indicators should add +1 point."""
        from twitter_intel.domain.services.scoring import score_candidate_xss

        result = score_candidate_xss(
            tweet_text="This is terrible, how long will it take?",
            author_username="user123",
        )
        assert result.frustration_points == 1

    def test_official_account_penalty(self):
        """Official brand account should subtract -3 points."""
        from twitter_intel.domain.services.scoring import score_candidate_xss
        from twitter_intel.config.brand_registry import get_brand

        brand = get_brand("chipper")
        result = score_candidate_xss(
            tweet_text="We are working on resolving the issue",
            author_username="chippercashapp",
            brand_config=brand,
        )
        assert result.official_account_penalty == -3

    def test_vague_post_penalty(self):
        """Vague/generic post should subtract -2 points."""
        from twitter_intel.domain.services.scoring import score_candidate_xss

        result = score_candidate_xss(
            tweet_text="Not happy with this app",  # No issue terms, <100 chars
            author_username="user123",
        )
        assert result.vague_post_penalty == -2

    def test_high_quality_complaint_scores_above_threshold(self):
        """High-quality complaint should score >= 5."""
        from twitter_intel.domain.services.scoring import score_candidate_xss
        from twitter_intel.config.brand_registry import get_brand

        brand = get_brand("grey")
        result = score_candidate_xss(
            tweet_text=(
                "I've been waiting for my Grey transfer for 3 days now. "
                "This is so frustrating. My money is still pending!"
            ),
            author_username="frustrated_user",
            brand_config=brand,
        )
        # issue_keyword(3) + first_person(2) + brand_named(2) +
        # recovery_timing(2) + frustration(1) = 10
        assert result.total_score >= 5
        assert result.passes_threshold

    def test_low_quality_post_scores_below_threshold(self):
        """Low-quality post should score < 5."""
        from twitter_intel.domain.services.scoring import score_candidate_xss

        result = score_candidate_xss(
            tweet_text="lol",  # Very short, no signals
            author_username="user123",
        )
        assert result.total_score < 5
        assert not result.passes_threshold

    def test_scoring_result_has_reason(self):
        """ScoringResult should include reason breakdown."""
        from twitter_intel.domain.services.scoring import score_candidate_xss

        result = score_candidate_xss(
            tweet_text="My transfer failed and I'm frustrated",
            author_username="user123",
        )
        assert "issue_keyword" in result.reason
        assert "first_person" in result.reason
        assert "frustration" in result.reason

    def test_brand_key_lookup(self):
        """Should work with brand_key instead of brand_config."""
        from twitter_intel.domain.services.scoring import score_candidate_xss

        result = score_candidate_xss(
            tweet_text="LemFi is not working for me",
            author_username="user123",
            brand_key="lemfi",
        )
        assert result.brand_named_points == 2


class TestScoringResult:
    """Tests for ScoringResult dataclass."""

    def test_passes_threshold_true(self):
        """passes_threshold should return True for score >= 5."""
        from twitter_intel.domain.services.scoring import ScoringResult

        result = ScoringResult(total_score=5)
        assert result.passes_threshold is True

        result = ScoringResult(total_score=10)
        assert result.passes_threshold is True

    def test_passes_threshold_false(self):
        """passes_threshold should return False for score < 5."""
        from twitter_intel.domain.services.scoring import ScoringResult

        result = ScoringResult(total_score=4)
        assert result.passes_threshold is False

        result = ScoringResult(total_score=0)
        assert result.passes_threshold is False


class TestScoringSRSCompliance:
    """Integration tests for SRS Section 4.4.2 compliance."""

    def test_complaint_scenarios(self):
        """Test various complaint scenarios per SRS examples."""
        from twitter_intel.domain.services.scoring import score_candidate_xss
        from twitter_intel.config.brand_registry import get_brand

        # Scenario 1: Clear complaint with all signals
        grey = get_brand("grey")
        result = score_candidate_xss(
            tweet_text=(
                "@greyfinance why is my transfer still pending after 2 days? "
                "I'm so tired of waiting, this is ridiculous!"
            ),
            author_username="real_user",
            brand_config=grey,
        )
        assert result.total_score >= 8
        assert result.passes_threshold

        # Scenario 2: Short vague post
        result = score_candidate_xss(
            tweet_text="grey sucks",
            author_username="random_user",
            brand_config=grey,
        )
        # brand_named(2) + vague_penalty(-2) = 0
        assert result.total_score < 5

        # Scenario 3: Official account response
        result = score_candidate_xss(
            tweet_text="We apologize for the delay. Our team is working on it.",
            author_username="greyfinance",
            brand_config=grey,
        )
        assert result.official_account_penalty == -3
        assert result.total_score < 5
