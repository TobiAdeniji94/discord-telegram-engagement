"""
Smoke test use case.

Handles the !smoke command by generating synthetic test candidates
that exercise the Discord review workflow without hitting X APIs.
"""

import time
from datetime import datetime, timezone

from twitter_intel.domain.entities.category import TweetCategory, category_to_hint
from twitter_intel.domain.entities.tweet import TweetCandidate
from twitter_intel.domain.interfaces import NotificationService, TweetRepository


# Smoke test variants for each category
_SMOKE_VARIANTS = {
    TweetCategory.BRAND_MENTION: {
        "text": (
            "Smoke test: synthetic brand mention. Use this to verify the "
            "brand review lane, approve flow, and custom replies."
        ),
        "author_username": "yara_brand_smoke",
        "author_name": "Yara Brand Smoke",
        "search_query": "smoke-test:brand-mentions",
        "category_hint": "brand_mention",
        "sentiment": "neutral",
        "themes": ["smoke-test", "brand"],
        "urgency": "low",
        "competitor_mentioned": None,
        "yara_angle": "Synthetic brand mention for validating the default review channel.",
        "replies": [
            {
                "tone": "helpful",
                "text": "Smoke-test brand reply 1: approve this to verify the default flow.",
                "strategy": "Exercises the primary approval button.",
            },
            {
                "tone": "direct",
                "text": "Smoke-test brand reply 2: use this to verify alternate reply choices.",
                "strategy": "Exercises a secondary approval button.",
            },
            {
                "tone": "custom",
                "text": "Smoke-test brand reply 3: or use !reply for a manual response.",
                "strategy": "Exercises the custom reply instructions.",
            },
        ],
    },
    TweetCategory.COMPETITOR_COMPLAINT: {
        "text": (
            "Smoke test: synthetic competitor complaint about slow transfers and high fees. "
            "Use this to verify the competitor review lane."
        ),
        "author_username": "competitor_smoke",
        "author_name": "Competitor Smoke",
        "search_query": "smoke-test:competitor-complaints",
        "category_hint": "competitor_complaint",
        "sentiment": "negative",
        "themes": ["smoke-test", "competitor"],
        "urgency": "medium",
        "competitor_mentioned": "ExamplePay",
        "yara_angle": "Synthetic competitor complaint for testing the competitor channel.",
        "replies": [
            {
                "tone": "empathetic",
                "text": "Smoke-test competitor reply 1: acknowledge the pain, then point to a smoother option.",
                "strategy": "Tests the competitor complaint approval path.",
            },
            {
                "tone": "practical",
                "text": "Smoke-test competitor reply 2: offer a lower-friction alternative without bashing anyone.",
                "strategy": "Tests a second competitor-style suggestion.",
            },
            {
                "tone": "question",
                "text": "Smoke-test competitor reply 3: invite them to compare a more reliable transfer flow.",
                "strategy": "Tests a softer CTA.",
            },
        ],
    },
    TweetCategory.SOLUTION_SEEKER: {
        "text": (
            "Smoke test: synthetic solution-seeker asking for the best way to receive USD in Nigeria. "
            "Use this to verify the solution-seekers lane."
        ),
        "author_username": "seeker_smoke",
        "author_name": "Solution Seeker Smoke",
        "search_query": "smoke-test:solution-seekers",
        "category_hint": "solution_seeker",
        "sentiment": "neutral",
        "themes": ["smoke-test", "solution-seeker"],
        "urgency": "high",
        "competitor_mentioned": None,
        "yara_angle": "Synthetic demand-capture lead for testing the solution-seekers channel.",
        "replies": [
            {
                "tone": "helpful",
                "text": "Smoke-test seeker reply 1: answer the use case directly and mention the relevant feature.",
                "strategy": "Tests a feature-led reply in the seekers lane.",
            },
            {
                "tone": "concise",
                "text": "Smoke-test seeker reply 2: position Yara.cash as a practical option for this need.",
                "strategy": "Tests a shorter solution-oriented response.",
            },
            {
                "tone": "clarifying",
                "text": "Smoke-test seeker reply 3: ask a qualifying question before recommending the best path.",
                "strategy": "Tests a consultative response pattern.",
            },
        ],
    },
}


def build_smoke_test_payload(
    category: TweetCategory = TweetCategory.BRAND_MENTION,
) -> tuple[TweetCandidate, dict]:
    """
    Build a synthetic candidate that exercises the Discord review workflow.

    Args:
        category: The category to generate a smoke test for

    Returns:
        Tuple of (TweetCandidate, analysis dict)
    """
    now = datetime.now(timezone.utc)
    tweet_id = f"smoke-{int(time.time() * 1000)}"
    variant = _SMOKE_VARIANTS[category]

    tweet = TweetCandidate(
        tweet_id=tweet_id,
        text=variant["text"],
        author_username=variant["author_username"],
        author_name=variant["author_name"],
        author_followers=0,
        url="https://example.com/smoke-test",
        created_at=now,
        likes=12,
        retweets=3,
        replies=5,
        quotes=1,
        views=248,
        age_minutes=0,
        source_tab="Smoke",
        search_query=variant["search_query"],
        category_hint=variant["category_hint"],
    )

    analysis = {
        "category": category.value,
        "sentiment": variant["sentiment"],
        "confidence": 1.0,
        "themes": variant["themes"],
        "urgency": variant["urgency"],
        "competitor_mentioned": variant["competitor_mentioned"],
        "yara_angle": variant["yara_angle"],
        "replies": variant["replies"],
    }

    return tweet, analysis


class SmokeTestUseCase:
    """
    Handle smoke test command execution.

    Creates synthetic test candidates to verify the Discord review
    workflow without requiring real X/Twitter data.
    """

    def __init__(
        self,
        repository: TweetRepository,
        notification_service: NotificationService,
    ):
        self._repository = repository
        self._notification_service = notification_service

    async def execute(self, category: TweetCategory) -> tuple[bool, str]:
        """
        Execute a smoke test for the given category.

        Args:
            category: The TweetCategory to test

        Returns:
            Tuple of (success, message)
        """
        tweet, analysis = build_smoke_test_payload(category)

        # Queue for review (bypasses search/AI)
        success = await self._queue_for_review(tweet, analysis)

        if not success:
            self._repository.mark_rejected(tweet.tweet_id)
            return False, "Could not queue the smoke test. Check the review channels."

        return True, (
            f"Smoke test queued in `{category.value}` as `{tweet.tweet_id}`. "
            "Approve is safe: smoke items always dry-run instead of posting to X."
        )

    async def _queue_for_review(
        self, tweet: TweetCandidate, analysis: dict
    ) -> bool:
        """
        Queue a candidate for Discord review.

        Args:
            tweet: The tweet candidate
            analysis: AI analysis dict

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
