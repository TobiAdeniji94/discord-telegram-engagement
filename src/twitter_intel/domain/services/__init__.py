"""
Domain services for Twitter Intelligence Bot.

Contains business logic services like scoring and classification.
Includes SRS-YARA-XSS-2026 Section 4.4.2 compliant scoring.
"""

from twitter_intel.domain.services.scoring import (
    score_candidate,
    get_score_threshold,
    passes_score_threshold,
    filter_candidates,
    format_discarded_candidates,
    # SRS-YARA-XSS-2026 Section 4.4.2 scoring
    score_candidate_xss,
    ScoringResult,
)

__all__ = [
    "score_candidate",
    "get_score_threshold",
    "passes_score_threshold",
    "filter_candidates",
    "format_discarded_candidates",
    # SRS-YARA-XSS-2026 Section 4.4.2 scoring
    "score_candidate_xss",
    "ScoringResult",
]
