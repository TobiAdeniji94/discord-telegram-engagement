"""
Search query definitions for Twitter Intelligence Bot.

Defines the SearchQuery dataclass and default search queries for
monitoring competitor complaints, solution seekers, and brand mentions.
"""

from dataclasses import dataclass


@dataclass
class SearchQuery:
    """
    A search query configuration with category context.

    Attributes:
        query: Twitter/X search query string
        category_hint: Helps AI classify the intent (competitor_complaint, solution_seeker, brand_mention)
        description: Human-readable description of what this query targets
        query_type: "Top" for popular tweets, "Latest" for recent tweets
        cooldown_seconds: Minimum time between executions of this query
        max_pages: Maximum pages to fetch (1 page = ~20 tweets)
        enabled: Whether this query is active
    """
    query: str
    category_hint: str
    description: str
    query_type: str = "Top"
    cooldown_seconds: int = 3600
    max_pages: int = 1
    enabled: bool = True


# Default search queries organized by business intent
DEFAULT_SEARCH_QUERIES: list[SearchQuery] = [
    SearchQuery(
        query=(
            '("chipper cash" OR "lemfi" OR "grey.co" OR "sendwave" OR "wise" OR "remitly") '
            '(down OR failed OR issue OR complaint OR slow OR fees) '
            "lang:en -filter:retweets -filter:replies min_faves:3"
        ),
        category_hint="competitor_complaint",
        description="High-signal competitor complaints",
        cooldown_seconds=3600,
    ),
    SearchQuery(
        query=(
            '("send money" OR "receive USD" OR "dollar card" OR "best app") '
            '(Nigeria OR Africa) '
            '(need OR looking OR best OR cheapest) '
            "lang:en -filter:retweets -filter:replies min_faves:2"
        ),
        category_hint="solution_seeker",
        description="People asking for the exact problems Yara solves",
        cooldown_seconds=2700,
    ),
    SearchQuery(
        query='("yara.cash" OR "yara cash") lang:en -filter:retweets',
        category_hint="brand_mention",
        description="Fallback brand keyword mentions when direct mentions are unavailable",
        cooldown_seconds=900,
    ),
]
