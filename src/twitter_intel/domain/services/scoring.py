"""
Scoring service for tweet candidates.

Provides scoring and filtering logic for tweet candidates.
Implements SRS-YARA-XSS-2026 Section 4.4.2 Scoring Rubric.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from twitter_intel.domain.entities.tweet import TweetCandidate

from twitter_intel.config.brand_registry import (
    BrandConfig,
    DEFAULT_SCORING_WEIGHTS,
    FIRST_PERSON_TERMS,
    FRUSTRATION_TERMS,
    ISSUE_TERMS,
    RECOVERY_TERMS,
    ScoringWeights,
    get_brand,
)


@dataclass
class ScoringResult:
    """
    Result of scoring a candidate per SRS Section 4.4.2.

    Provides detailed breakdown of score components for debugging
    and audit purposes.
    """
    total_score: int
    issue_keyword_points: int = 0
    first_person_points: int = 0
    brand_named_points: int = 0
    recovery_timing_points: int = 0
    frustration_points: int = 0
    official_account_penalty: int = 0
    vague_post_penalty: int = 0
    reason: str = ""

    @property
    def passes_threshold(self) -> bool:
        """Check if score meets minimum threshold (default: 5)."""
        return self.total_score >= DEFAULT_SCORING_WEIGHTS.minimum_score


def score_candidate_xss(
    tweet_text: str,
    author_username: str,
    brand_config: Optional[BrandConfig] = None,
    brand_key: Optional[str] = None,
    weights: ScoringWeights = DEFAULT_SCORING_WEIGHTS,
) -> ScoringResult:
    """
    Score a candidate tweet using SRS Section 4.4.2 rubric.

    This implements the exact scoring criteria from the SRS:
    - Concrete issue keyword present: +3
    - First-person language: +2
    - Brand clearly named: +2
    - Post-recovery timing language: +2
    - Frustration or urgency indicators: +1
    - Official brand account: -3
    - Vague/generic post: -2

    Args:
        tweet_text: The full text of the tweet
        author_username: The tweet author's handle (without @)
        brand_config: Optional brand configuration for brand-specific checks
        brand_key: Optional brand key to look up brand_config
        weights: Scoring weights (defaults to SRS-specified values)

    Returns:
        ScoringResult with total score and component breakdown
    """
    # Resolve brand config if only key provided
    if brand_config is None and brand_key:
        brand_config = get_brand(brand_key)

    text_lower = tweet_text.lower()
    author_lower = author_username.lower().lstrip("@")

    result = ScoringResult(total_score=0)
    reasons: list[str] = []

    # Check for concrete issue keywords (+3)
    if _has_any_term(text_lower, ISSUE_TERMS):
        result.issue_keyword_points = weights.issue_keyword_present
        reasons.append("issue_keyword")

    # Check for first-person language (+2)
    if _has_first_person(text_lower):
        result.first_person_points = weights.first_person_language
        reasons.append("first_person")

    # Check if brand is clearly named (+2)
    if brand_config and _has_brand_name(text_lower, brand_config):
        result.brand_named_points = weights.brand_clearly_named
        reasons.append("brand_named")

    # Check for recovery/timing language (+2)
    if _has_any_term(text_lower, RECOVERY_TERMS):
        result.recovery_timing_points = weights.recovery_timing_language
        reasons.append("recovery_timing")

    # Check for frustration/urgency indicators (+1)
    if _has_any_term(text_lower, FRUSTRATION_TERMS):
        result.frustration_points = weights.frustration_urgency
        reasons.append("frustration")

    # Check if from official brand account (-3)
    if brand_config and author_lower in brand_config.get_excluded_handles_set():
        result.official_account_penalty = weights.official_brand_account
        reasons.append("official_account")

    # Check if vague/generic with no specific issue (-2)
    if not result.issue_keyword_points and len(tweet_text) < 100:
        result.vague_post_penalty = weights.vague_generic_post
        reasons.append("vague_post")

    # Calculate total score
    result.total_score = (
        result.issue_keyword_points
        + result.first_person_points
        + result.brand_named_points
        + result.recovery_timing_points
        + result.frustration_points
        + result.official_account_penalty  # Already negative
        + result.vague_post_penalty  # Already negative
    )

    result.reason = "+".join(reasons) if reasons else "no_signals"
    return result


def _has_any_term(text: str, terms: tuple[str, ...]) -> bool:
    """Check if text contains any of the given terms (word boundary aware)."""
    for term in terms:
        # Use word boundary matching for single words
        if " " not in term:
            pattern = rf"\b{re.escape(term)}\b"
            if re.search(pattern, text, re.IGNORECASE):
                return True
        elif term in text:
            return True
    return False


def _has_first_person(text: str) -> bool:
    """Check for first-person language with proper word boundaries."""
    for term in FIRST_PERSON_TERMS:
        pattern = rf"\b{re.escape(term)}\b"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _has_brand_name(text: str, brand_config: BrandConfig) -> bool:
    """Check if brand name or alias appears in text."""
    for alias in brand_config.aliases:
        if alias.lower() in text:
            return True
    # Also check for @handle mentions
    for handle in brand_config.handles:
        if f"@{handle.lower()}" in text or handle.lower() in text:
            return True
    return False


def score_candidate(tweet: "TweetCandidate") -> float:
    """
    Calculate an engagement score for a tweet candidate.

    The score is based on:
    - Engagement metrics (replies, likes, retweets, quotes, views)
    - Author follower count
    - Category hint bonus
    - Age penalty for older tweets

    Args:
        tweet: The tweet candidate to score

    Returns:
        Calculated score (higher is better)
    """
    # Base engagement score
    score = (
        tweet.replies * 4.0       # Replies weighted highest
        + tweet.likes * 1.5
        + tweet.retweets * 2.0
        + tweet.quotes * 2.0
        + min(tweet.views / 500.0, 8.0)         # Capped at 8.0
        + min(tweet.author_followers / 5000.0, 4.0)  # Capped at 4.0
    )

    # Category bonus
    if tweet.category_hint == "solution_seeker":
        score += 3.0
    elif tweet.category_hint == "competitor_complaint":
        score += 2.0
    elif tweet.category_hint == "brand_mention":
        score += 1.0

    # Age penalty
    if tweet.age_minutes > 360:
        score -= 4.0
    elif tweet.age_minutes > 120:
        score -= 2.0

    return score


def get_score_threshold(tweet: "TweetCandidate") -> float | None:
    """
    Get the minimum score threshold for a tweet category.

    Direct mentions have no threshold (always considered).

    Args:
        tweet: The tweet candidate

    Returns:
        Minimum score required, or None if no threshold applies
    """
    # Direct mentions always pass
    if tweet.category_hint == "brand_mention" and tweet.is_direct_mention:
        return None

    thresholds = {
        "competitor_complaint": 12.0,
        "solution_seeker": 11.0,
        "brand_mention": 6.0,
    }
    return thresholds.get(tweet.category_hint, 10.0)


def passes_score_threshold(tweet: "TweetCandidate") -> bool:
    """
    Check if a tweet passes its category's score threshold.

    Args:
        tweet: The tweet candidate (must have local_score set)

    Returns:
        True if the tweet passes the threshold
    """
    threshold = get_score_threshold(tweet)
    if threshold is None:
        return True
    return tweet.local_score >= threshold


def filter_candidates(
    candidates: list["TweetCandidate"],
    max_tweet_age_minutes: int,
    processed_ids: set[str],
) -> tuple[list["TweetCandidate"], list[tuple[str, float, str]]]:
    """
    Filter and score a list of tweet candidates.

    Args:
        candidates: Raw list of tweet candidates
        max_tweet_age_minutes: Maximum age for tweets
        processed_ids: Set of already processed tweet IDs

    Returns:
        Tuple of (filtered_candidates, discarded_list)
        where discarded_list contains (tweet_id, score, reason) tuples
    """
    scored: list["TweetCandidate"] = []
    discarded: list[tuple[str, float, str]] = []
    seen_ids: set[str] = set()

    for tweet in candidates:
        # Calculate score
        tweet.local_score = score_candidate(tweet)

        # Check for duplicates
        if tweet.tweet_id in seen_ids:
            discarded.append((tweet.tweet_id, tweet.local_score, "duplicate_in_scan"))
            continue

        if tweet.tweet_id in processed_ids:
            discarded.append((tweet.tweet_id, tweet.local_score, "already_processed"))
            continue

        seen_ids.add(tweet.tweet_id)

        # Check age
        if tweet.age_minutes > max_tweet_age_minutes:
            discarded.append((tweet.tweet_id, tweet.local_score, "too_old"))
            continue

        # Check score threshold
        if not passes_score_threshold(tweet):
            discarded.append((tweet.tweet_id, tweet.local_score, "below_threshold"))
            continue

        scored.append(tweet)

    # Sort by score descending, then most recent first among equal scores.
    scored.sort(key=lambda t: (t.local_score, t.created_at), reverse=True)

    return scored, discarded


def format_discarded_candidates(
    discarded: list[tuple[str, float, str]],
    limit: int = 3,
) -> list[str]:
    """
    Format discarded candidates for logging.

    Args:
        discarded: List of (tweet_id, score, reason) tuples
        limit: Maximum number of candidates to include

    Returns:
        List of formatted strings
    """
    sorted_discarded = sorted(discarded, key=lambda x: x[1], reverse=True)
    return [
        f"{tid[:8]}... ({score:.1f}, {reason})"
        for tid, score, reason in sorted_discarded[:limit]
    ]
