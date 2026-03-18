"""
Tests for xAI live search prompt construction.
"""

from twitter_intel.config import Config, SearchJob, SearchQuery
from twitter_intel.infrastructure.search.xai_live_search import (
    build_xai_search_prompt,
)


class TestBuildXaiSearchPrompt:
    """Tests for rolling freshness instructions in xAI prompts."""

    def test_includes_max_age_freshness_window(self):
        """Prompt should tell xAI to enforce the rolling max-age window."""
        config = Config(
            brand_context="Test brand context",
            max_tweet_age_minutes=60,
            max_discord_approvals_per_scan=2,
            num_reply_options=4,
            search_since_days=0,
        )
        job = SearchJob(
            query=SearchQuery(
                query="test query",
                category_hint="solution_seeker",
                description="Test lane",
                query_type="Latest",
            ),
            query_type="Latest",
        )

        prompt = build_xai_search_prompt(config, job)

        assert "within the last 60 minutes" in prompt
        assert 'return {"candidates": []}' in prompt
        assert "Current UTC time is" in prompt
