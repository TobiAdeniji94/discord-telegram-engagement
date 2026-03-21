"""
Brand Registry for XSS (X Search Subsystem).

Implements SRS-YARA-XSS-2026 Section 6.2 Brand Registry Schema.
Defines competitor brands with aliases, handles, excluded handles,
and disambiguation context for accurate X search queries.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class BrandConfig:
    """
    Brand configuration per SRS Section 6.2 Brand Registry Schema.

    Attributes:
        brand_key: Unique identifier for the brand (lowercase)
        aliases: List of brand name variations for search
        handles: Official X handles (without @)
        excluded_handles: Handles to exclude from results (official accounts)
        disambiguation_context: Context phrase to avoid false positives
    """
    brand_key: str
    aliases: tuple[str, ...]
    handles: tuple[str, ...]
    excluded_handles: tuple[str, ...]
    disambiguation_context: Optional[str] = None

    def get_handles_with_at(self) -> list[str]:
        """Return handles with @ prefix for display."""
        return [f"@{h}" for h in self.handles]

    def get_excluded_handles_set(self) -> set[str]:
        """Return excluded handles as lowercase set for matching."""
        return {h.lower() for h in self.excluded_handles}


# SRS Section 4.1.3 Supported Brands
BRAND_REGISTRY: dict[str, BrandConfig] = {
    "chipper": BrandConfig(
        brand_key="chipper",
        aliases=("Chipper", "Chipper Cash"),
        handles=("chippercashapp",),
        excluded_handles=("chippercashapp",),
        disambiguation_context=None,
    ),
    "grey": BrandConfig(
        brand_key="grey",
        aliases=("Grey", "greyfinance"),
        handles=("greyfinance", "greyfinanceEA", "greyfinanceMENA"),
        excluded_handles=("greyfinance", "greyfinanceEA", "greyfinanceMENA"),
        disambiguation_context="the fintech/money transfer app",
    ),
    "lemfi": BrandConfig(
        brand_key="lemfi",
        aliases=("LemFi", "Lemfi"),
        handles=("UseLemfi",),
        excluded_handles=("UseLemfi",),
        disambiguation_context=None,
    ),
    "raenest": BrandConfig(
        brand_key="raenest",
        aliases=("Raenest", "Geegpay"),
        handles=("RaenestApp", "RaenestHQ"),
        excluded_handles=("RaenestApp", "RaenestHQ"),
        disambiguation_context="formerly Geegpay",
    ),
    "wise": BrandConfig(
        brand_key="wise",
        aliases=("Wise",),
        handles=("Wise",),
        excluded_handles=("Wise",),
        disambiguation_context="the international money transfer company",
    ),
    "cleva": BrandConfig(
        brand_key="cleva",
        aliases=("Cleva",),
        handles=("clevabanking",),
        excluded_handles=("clevabanking",),
        disambiguation_context=None,
    ),
    "remitly": BrandConfig(
        brand_key="remitly",
        aliases=("Remitly",),
        handles=("remitly", "remitlysupport"),
        excluded_handles=("remitly", "remitlysupport"),
        disambiguation_context=None,
    ),
}


def get_brand(brand_key: str) -> Optional[BrandConfig]:
    """Get brand configuration by key."""
    return BRAND_REGISTRY.get(brand_key.lower())


def get_all_brands() -> list[BrandConfig]:
    """Get all registered brands."""
    return list(BRAND_REGISTRY.values())


def get_brand_keys() -> list[str]:
    """Get all registered brand keys."""
    return list(BRAND_REGISTRY.keys())


def get_all_excluded_handles() -> set[str]:
    """Get combined set of all excluded handles (lowercase)."""
    handles: set[str] = set()
    for brand in BRAND_REGISTRY.values():
        handles.update(brand.get_excluded_handles_set())
    return handles


# SRS Section 4.4.2 Scoring Rubric Term Lists
ISSUE_TERMS: tuple[str, ...] = (
    "pending",
    "failed",
    "stuck",
    "declined",
    "blocked",
    "restricted",
    "frozen",
    "locked",
    "delayed",
    "error",
    "issue",
    "problem",
    "not working",
    "doesn't work",
    "won't work",
    "can't",
    "cannot",
    "unable",
    "reject",
    "rejected",
)

FIRST_PERSON_TERMS: tuple[str, ...] = (
    "i",
    "my",
    "me",
    "i'm",
    "i've",
    "i'd",
    "i'll",
    "we",
    "our",
    "us",
    "we're",
    "we've",
)

RECOVERY_TERMS: tuple[str, ...] = (
    "still",
    "again",
    "since",
    "after",
    "came back",
    "maintenance",
    "outage",
    "downtime",
    "hours",
    "days",
    "weeks",
    "waiting",
)

FRUSTRATION_TERMS: tuple[str, ...] = (
    "frustrated",
    "frustrated",
    "tired",
    "worst",
    "terrible",
    "horrible",
    "awful",
    "fix",
    "how long",
    "do better",
    "seriously",
    "ridiculous",
    "unacceptable",
    "disappointed",
    "angry",
    "pissed",
    "scam",
    "joke",
    "pathetic",
)

# SRS Section 4.2.2 Solution-Seeker Indicators
SOLUTION_SEEKER_TERMS: tuple[str, ...] = (
    "recommend",
    "recommendation",
    "best",
    "alternative",
    "alternatives",
    "looking for",
    "need",
    "how to",
    "where can",
    "which",
    "any suggestions",
    "suggest",
    "help me",
    "advice",
    "options",
    "compare",
    "vs",
    "versus",
)

FREELANCER_TERMS: tuple[str, ...] = (
    "freelancer",
    "freelance",
    "remote worker",
    "remote work",
    "upwork",
    "fiverr",
    "toptal",
    "contractor",
    "client payment",
    "international payment",
    "usd",
    "dollars",
    "receive payment",
    "get paid",
    "payout",
)

GEO_TERMS: tuple[str, ...] = (
    "nigeria",
    "nigerian",
    "ghana",
    "ghanaian",
    "africa",
    "african",
    "lagos",
    "accra",
    "naira",
    "cedi",
)


@dataclass
class ScoringWeights:
    """
    Scoring weights per SRS Section 4.4.2.

    These are the exact point values from the SRS scoring rubric
    for competitor complaint lane candidates.
    """
    # Positive signals
    issue_keyword_present: int = 3
    first_person_language: int = 2
    brand_clearly_named: int = 2
    recovery_timing_language: int = 2
    frustration_urgency: int = 1

    # Negative signals
    official_brand_account: int = -3
    vague_generic_post: int = -2

    # Threshold
    minimum_score: int = 5


# Default scoring weights instance
DEFAULT_SCORING_WEIGHTS = ScoringWeights()
