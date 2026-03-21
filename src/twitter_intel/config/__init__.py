"""
Configuration module for Twitter Intelligence Bot.

This module provides:
- Environment variable parsing utilities
- Search query definitions
- Main Config dataclass with all settings
- Brand registry for XSS (SRS-YARA-XSS-2026)
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
from twitter_intel.config.brand_registry import (
    BrandConfig,
    BRAND_REGISTRY,
    get_brand,
    get_all_brands,
    get_brand_keys,
    get_all_excluded_handles,
    ScoringWeights,
    DEFAULT_SCORING_WEIGHTS,
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
    # Brand registry (SRS-YARA-XSS-2026)
    "BrandConfig",
    "BRAND_REGISTRY",
    "get_brand",
    "get_all_brands",
    "get_brand_keys",
    "get_all_excluded_handles",
    "ScoringWeights",
    "DEFAULT_SCORING_WEIGHTS",
]
