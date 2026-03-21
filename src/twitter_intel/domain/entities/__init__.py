"""
Domain entities for Twitter Intelligence Bot.

Contains the core domain objects: TweetCategory, TweetCandidate, etc.
Includes XSS output schema per SRS-YARA-XSS-2026 Section 6.1.
"""

from twitter_intel.domain.entities.category import (
    TweetCategory,
    parse_smoke_category,
    category_to_hint,
    is_local_test_tweet_id,
)
from twitter_intel.domain.entities.tweet import (
    TweetCandidate,
    PreparedReviewCandidate,
)
from twitter_intel.domain.entities.xss_output import (
    XSSCandidate,
    XSSSearchCycleOutput,
    create_search_cycle_output,
)

__all__ = [
    "TweetCategory",
    "parse_smoke_category",
    "category_to_hint",
    "is_local_test_tweet_id",
    "TweetCandidate",
    "PreparedReviewCandidate",
    # XSS Output Schema (SRS-YARA-XSS-2026 Section 6.1)
    "XSSCandidate",
    "XSSSearchCycleOutput",
    "create_search_cycle_output",
]
