"""
Tests for xAI live search prompt construction and lane scheduling.
"""

import json
from datetime import datetime, timezone

from twitter_intel.config import Config, SearchJob, SearchQuery, SearchRuntime
from twitter_intel.infrastructure.search.xai_live_search import (
    build_xai_tool_config_for_job,
    build_manual_grok_prompt,
    build_xai_search_prompt,
    parse_xai_candidates,
    select_due_queries,
)


class TestBuildXaiSearchPrompt:
    """Tests for structured xAI prompts."""

    def test_complaint_prompt_is_srs_sized_and_semantic(self):
        config = Config(search_since_days=0, max_tweet_age_minutes=360)
        job = SearchJob(
            query=SearchQuery(
                query="Find Grey complaints",
                category_hint="competitor_complaint",
                description="Grey complaints",
                query_type="Latest",
                lane_id="complaint-grey",
                intent_summary="Find complaints about Grey from real users.",
                brand_family="grey",
                brand_aliases=["Grey", "greyfinance"],
                brand_handles=["greyfinance", "greyfinanceEA", "greyfinanceMENA"],
            ),
            query_type="Latest",
        )

        prompt = build_xai_search_prompt(config, job)

        assert len(prompt) <= 500
        assert "Grey" in prompt
        assert "last 6 hours" in prompt
        assert "the fintech/money transfer app" in prompt
        assert "full text" in prompt
        assert "author username" in prompt
        assert "timestamp" in prompt
        assert " OR " not in prompt
        assert "lang:" not in prompt

    def test_solution_prompt_mentions_persona_and_platforms(self):
        config = Config(search_since_days=0, max_tweet_age_minutes=360)
        job = SearchJob(
            query=SearchQuery(
                query="Find solution seekers",
                category_hint="solution_seeker",
                description="Solution seekers",
                query_type="Latest",
                lane_id="solution-seeker-usd-payments",
                intent_summary="Find solution seekers for USD payments.",
                geo_focus=["Nigeria", "Ghana", "Africa"],
            ),
            query_type="Latest",
        )

        prompt = build_xai_search_prompt(config, job)

        assert len(prompt) <= 500
        assert "freelancers" in prompt
        assert "last 6 hours" in prompt
        assert "Upwork/Fiverr" in prompt
        assert "Payoneer" in prompt
        assert "Wise" in prompt
        assert "Grey" in prompt
        assert "bot posts" in prompt

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

 
class TestBuildXaiToolConfig:
    """Tests for per-lane x_search tool config construction."""

    def test_complaint_lane_uses_brand_excluded_handles(self):
        config = Config(search_since_days=0)
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

        tool_config = build_xai_tool_config_for_job(config, job)

        assert tool_config["excluded_x_handles"] == ["chippercashapp"]
        assert "allowed_x_handles" not in tool_config
        assert tool_config["from_date"]
        assert tool_config["to_date"]

    def test_solution_lane_omits_excluded_handles(self):
        config = Config(search_since_days=0, xai_allowed_x_handles=["partner1"])
        job = SearchJob(
            query=SearchQuery(
                query="Find solution seekers",
                category_hint="solution_seeker",
                description="Solution seekers",
                query_type="Latest",
                lane_id="solution-seeker-usd-payments",
                intent_summary="Find solution seekers.",
            ),
            query_type="Latest",
        )

        tool_config = build_xai_tool_config_for_job(config, job)

        assert "excluded_x_handles" not in tool_config
        assert tool_config["allowed_x_handles"] == ["partner1"]
        assert tool_config["from_date"]
        assert tool_config["to_date"]

    def test_defaults_date_window_to_today(self):
        config = Config(search_since_days=None)
        job = SearchJob(
            query=SearchQuery(
                query="Find solution seekers",
                category_hint="solution_seeker",
                description="Solution seekers",
            ),
            query_type="Latest",
        )

        tool_config = build_xai_tool_config_for_job(config, job)

        assert tool_config["from_date"] == tool_config["to_date"]


class TestParseXaiCandidates:
    """Tests for xAI candidate parsing."""

    @staticmethod
    def _tweet_id_for_datetime(dt: datetime) -> str:
        twitter_epoch_ms = 1288834974657
        return str((int(dt.timestamp() * 1000) - twitter_epoch_ms) << 22)

    def test_accepts_srs_json_without_replies(self):
        job = SearchJob(
            query=SearchQuery(
                query="Find Grey complaints",
                category_hint="competitor_complaint",
                description="Grey complaints",
                brand_family="grey",
            ),
            query_type="Latest",
        )

        response_text = json.dumps(
            {
                "candidates": [
                    {
                        "tweet_url": "https://x.com/user/status/123",
                        "tweet_text": "My Grey transfer is still pending after 2 days.",
                        "author_username": "user",
                        "created_at_iso": "2026-03-20T12:00:00Z",
                        "category": "competitor_complaint",
                        "score": 7,
                        "reason": "issue_keyword+first_person",
                    }
                ]
            }
        )

        candidates = parse_xai_candidates({}, response_text, job)

        assert len(candidates) == 1
        assert candidates[0].tweet.tweet_id == "123"
        assert candidates[0].analysis["replies"] == []
        assert candidates[0].analysis["score"] == 7
        assert candidates[0].analysis["reason"] == "issue_keyword+first_person"

    def test_falls_back_to_text_response(self):
        job = SearchJob(
            query=SearchQuery(
                query="Find Grey complaints",
                category_hint="competitor_complaint",
                description="Grey complaints",
                brand_family="grey",
            ),
            query_type="Latest",
        )

        response_text = """
        URL: https://x.com/user/status/123
        Text: My Grey transfer is still pending after 2 days.
        Author: @user
        Timestamp: 2026-03-20T12:00:00Z
        """

        candidates = parse_xai_candidates({}, response_text, job)

        assert len(candidates) == 1
        assert candidates[0].tweet.author_username == "user"
        assert candidates[0].tweet.text == "My Grey transfer is still pending after 2 days."
        assert candidates[0].analysis["category"] == "competitor-complaints"

    def test_derives_created_at_from_realistic_tweet_id_when_timestamp_missing(self):
        job = SearchJob(
            query=SearchQuery(
                query="Find Grey complaints",
                category_hint="competitor_complaint",
                description="Grey complaints",
                brand_family="grey",
            ),
            query_type="Latest",
        )
        created_at = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)
        tweet_id = self._tweet_id_for_datetime(created_at)
        response_text = json.dumps(
            {
                "candidates": [
                    {
                        "tweet_url": f"https://x.com/user/status/{tweet_id}",
                        "tweet_text": "My Grey transfer is still pending after 2 days.",
                        "author_username": "user",
                        "category": "competitor_complaint",
                    }
                ]
            }
        )

        candidates = parse_xai_candidates({}, response_text, job)

        assert len(candidates) == 1
        assert candidates[0].tweet.tweet_id == tweet_id
        assert candidates[0].tweet.created_at == created_at

    def test_discards_candidate_without_created_at_iso(self):
        job = SearchJob(
            query=SearchQuery(
                query="Find Grey complaints",
                category_hint="competitor_complaint",
                description="Grey complaints",
                brand_family="grey",
            ),
            query_type="Latest",
        )

        response_text = json.dumps(
            {
                "candidates": [
                    {
                        "tweet_url": "https://x.com/user/status/123",
                        "tweet_text": "My Grey transfer is still pending after 2 days.",
                        "author_username": "user",
                        "category": "competitor_complaint",
                    }
                ]
            }
        )

        candidates = parse_xai_candidates({}, response_text, job)

        assert candidates == []

    def test_discards_candidate_with_invalid_created_at_iso(self):
        job = SearchJob(
            query=SearchQuery(
                query="Find Grey complaints",
                category_hint="competitor_complaint",
                description="Grey complaints",
                brand_family="grey",
            ),
            query_type="Latest",
        )

        response_text = json.dumps(
            {
                "candidates": [
                    {
                        "tweet_url": "https://x.com/user/status/123",
                        "tweet_text": "My Grey transfer is still pending after 2 days.",
                        "author_username": "user",
                        "created_at_iso": "not-a-real-timestamp",
                        "category": "competitor_complaint",
                    }
                ]
            }
        )

        candidates = parse_xai_candidates({}, response_text, job)

        assert candidates == []


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

    def test_rotates_one_complaint_lane_per_brand_and_one_seeker_per_scan(self):
        config = Config(
            search_queries=[
                SearchQuery(
                    query="grey delay",
                    category_hint="competitor_complaint",
                    description="Grey delay",
                    lane_id="complaint-grey-delay",
                    intent_summary="Grey delay",
                    brand_family="grey",
                    cooldown_seconds=60,
                    priority=10,
                ),
                SearchQuery(
                    query="grey payment",
                    category_hint="competitor_complaint",
                    description="Grey payment",
                    lane_id="complaint-grey-payment",
                    intent_summary="Grey payment",
                    brand_family="grey",
                    cooldown_seconds=60,
                    priority=20,
                ),
                SearchQuery(
                    query="wise delay",
                    category_hint="competitor_complaint",
                    description="Wise delay",
                    lane_id="complaint-wise-delay",
                    intent_summary="Wise delay",
                    brand_family="wise",
                    cooldown_seconds=60,
                    priority=11,
                ),
                SearchQuery(
                    query="wise payment",
                    category_hint="competitor_complaint",
                    description="Wise payment",
                    lane_id="complaint-wise-payment",
                    intent_summary="Wise payment",
                    brand_family="wise",
                    cooldown_seconds=60,
                    priority=21,
                ),
                SearchQuery(
                    query="seekers transfers",
                    category_hint="solution_seeker",
                    description="Seekers transfers",
                    lane_id="seekers-transfers",
                    intent_summary="Seekers transfers",
                    cooldown_seconds=60,
                    priority=30,
                ),
                SearchQuery(
                    query="seekers cards",
                    category_hint="solution_seeker",
                    description="Seekers cards",
                    lane_id="seekers-cards",
                    intent_summary="Seekers cards",
                    cooldown_seconds=60,
                    priority=31,
                ),
            ],
            max_api_requests_per_scan=8,
        )
        runtime = SearchRuntime()

        jobs = select_due_queries(config, runtime, request_budget=8)

        assert [job.query.lane_id for job in jobs] == [
            "complaint-grey-delay",
            "complaint-wise-delay",
            "seekers-transfers",
        ]
