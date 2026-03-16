"""
Manual ingest use case.

Handles the !ingest command by creating candidates from user-provided
tweet text, bypassing the normal search/scrape flow.
"""

import time
from datetime import datetime, timezone

from twitter_intel.domain.entities.category import TweetCategory, category_to_hint
from twitter_intel.domain.entities.tweet import TweetCandidate
from twitter_intel.domain.interfaces import NotificationService, TweetRepository


# Manual ingest analysis variants for each category
_MANUAL_VARIANTS = {
    TweetCategory.BRAND_MENTION: {
        "sentiment": "neutral",
        "themes": ["manual-ingest", "brand"],
        "urgency": "low",
        "competitor_mentioned": None,
        "yara_angle": "Manual brand-mention candidate injected without relying on X search.",
    },
    TweetCategory.COMPETITOR_COMPLAINT: {
        "sentiment": "negative",
        "themes": ["manual-ingest", "competitor"],
        "urgency": "medium",
        "competitor_mentioned": "manual-test",
        "yara_angle": "Manual competitor-complaint candidate injected without twscrape.",
    },
    TweetCategory.SOLUTION_SEEKER: {
        "sentiment": "neutral",
        "themes": ["manual-ingest", "solution-seeker"],
        "urgency": "high",
        "competitor_mentioned": None,
        "yara_angle": "Manual solution-seeker candidate injected without twscrape.",
    },
}


def build_manual_candidate(category: TweetCategory, text: str) -> TweetCandidate:
    """
    Build a TweetCandidate from manually-provided text.

    Args:
        category: The category to assign
        text: The tweet text provided by the user

    Returns:
        A TweetCandidate with a manual- prefixed ID
    """
    now = datetime.now(timezone.utc)
    return TweetCandidate(
        tweet_id=f"manual-{int(time.time() * 1000)}",
        text=text.strip(),
        author_username="manual_ingest",
        author_name="Manual Ingest",
        author_followers=0,
        url="https://example.com/manual-ingest",
        created_at=now,
        likes=0,
        retweets=0,
        replies=0,
        quotes=0,
        views=0,
        age_minutes=0,
        source_tab="Manual",
        search_query=f"manual-ingest:{category.value}",
        category_hint=category_to_hint(category),
    )


def build_manual_ingest_analysis(category: TweetCategory, text: str) -> dict:
    """
    Build an analysis dict for a manually-ingested tweet.

    Args:
        category: The category to assign
        text: The tweet text

    Returns:
        Analysis dict with category, sentiment, and reply options
    """
    snippet = text.strip().replace("\n", " ")
    if len(snippet) > 120:
        snippet = snippet[:117] + "..."

    variant = _MANUAL_VARIANTS[category]

    replies = _build_manual_replies(category, snippet)

    return {
        "category": category.value,
        "sentiment": variant["sentiment"],
        "confidence": 1.0,
        "themes": variant["themes"],
        "urgency": variant["urgency"],
        "competitor_mentioned": variant["competitor_mentioned"],
        "yara_angle": variant["yara_angle"],
        "replies": replies,
    }


def _build_manual_replies(category: TweetCategory, snippet: str) -> list[dict]:
    """Build reply options based on category and text snippet."""
    if category == TweetCategory.BRAND_MENTION:
        return [
            {
                "tone": "friendly",
                "text": f'Manual test reply 1: thanks for mentioning this. Context noted: "{snippet}"',
                "strategy": "Verifies the default brand lane with user-provided text.",
            },
            {
                "tone": "concise",
                "text": f"Manual test reply 2: this routes brand mentions without waiting on X search. ({snippet})",
                "strategy": "Verifies an alternate approval option.",
            },
        ]
    elif category == TweetCategory.COMPETITOR_COMPLAINT:
        return [
            {
                "tone": "empathetic",
                "text": f'Manual test reply 1: that pain point is clear. Logged context: "{snippet}"',
                "strategy": "Verifies competitor complaint routing.",
            },
            {
                "tone": "practical",
                "text": f"Manual test reply 2: this is a manual fallback lead, queued without X search. ({snippet})",
                "strategy": "Verifies a second competitor-style suggestion.",
            },
        ]
    else:  # SOLUTION_SEEKER
        return [
            {
                "tone": "helpful",
                "text": f'Manual test reply 1: this use case is queued for review. Input: "{snippet}"',
                "strategy": "Verifies the solution-seekers lane.",
            },
            {
                "tone": "direct",
                "text": f"Manual test reply 2: this candidate bypassed X search so the workflow can still be tested. ({snippet})",
                "strategy": "Verifies a shorter solution-led option.",
            },
        ]


class ManualIngestUseCase:
    """
    Handle manual ingest command execution.

    Creates candidates from user-provided text to test the workflow
    when X search is unavailable or for specific testing scenarios.
    """

    def __init__(
        self,
        repository: TweetRepository,
        notification_service: NotificationService,
    ):
        self._repository = repository
        self._notification_service = notification_service

    async def execute(self, category: TweetCategory, text: str) -> tuple[bool, str]:
        """
        Execute a manual ingest for the given category and text.

        Args:
            category: The TweetCategory to assign
            text: The tweet text to use

        Returns:
            Tuple of (success, message)
        """
        tweet = build_manual_candidate(category, text)
        analysis = build_manual_ingest_analysis(category, text)

        # Queue for review (bypasses search/AI)
        success = await self._queue_for_review(tweet, analysis)

        if not success:
            self._repository.mark_rejected(tweet.tweet_id)
            return False, "Could not queue the manual ingest item. Check the review channels."

        return True, (
            f"Manual ingest queued in `{category.value}` as `{tweet.tweet_id}`. "
            "Approve is safe: manual items dry-run instead of posting to X."
        )

    async def _queue_for_review(
        self, tweet: TweetCandidate, analysis: dict
    ) -> bool:
        """
        Queue a candidate for Discord review.

        Args:
            tweet: The tweet candidate
            analysis: Analysis dict

        Returns:
            True if successfully queued
        """
        category = analysis.get("category", "irrelevant")
        sentiment = analysis.get("sentiment", "neutral")

        if category == TweetCategory.IRRELEVANT.value:
            return False

        reply_texts = [r["text"] for r in analysis.get("replies", [])]

        # Mark as processed in database
        self._repository.mark_processed(
            tweet_id=tweet.tweet_id,
            url=tweet.url,
            text=tweet.text,
            author=tweet.author_username,
            category=category,
            sentiment=sentiment,
            search_query=tweet.search_query,
        )

        # Send to Discord for review
        result = await self._notification_service.send_approval(tweet, analysis)
        if not result:
            return False

        msg_id, ch_id = result
        self._repository.save_pending(
            tweet_id=tweet.tweet_id,
            replies=reply_texts,
            message_id=msg_id,
            channel_id=ch_id,
            category=category,
        )

        return True
