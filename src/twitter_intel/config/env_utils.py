"""
Environment variable parsing utilities.

Provides helper functions for parsing environment variables consistently,
including boolean flags, comma-separated lists, and path resolution for
Docker container environments.
"""

import os
from pathlib import Path


def env_flag(name: str, default: str = "false") -> bool:
    """
    Parse a boolean environment variable consistently.

    Accepts: "1", "true", "yes", "on" (case-insensitive) as True.
    Everything else is False.

    Args:
        name: Environment variable name
        default: Default value if not set (default: "false")

    Returns:
        Boolean value of the environment variable
    """
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def parse_handle_env_list(name: str) -> list[str]:
    """
    Parse a comma-separated list of X handles from an environment variable.

    Handles are normalized (@ prefix removed) and deduplicated.
    Maximum of 10 handles are returned.

    Args:
        name: Environment variable name

    Returns:
        List of unique handle strings (without @ prefix), max 10 items
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return []

    handles: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        handle = part.strip().lstrip("@")
        if not handle or handle in seen:
            continue
        handles.append(handle)
        seen.add(handle)
        if len(handles) >= 10:
            break
    return handles


def parse_id_env_list(name: str, max_items: int = 100) -> list[str]:
    """
    Parse a comma-separated list of Discord snowflake IDs from an env var.

    Values are trimmed, deduplicated, and filtered to digits only.

    Args:
        name: Environment variable name
        max_items: Maximum number of IDs to return

    Returns:
        List of unique numeric ID strings
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return []

    ids: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        value = part.strip()
        if not value or not value.isdigit() or value in seen:
            continue
        ids.append(value)
        seen.add(value)
        if len(ids) >= max(1, max_items):
            break
    return ids


def resolve_data_path(raw_path: str, default_name: str) -> str:
    """
    Resolve a data file path, preferring Docker mounted volume when available.

    When running in a Docker container with /app/data mounted, relative paths
    are resolved to that directory. Outside Docker, paths are kept as-is.

    Args:
        raw_path: Raw path from environment (may be empty or relative)
        default_name: Default filename if raw_path is empty

    Returns:
        Resolved absolute or relative path string
    """
    path = Path(raw_path or default_name)
    if path.is_absolute():
        return str(path)

    container_data_dir = Path("/app/data")
    if container_data_dir.exists():
        return str(container_data_dir / path.name)

    return str(path)


def resolve_db_path(raw_path: str) -> str:
    """
    Resolve the main bot state database path.

    Args:
        raw_path: Raw path from DB_PATH environment variable

    Returns:
        Resolved database file path
    """
    return resolve_data_path(raw_path, "bot_state.db")


def resolve_twscrape_db_path(raw_path: str) -> str:
    """
    Resolve the twscrape accounts database path.

    Args:
        raw_path: Raw path from TWSCRAPE_DB_PATH environment variable

    Returns:
        Resolved database file path
    """
    return resolve_data_path(raw_path, "accounts.db")
