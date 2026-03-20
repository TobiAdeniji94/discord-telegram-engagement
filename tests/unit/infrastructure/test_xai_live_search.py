"""
Tests for xAI live search prompt construction and lane scheduling.
"""

from datetime import datetime, timezone

from twitter_intel.config import Config, SearchJob, SearchQuery, SearchRuntime
from twitter_intel.infrastructure.search.xai_live_search import (
    build_manual_grok_prompt,
    build_xai_search_prompt,
    select_due_queries,
)


class TestBuildXaiSearchPrompt:
    """Tests for structured xAI prompts."""

    def test_includes_structured_lane_context(self):
        config = Config(
            brand_context="Test brand context",
            max_tweet_age_minutes=60,
            max_discord_approvals_per_scan=2,
            num_reply_options=4,
            search_since_days=0,
        )
        job = SearchJob(
            query=SearchQuery(
                query="Find Chipper complaints",
                category_hint="competitor_complaint",
                description="Chipper complaints",
                query_type="Latest",
                lane_id="complaint-chipper",
                intent_summary="Find complaints about Chipper from real users.",
                brand_family="chipper",
                brand_aliases=["Chipper", "Chipper Cash"],
                brand_handles=["chippercashapp"],
                issue_focus=["pending transfers", "support issues"],
                geo_focus=["Nigeria", "Africa"],
                exclude_author_handles=["chippercashapp"],
            ),
            query_type="Latest",
        )

        prompt = build_xai_search_prompt(config, job)

        assert "Lane ID: complaint-chipper" in prompt
        assert "Brand aliases to consider: Chipper, Chipper Cash" in prompt
        assert "@chippercashapp" in prompt
        assert "Current UTC time is" in prompt
        assert 'return {"candidates": []}' in prompt
        assert "Search semantically" in prompt

    def test_includes_anchored_event_window_when_active(self):
        config = Config(
            brand_context="Test brand context",
            max_tweet_age_minutes=60,
            max_discord_approvals_per_scan=2,
            num_reply_options=4,
            search_since_days=0,
            search_event_mode="anchored",
            search_event_anchor_utc=datetime(2026, 3, 19, 8, 0, tzinfo=timezone.utc),
            search_event_min_offset_minutes=30,
            search_event_max_offset_minutes=360,
            search_event_brands=["chipper"],
        )
        job = SearchJob(
            query=SearchQuery(
                query="Find Chipper complaints",
                category_hint="competitor_complaint",
                description="Chipper complaints",
                query_type="Latest",
                lane_id="complaint-chipper",
                intent_summary="Find complaints about Chipper from real users.",
                brand_family="chipper",
            ),
            query_type="Latest",
        )

        prompt = build_xai_search_prompt(config, job)

        assert "Anchored-event window is active for this lane." in prompt
        assert "2026-03-19T08:30:00Z" in prompt
        assert "2026-03-19T14:00:00Z" in prompt

    def test_manual_prompt_uses_same_lane_data(self):
        config = Config(max_tweet_age_minutes=60, search_since_days=0)
        job = SearchJob(
            query=SearchQuery(
                query="Find Wise complaints",
                category_hint="competitor_complaint",
                description="Wise complaints",
                query_type="Latest",
                lane_id="complaint-wise",
                intent_summary="Find complaints about Wise from real users.",
                brand_family="wise",
                brand_aliases=["Wise", "TransferWise"],
                brand_handles=["Wise"],
            ),
            query_type="Latest",
        )

        prompt = build_manual_grok_prompt(config, job)

        assert "complaint-wise" in prompt
        assert "Wise, TransferWise" in prompt
        assert "@Wise" in prompt
        assert "Return strict JSON with a candidates array." in prompt

    def test_prompt_uses_restart_catchup_window_when_active(self):
        config = Config(
            brand_context="Test brand context",
            max_tweet_age_minutes=60,
            max_discord_approvals_per_scan=2,
            num_reply_options=4,
            search_since_days=1,
        )
        runtime = SearchRuntime(
            restart_catchup_start_utc=datetime(2026, 3, 19, 8, 0, tzinfo=timezone.utc),
            restart_catchup_end_utc=datetime(2026, 3, 19, 9, 0, tzinfo=timezone.utc),
        )
        job = SearchJob(
            query=SearchQuery(
                query="Find Grey complaints",
                category_hint="competitor_complaint",
                description="Grey complaints",
                query_type="Latest",
                lane_id="complaint-grey",
                intent_summary="Find complaints about Grey from real users.",
                brand_family="grey",
            ),
            query_type="Latest",
        )

        prompt = build_xai_search_prompt(config, job, runtime)

        assert "Restart catch-up is active for this scan." in prompt
        assert "2026-03-19T08:00:00Z" in prompt
        assert "2026-03-19T09:00:00Z" in prompt


class TestSelectDueQueries:
    """Tests for lane scheduling and event-mode filtering."""

    def test_prefers_competitor_lanes_before_seekers(self):
        config = Config(
            search_queries=[
                SearchQuery(
                    query="seekers",
                    category_hint="solution_seeker",
                    description="Seekers",
                    lane_id="seekers",
                    intent_summary="Seekers",
                    priority=30,
                    cooldown_seconds=60,
                ),
                SearchQuery(
                    query="complaints",
                    category_hint="competitor_complaint",
                    description="Complaints",
                    lane_id="complaints",
                    intent_summary="Complaints",
                    brand_family="chipper",
                    priority=10,
                    cooldown_seconds=60,
                ),
            ]
        )
        runtime = SearchRuntime()

        jobs = select_due_queries(config, runtime, request_budget=2)

        assert [job.query.category_hint for job in jobs] == [
            "competitor_complaint",
            "solution_seeker",
        ]

    def test_anchored_mode_runs_only_selected_brand_families(self):
        config = Config(
            search_event_mode="anchored",
            search_event_anchor_utc=datetime(2026, 3, 19, 8, 0, tzinfo=timezone.utc),
            search_event_min_offset_minutes=30,
            search_event_max_offset_minutes=360,
            search_event_brands=["grey"],
            search_queries=[
                SearchQuery(
                    query="grey complaints",
                    category_hint="competitor_complaint",
                    description="Grey complaints",
                    lane_id="complaint-grey",
                    intent_summary="Grey complaints",
                    brand_family="grey",
                    cooldown_seconds=60,
                ),
                SearchQuery(
                    query="chipper complaints",
                    category_hint="competitor_complaint",
                    description="Chipper complaints",
                    lane_id="complaint-chipper",
                    intent_summary="Chipper complaints",
                    brand_family="chipper",
                    cooldown_seconds=60,
                ),
                SearchQuery(
                    query="seekers",
                    category_hint="solution_seeker",
                    description="Seekers",
                    lane_id="seekers",
                    intent_summary="Seekers",
                    cooldown_seconds=60,
                ),
            ],
        )
        runtime = SearchRuntime()

        jobs = select_due_queries(config, runtime, request_budget=8)

        assert [job.query.brand_family for job in jobs] == ["grey"]
