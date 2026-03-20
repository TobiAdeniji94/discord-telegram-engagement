"""
Configuration module for Twitter Intelligence Bot.

This module provides:
- Environment variable parsing utilities
- Search query definitions
- Main Config dataclass with all settings
"""

from twitter_intel.config.env_utils import (
    env_flag,
    parse_csv_env_list,
    parse_handle_env_list,
    parse_id_env_list,
    resolve_data_path,
    resolve_db_path,
    resolve_twscrape_db_path,
)
from twitter_intel.config.search_queries import SearchQuery, DEFAULT_SEARCH_QUERIES
from twitter_intel.config.settings import (
    Config,
    SearchJob,
    SearchRuntime,
    load_config,
)

__all__ = [
    # Environment utilities
    "env_flag",
    "parse_csv_env_list",
    "parse_handle_env_list",
    "parse_id_env_list",
    "resolve_data_path",
    "resolve_db_path",
    "resolve_twscrape_db_path",
    # Search queries
    "SearchQuery",
    "DEFAULT_SEARCH_QUERIES",
    # Settings
    "Config",
    "SearchJob",
    "SearchRuntime",
    "load_config",
]
