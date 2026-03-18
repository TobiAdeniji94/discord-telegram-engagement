"""
Tests for twitter_intel.config.settings module.
"""

import json
import pytest


class TestConfig:
    """Tests for Config dataclass."""

    def test_default_values(self, clean_env):
        """Config should have sensible defaults."""
        from twitter_intel.config import Config

        cfg = Config()
        assert cfg.max_tweet_age_minutes == 120
        assert cfg.poll_interval == 900
        assert cfg.search_provider == "twitterapi_io"
        assert cfg.max_api_requests_per_scan == 4
        assert cfg.max_local_candidates_per_scan == 8
        assert cfg.max_ai_candidates_per_scan == 4
        assert cfg.max_discord_approvals_per_scan == 2
        assert cfg.gemini_model == "gemini-2.0-flash"
        assert cfg.xai_model == "grok-4-1-fast-reasoning"
        assert cfg.discord_command_auth_mode == "enforce"
        assert cfg.discord_allowed_user_ids == []
        assert cfg.discord_allowed_role_ids == []
        assert cfg.discord_allowed_channel_ids == []
        assert cfg.discord_require_pending_channel_match is True

    def test_default_search_queries(self, clean_env):
        """Config should have default search queries."""
        from twitter_intel.config import Config

        cfg = Config()
        assert len(cfg.search_queries) == 3

        # Check competitor complaints query
        competitor_query = cfg.search_queries[0]
        assert competitor_query.category_hint == "competitor_complaint"
        assert "chipper cash" in competitor_query.query.lower()

        # Check solution seeker query
        seeker_query = cfg.search_queries[1]
        assert seeker_query.category_hint == "solution_seeker"
        assert "send money" in seeker_query.query.lower()

        # Check brand mention query
        brand_query = cfg.search_queries[2]
        assert brand_query.category_hint == "brand_mention"
        assert "yara.cash" in brand_query.query.lower()


class TestLoadConfig:
    """Tests for load_config function."""

    def test_loads_defaults_when_no_env_vars(self, clean_env):
        """load_config should return defaults when no env vars set."""
        from twitter_intel.config import load_config

        cfg = load_config()
        assert cfg.max_tweet_age_minutes == 120
        assert cfg.poll_interval == 900
        assert cfg.search_provider == "twitterapi_io"

    def test_loads_from_env_vars(self, clean_env):
        """load_config should load values from environment variables."""
        from twitter_intel.config import load_config

        clean_env.setenv("MAX_TWEET_AGE_MINUTES", "60")
        clean_env.setenv("POLL_INTERVAL", "300")
        clean_env.setenv("SEARCH_PROVIDER", "xai_x_search")
        clean_env.setenv("DISCORD_BOT_TOKEN", "my_token")
        clean_env.setenv("GEMINI_API_KEY", "gemini_key")

        cfg = load_config()
        assert cfg.max_tweet_age_minutes == 60
        assert cfg.poll_interval == 300
        assert cfg.search_provider == "xai_x_search"
        assert cfg.discord_bot_token == "my_token"
        assert cfg.gemini_api_key == "gemini_key"

    def test_loads_boolean_env_vars(self, clean_env):
        """load_config should parse boolean env vars correctly."""
        from twitter_intel.config import load_config

        clean_env.setenv("ENABLE_LATEST_FALLBACK", "true")
        clean_env.setenv("X_POSTING_DRY_RUN", "1")
        clean_env.setenv("TELEGRAM_ENABLED", "yes")
        clean_env.setenv("DEBUG_DISCARDED_TO_STATUS", "false")

        cfg = load_config()
        assert cfg.enable_latest_fallback is True
        assert cfg.x_posting_dry_run is True
        assert cfg.telegram_enabled is True
        assert cfg.debug_discarded_to_status is False

    def test_loads_custom_search_queries_from_json(self, clean_env):
        """load_config should parse SEARCH_QUERIES JSON."""
        from twitter_intel.config import load_config

        custom_queries = [
            {
                "query": "test query 1",
                "category_hint": "solution_seeker",
                "description": "Test description",
                "cooldown_seconds": 600,
            },
            {
                "query": "test query 2",
                "category_hint": "brand_mention",
                "description": "Another test",
            },
        ]
        clean_env.setenv("SEARCH_QUERIES", json.dumps(custom_queries))

        cfg = load_config()
        assert len(cfg.search_queries) == 2
        assert cfg.search_queries[0].query == "test query 1"
        assert cfg.search_queries[0].cooldown_seconds == 600
        assert cfg.search_queries[1].query == "test query 2"

    def test_invalid_search_queries_json_uses_defaults(self, clean_env):
        """load_config should use defaults if SEARCH_QUERIES JSON is invalid."""
        from twitter_intel.config import load_config

        clean_env.setenv("SEARCH_QUERIES", "not valid json {{{")

        cfg = load_config()
        # Should still have default queries
        assert len(cfg.search_queries) == 3

    def test_strips_at_from_brand_username(self, clean_env):
        """load_config should strip @ prefix from BRAND_X_USERNAME."""
        from twitter_intel.config import load_config

        clean_env.setenv("BRAND_X_USERNAME", "@yaracash")

        cfg = load_config()
        assert cfg.brand_x_username == "yaracash"

    def test_parses_handle_lists(self, clean_env):
        """load_config should parse XAI handle lists."""
        from twitter_intel.config import load_config

        clean_env.setenv("XAI_EXCLUDED_X_HANDLES", "@spammer1,@spammer2")
        clean_env.setenv("XAI_ALLOWED_X_HANDLES", "partner1,partner2,partner3")

        cfg = load_config()
        assert cfg.xai_excluded_x_handles == ["spammer1", "spammer2"]
        assert cfg.xai_allowed_x_handles == ["partner1", "partner2", "partner3"]

    def test_enforces_min_values(self, clean_env):
        """load_config should enforce minimum values for certain settings."""
        from twitter_intel.config import load_config

        clean_env.setenv("XAI_MAX_TURNS", "0")
        clean_env.setenv("XAI_REQUEST_TIMEOUT_SECONDS", "1")

        cfg = load_config()
        assert cfg.xai_max_turns >= 1
        assert cfg.xai_request_timeout_seconds >= 5

    def test_loads_search_since_days(self, clean_env):
        """load_config should parse SEARCH_SINCE_DAYS."""
        from twitter_intel.config import load_config

        clean_env.setenv("SEARCH_SINCE_DAYS", "7")

        cfg = load_config()
        assert cfg.search_since_days == 7

    def test_invalid_search_since_days_ignored(self, clean_env):
        """load_config should ignore invalid SEARCH_SINCE_DAYS."""
        from twitter_intel.config import load_config

        clean_env.setenv("SEARCH_SINCE_DAYS", "not_a_number")

        cfg = load_config()
        assert cfg.search_since_days is None

    def test_loads_discord_auth_policy(self, clean_env):
        """load_config should parse Discord authorization env vars."""
        from twitter_intel.config import load_config

        clean_env.setenv("DISCORD_COMMAND_AUTH_MODE", "audit")
        clean_env.setenv("DISCORD_ALLOWED_USER_IDS", "123,456")
        clean_env.setenv("DISCORD_ALLOWED_ROLE_IDS", "777")
        clean_env.setenv("DISCORD_ALLOWED_CHANNEL_IDS", "999,1000")
        clean_env.setenv("DISCORD_REQUIRE_PENDING_CHANNEL_MATCH", "false")

        cfg = load_config()
        assert cfg.discord_command_auth_mode == "audit"
        assert cfg.discord_allowed_user_ids == ["123", "456"]
        assert cfg.discord_allowed_role_ids == ["777"]
        assert cfg.discord_allowed_channel_ids == ["999", "1000"]
        assert cfg.discord_require_pending_channel_match is False

    def test_invalid_discord_auth_mode_defaults_to_enforce(self, clean_env):
        """load_config should normalize unsupported auth mode to enforce."""
        from twitter_intel.config import load_config

        clean_env.setenv("DISCORD_COMMAND_AUTH_MODE", "allow_all")
        cfg = load_config()
        assert cfg.discord_command_auth_mode == "enforce"


class TestSearchJob:
    """Tests for SearchJob dataclass."""

    def test_default_query_type(self):
        """SearchJob should default to 'Top' query type."""
        from twitter_intel.config import SearchJob, SearchQuery

        query = SearchQuery(
            query="test",
            category_hint="brand_mention",
            description="test query",
        )
        job = SearchJob(query=query)
        assert job.query_type == "Top"

    def test_custom_query_type(self):
        """SearchJob should accept custom query type."""
        from twitter_intel.config import SearchJob, SearchQuery

        query = SearchQuery(
            query="test",
            category_hint="brand_mention",
            description="test query",
        )
        job = SearchJob(query=query, query_type="Latest")
        assert job.query_type == "Latest"


class TestSearchRuntime:
    """Tests for SearchRuntime dataclass."""

    def test_default_values(self):
        """SearchRuntime should initialize with zero counters."""
        from twitter_intel.config import SearchRuntime

        runtime = SearchRuntime()
        assert runtime.api_requests_made == 0
        assert runtime.tweets_fetched == 0
        assert runtime.duplicates_dropped == 0
        assert runtime.sent_to_gemini == 0
        assert runtime.queued_to_discord == 0
        assert runtime.provider_paused_until == 0.0
        assert runtime.last_query_run == {}
        assert runtime.empty_scan_counts == {}
        assert runtime.stale_candidate_ids == set()

    def test_mutable_dict_fields_are_independent(self):
        """Each SearchRuntime instance should have independent dict fields."""
        from twitter_intel.config import SearchRuntime

        runtime1 = SearchRuntime()
        runtime2 = SearchRuntime()

        runtime1.last_query_run["query1"] = 123.0
        runtime1.stale_candidate_ids.add("tweet1")
        assert "query1" not in runtime2.last_query_run
        assert "tweet1" not in runtime2.stale_candidate_ids
