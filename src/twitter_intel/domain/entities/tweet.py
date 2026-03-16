"""
Tweet entity definitions.

Contains the core domain objects representing tweets and review candidates.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class TweetCandidate:
    """
    Represents a tweet candidate for engagement.

    This is the core domain entity containing all information about a tweet
    that may be reviewed and responded to.

    Attributes:
        tweet_id: Unique identifier for the tweet
        text: The tweet's text content
        author_username: The author's X/Twitter username (without @)
        author_name: The author's display name
        author_followers: Number of followers the author has
        url: Direct URL to the tweet
        created_at: When the tweet was posted
        likes: Number of likes on the tweet
        retweets: Number of retweets
        replies: Number of replies
        quotes: Number of quote tweets
        views: Number of views (impressions)
        age_minutes: How old the tweet is in minutes
        source_tab: Which search tab this came from ("Top" or "Latest")
        search_query: The query that found this tweet
        category_hint: Suggested category based on search query
        is_direct_mention: Whether this is a direct @mention
        local_score: Calculated engagement score
    """
    tweet_id: str
    text: str
    author_username: str
    author_name: str
    author_followers: int
    url: str
    created_at: datetime
    likes: int
    retweets: int
    replies: int
    quotes: int
    views: int
    age_minutes: float
    source_tab: str
    search_query: str
    category_hint: str
    is_direct_mention: bool = False
    local_score: float = 0.0

    @property
    def engagement_total(self) -> int:
        """Total engagement (likes + retweets + replies + quotes)."""
        return self.likes + self.retweets + self.replies + self.quotes

    @property
    def is_test_tweet(self) -> bool:
        """Check if this is a locally-generated test tweet."""
        return self.tweet_id.startswith(("smoke-", "manual-"))


@dataclass
class PreparedReviewCandidate:
    """
    A tweet candidate that has been prepared for human review.

    Contains the original tweet along with AI analysis results
    and metadata about how it was discovered.

    Attributes:
        tweet: The original TweetCandidate
        analysis: AI classification results including category, sentiment, and reply options
        provider: Which search provider found this tweet
        source_query: The specific query that found this tweet
    """
    tweet: TweetCandidate
    analysis: dict[str, Any]
    provider: str
    source_query: str

    @property
    def category(self) -> str:
        """Get the classified category from analysis."""
        return self.analysis.get("category", "irrelevant")

    @property
    def sentiment(self) -> str:
        """Get the detected sentiment from analysis."""
        return self.analysis.get("sentiment", "neutral")

    @property
    def reply_options(self) -> list[dict[str, Any]]:
        """Get the generated reply options from analysis."""
        return self.analysis.get("replies", [])

    @property
    def confidence(self) -> float:
        """Get the classification confidence score."""
        return self.analysis.get("confidence", 0.0)
