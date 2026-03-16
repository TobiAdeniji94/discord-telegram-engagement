"""
Tests for twitter_intel.config.env_utils module.
"""

import pytest


class TestEnvFlag:
    """Tests for env_flag function."""

    def test_true_values(self, monkeypatch):
        """env_flag should return True for truthy string values."""
        from twitter_intel.config.env_utils import env_flag

        for value in ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"]:
            monkeypatch.setenv("TEST_FLAG", value)
            assert env_flag("TEST_FLAG") is True, f"Expected True for '{value}'"

    def test_false_values(self, monkeypatch):
        """env_flag should return False for falsy string values."""
        from twitter_intel.config.env_utils import env_flag

        for value in ["0", "false", "False", "FALSE", "no", "NO", "off", "OFF", "", "random"]:
            monkeypatch.setenv("TEST_FLAG", value)
            assert env_flag("TEST_FLAG") is False, f"Expected False for '{value}'"

    def test_missing_variable_uses_default(self, monkeypatch):
        """env_flag should use default when variable is not set."""
        from twitter_intel.config.env_utils import env_flag

        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        assert env_flag("NONEXISTENT_VAR") is False
        assert env_flag("NONEXISTENT_VAR", "true") is True

    def test_whitespace_handling(self, monkeypatch):
        """env_flag should handle whitespace in values."""
        from twitter_intel.config.env_utils import env_flag

        monkeypatch.setenv("TEST_FLAG", "  true  ")
        assert env_flag("TEST_FLAG") is True


class TestParseHandleEnvList:
    """Tests for parse_handle_env_list function."""

    def test_empty_value(self, monkeypatch):
        """Should return empty list for empty/missing value."""
        from twitter_intel.config.env_utils import parse_handle_env_list

        monkeypatch.delenv("HANDLES", raising=False)
        assert parse_handle_env_list("HANDLES") == []

        monkeypatch.setenv("HANDLES", "")
        assert parse_handle_env_list("HANDLES") == []

        monkeypatch.setenv("HANDLES", "   ")
        assert parse_handle_env_list("HANDLES") == []

    def test_single_handle(self, monkeypatch):
        """Should parse single handle correctly."""
        from twitter_intel.config.env_utils import parse_handle_env_list

        monkeypatch.setenv("HANDLES", "user1")
        assert parse_handle_env_list("HANDLES") == ["user1"]

    def test_multiple_handles(self, monkeypatch):
        """Should parse comma-separated handles."""
        from twitter_intel.config.env_utils import parse_handle_env_list

        monkeypatch.setenv("HANDLES", "user1,user2,user3")
        assert parse_handle_env_list("HANDLES") == ["user1", "user2", "user3"]

    def test_strips_at_prefix(self, monkeypatch):
        """Should remove @ prefix from handles."""
        from twitter_intel.config.env_utils import parse_handle_env_list

        monkeypatch.setenv("HANDLES", "@user1,@user2,user3")
        assert parse_handle_env_list("HANDLES") == ["user1", "user2", "user3"]

    def test_deduplication(self, monkeypatch):
        """Should remove duplicate handles."""
        from twitter_intel.config.env_utils import parse_handle_env_list

        monkeypatch.setenv("HANDLES", "user1,user2,user1,user3,user2")
        assert parse_handle_env_list("HANDLES") == ["user1", "user2", "user3"]

    def test_max_10_handles(self, monkeypatch):
        """Should limit to 10 handles maximum."""
        from twitter_intel.config.env_utils import parse_handle_env_list

        handles = ",".join([f"user{i}" for i in range(15)])
        monkeypatch.setenv("HANDLES", handles)
        result = parse_handle_env_list("HANDLES")
        assert len(result) == 10
        assert result == [f"user{i}" for i in range(10)]

    def test_whitespace_handling(self, monkeypatch):
        """Should strip whitespace from handles."""
        from twitter_intel.config.env_utils import parse_handle_env_list

        monkeypatch.setenv("HANDLES", " user1 , user2 , user3 ")
        assert parse_handle_env_list("HANDLES") == ["user1", "user2", "user3"]


class TestResolveDataPath:
    """Tests for resolve_data_path and related functions."""

    def test_absolute_path_unchanged(self):
        """Absolute paths should be returned unchanged."""
        from twitter_intel.config.env_utils import resolve_data_path

        if os.name == "nt":
            path = "C:\\data\\mydb.db"
        else:
            path = "/data/mydb.db"

        result = resolve_data_path(path, "default.db")
        assert result == path

    def test_empty_path_uses_default(self):
        """Empty path should use default name."""
        from twitter_intel.config.env_utils import resolve_data_path

        result = resolve_data_path("", "default.db")
        assert result == "default.db"

    def test_relative_path_outside_docker(self):
        """Relative path outside Docker should remain relative."""
        from twitter_intel.config.env_utils import resolve_data_path

        result = resolve_data_path("mydata.db", "default.db")
        assert result == "mydata.db"


class TestParseIdEnvList:
    """Tests for parse_id_env_list function."""

    def test_empty_value(self, monkeypatch):
        from twitter_intel.config.env_utils import parse_id_env_list

        monkeypatch.delenv("DISCORD_IDS", raising=False)
        assert parse_id_env_list("DISCORD_IDS") == []

        monkeypatch.setenv("DISCORD_IDS", "")
        assert parse_id_env_list("DISCORD_IDS") == []

    def test_parses_numeric_ids(self, monkeypatch):
        from twitter_intel.config.env_utils import parse_id_env_list

        monkeypatch.setenv("DISCORD_IDS", "123,456,789")
        assert parse_id_env_list("DISCORD_IDS") == ["123", "456", "789"]

    def test_filters_invalid_and_deduplicates(self, monkeypatch):
        from twitter_intel.config.env_utils import parse_id_env_list

        monkeypatch.setenv("DISCORD_IDS", "123,abc,123,456,  ,789x,456")
        assert parse_id_env_list("DISCORD_IDS") == ["123", "456"]

    def test_respects_max_items(self, monkeypatch):
        from twitter_intel.config.env_utils import parse_id_env_list

        monkeypatch.setenv("DISCORD_IDS", "1,2,3,4,5")
        assert parse_id_env_list("DISCORD_IDS", max_items=3) == ["1", "2", "3"]


# Need to import os for the absolute path test
import os
