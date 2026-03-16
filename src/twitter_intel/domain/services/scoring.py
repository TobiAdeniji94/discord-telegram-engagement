"""
Scoring service for tweet candidates.

Provides scoring and filtering logic for tweet candidates.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from twitter_intel.domain.entities.tweet import TweetCandidate


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

    # Sort by score descending
    scored.sort(key=lambda t: t.local_score, reverse=True)

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
