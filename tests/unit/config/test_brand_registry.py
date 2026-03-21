"""
Tests for twitter_intel.config.brand_registry module.

Tests SRS-YARA-XSS-2026 Section 4.1.3 Supported Brands
and Section 6.2 Brand Registry Schema.
"""

import pytest


class TestBrandConfig:
    """Tests for BrandConfig dataclass per SRS Section 6.2."""

    def test_brand_config_immutable(self):
        """BrandConfig should be immutable (frozen dataclass)."""
        from twitter_intel.config.brand_registry import BrandConfig

        brand = BrandConfig(
            brand_key="test",
            aliases=("Test",),
            handles=("test_handle",),
            excluded_handles=("test_handle",),
        )
        with pytest.raises(Exception):
            brand.brand_key = "modified"

    def test_get_handles_with_at(self):
        """Should return handles with @ prefix."""
        from twitter_intel.config.brand_registry import BrandConfig

        brand = BrandConfig(
            brand_key="grey",
            aliases=("Grey",),
            handles=("greyfinance", "greyfinanceEA"),
            excluded_handles=("greyfinance", "greyfinanceEA"),
        )
        handles = brand.get_handles_with_at()
        assert handles == ["@greyfinance", "@greyfinanceEA"]

    def test_get_excluded_handles_set(self):
        """Should return lowercase set of excluded handles."""
        from twitter_intel.config.brand_registry import BrandConfig

        brand = BrandConfig(
            brand_key="grey",
            aliases=("Grey",),
            handles=("greyfinance", "GreyFinanceEA"),
            excluded_handles=("GreyFinance", "GREYFINANCEEA"),
        )
        excluded = brand.get_excluded_handles_set()
        assert excluded == {"greyfinance", "greyfinanceea"}


class TestBrandRegistry:
    """Tests for BRAND_REGISTRY per SRS Section 4.1.3."""

    def test_has_seven_brands(self):
        """Registry should contain exactly 7 brands per SRS."""
        from twitter_intel.config.brand_registry import BRAND_REGISTRY

        assert len(BRAND_REGISTRY) == 7

    def test_brand_keys(self):
        """Registry should have correct brand keys per SRS."""
        from twitter_intel.config.brand_registry import get_brand_keys

        keys = set(get_brand_keys())
        assert keys == {
            "chipper",
            "grey",
            "lemfi",
            "raenest",
            "wise",
            "cleva",
            "remitly",
        }

    def test_chipper_brand(self):
        """Chipper config should match SRS Section 4.1.3."""
        from twitter_intel.config.brand_registry import get_brand

        brand = get_brand("chipper")
        assert brand is not None
        assert brand.aliases == ("Chipper", "Chipper Cash")
        assert brand.handles == ("chippercashapp",)
        assert brand.excluded_handles == ("chippercashapp",)
        assert brand.disambiguation_context is None

    def test_grey_brand_has_disambiguation(self):
        """Grey config should have disambiguation context per SRS."""
        from twitter_intel.config.brand_registry import get_brand

        brand = get_brand("grey")
        assert brand is not None
        assert brand.disambiguation_context == "the fintech/money transfer app"
        assert len(brand.handles) == 3

    def test_wise_brand_has_disambiguation(self):
        """Wise config should have disambiguation context per SRS."""
        from twitter_intel.config.brand_registry import get_brand

        brand = get_brand("wise")
        assert brand is not None
        assert brand.disambiguation_context == "the international money transfer company"

    def test_raenest_has_geegpay_alias(self):
        """Raenest config should include Geegpay alias per SRS."""
        from twitter_intel.config.brand_registry import get_brand

        brand = get_brand("raenest")
        assert brand is not None
        assert "Geegpay" in brand.aliases
        assert brand.disambiguation_context == "formerly Geegpay"

    def test_get_all_excluded_handles(self):
        """Should return combined set of all excluded handles."""
        from twitter_intel.config.brand_registry import get_all_excluded_handles

        handles = get_all_excluded_handles()
        assert "chippercashapp" in handles
        assert "greyfinance" in handles
        assert "uselemfi" in handles
        assert "wise" in handles


class TestScoringTerms:
    """Tests for scoring term lists per SRS Section 4.4.2."""

    def test_issue_terms_exist(self):
        """Should have issue terms for keyword matching."""
        from twitter_intel.config.brand_registry import ISSUE_TERMS

        assert "pending" in ISSUE_TERMS
        assert "failed" in ISSUE_TERMS
        assert "stuck" in ISSUE_TERMS
        assert "declined" in ISSUE_TERMS
        assert "blocked" in ISSUE_TERMS

    def test_first_person_terms_exist(self):
        """Should have first-person terms per SRS rubric."""
        from twitter_intel.config.brand_registry import FIRST_PERSON_TERMS

        assert "i" in FIRST_PERSON_TERMS
        assert "my" in FIRST_PERSON_TERMS
        assert "me" in FIRST_PERSON_TERMS
        assert "i'm" in FIRST_PERSON_TERMS
        assert "we" in FIRST_PERSON_TERMS

    def test_recovery_terms_exist(self):
        """Should have recovery timing terms per SRS rubric."""
        from twitter_intel.config.brand_registry import RECOVERY_TERMS

        assert "still" in RECOVERY_TERMS
        assert "again" in RECOVERY_TERMS
        assert "since" in RECOVERY_TERMS
        assert "after" in RECOVERY_TERMS
        assert "maintenance" in RECOVERY_TERMS

    def test_frustration_terms_exist(self):
        """Should have frustration/urgency terms per SRS rubric."""
        from twitter_intel.config.brand_registry import FRUSTRATION_TERMS

        assert "frustrated" in FRUSTRATION_TERMS
        assert "tired" in FRUSTRATION_TERMS
        assert "worst" in FRUSTRATION_TERMS
        assert "terrible" in FRUSTRATION_TERMS
        assert "fix" in FRUSTRATION_TERMS


class TestScoringWeights:
    """Tests for scoring weights per SRS Section 4.4.2."""

    def test_default_scoring_weights(self):
        """Default weights should match SRS Section 4.4.2."""
        from twitter_intel.config.brand_registry import DEFAULT_SCORING_WEIGHTS

        # Positive signals
        assert DEFAULT_SCORING_WEIGHTS.issue_keyword_present == 3
        assert DEFAULT_SCORING_WEIGHTS.first_person_language == 2
        assert DEFAULT_SCORING_WEIGHTS.brand_clearly_named == 2
        assert DEFAULT_SCORING_WEIGHTS.recovery_timing_language == 2
        assert DEFAULT_SCORING_WEIGHTS.frustration_urgency == 1

        # Negative signals
        assert DEFAULT_SCORING_WEIGHTS.official_brand_account == -3
        assert DEFAULT_SCORING_WEIGHTS.vague_generic_post == -2

        # Threshold
        assert DEFAULT_SCORING_WEIGHTS.minimum_score == 5
