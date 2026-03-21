"""
XSS Candidate Output Schema.

Implements SRS-YARA-XSS-2026 Section 6.1 Candidate Output Schema.
Defines structured JSON payloads for search cycle results.
"""

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Any


@dataclass
class XSSCandidate:
    """
    Single candidate per SRS Section 6.1.

    Attributes:
        tweet_url: Direct URL to the X post (https://x.com/.../status/...)
        tweet_text: Full text content of the tweet
        author_username: Author's X handle (without @)
        created_at_iso: ISO 8601 datetime when posted
        category: competitor_complaint | solution_seeker
        score: Numeric relevance score per Section 4.4.2
        reason: Scoring breakdown explanation
    """
    tweet_url: str
    tweet_text: str
    author_username: str
    created_at_iso: str
    category: str
    score: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class XSSSearchCycleOutput:
    """
    Search cycle output per SRS Section 6.1 Candidate Output Schema.

    This is the structured JSON payload emitted by the XSS for each
    search cycle, consumable by downstream reply-authoring and CRM systems.

    Attributes:
        search_cycle_id: UUID for this search cycle
        search_timestamp_utc: ISO 8601 datetime of search execution
        lane: competitor_complaint | solution_seeker
        brand_key: Brand identifier (null for solution_seeker lane)
        restart_time_utc: Server restart timestamp (if applicable)
        filter_lower_bound: Time window lower bound
        filter_upper_bound: Time window upper bound
        raw_result_count: Total results before filtering
        filtered_result_count: Results after filtering
        candidates: List of XSSCandidate objects
    """
    search_cycle_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    search_timestamp_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    lane: str = ""
    brand_key: Optional[str] = None
    restart_time_utc: Optional[str] = None
    filter_lower_bound: Optional[str] = None
    filter_upper_bound: Optional[str] = None
    raw_result_count: int = 0
    filtered_result_count: int = 0
    candidates: list[XSSCandidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "search_cycle_id": self.search_cycle_id,
            "search_timestamp_utc": self.search_timestamp_utc,
            "lane": self.lane,
            "brand_key": self.brand_key,
            "restart_time_utc": self.restart_time_utc,
            "filter_lower_bound": self.filter_lower_bound,
            "filter_upper_bound": self.filter_upper_bound,
            "raw_result_count": self.raw_result_count,
            "filtered_result_count": self.filtered_result_count,
            "candidates": [c.to_dict() for c in self.candidates],
        }

    def add_candidate(
        self,
        tweet_url: str,
        tweet_text: str,
        author_username: str,
        created_at: datetime,
        category: str,
        score: int,
        reason: str,
    ) -> XSSCandidate:
        """
        Add a candidate to this search cycle output.

        Args:
            tweet_url: Direct URL to the X post
            tweet_text: Full text of the tweet
            author_username: Author handle (without @)
            created_at: When the tweet was posted
            category: Candidate category
            score: Relevance score
            reason: Scoring breakdown

        Returns:
            The created XSSCandidate
        """
        candidate = XSSCandidate(
            tweet_url=tweet_url,
            tweet_text=tweet_text,
            author_username=author_username,
            created_at_iso=created_at.isoformat() if created_at else "",
            category=category,
            score=score,
            reason=reason,
        )
        self.candidates.append(candidate)
        return candidate


def create_search_cycle_output(
    lane: str,
    brand_key: Optional[str] = None,
    restart_time_utc: Optional[datetime] = None,
    filter_lower_bound: Optional[datetime] = None,
    filter_upper_bound: Optional[datetime] = None,
) -> XSSSearchCycleOutput:
    """
    Create a new search cycle output container.

    Args:
        lane: Search lane (competitor_complaint or solution_seeker)
        brand_key: Brand key for complaint lanes
        restart_time_utc: Server restart timestamp
        filter_lower_bound: Time window lower bound
        filter_upper_bound: Time window upper bound

    Returns:
        New XSSSearchCycleOutput instance
    """
    return XSSSearchCycleOutput(
        lane=lane,
        brand_key=brand_key,
        restart_time_utc=restart_time_utc.isoformat() if restart_time_utc else None,
        filter_lower_bound=filter_lower_bound.isoformat() if filter_lower_bound else None,
        filter_upper_bound=filter_upper_bound.isoformat() if filter_upper_bound else None,
    )
