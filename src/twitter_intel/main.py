"""
Main entry point for Twitter Intelligence Bot.

This is the new modular entry point that replaces bot.py.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add src to path for imports
src_path = Path(__file__).parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from dotenv import load_dotenv

from twitter_intel.config import Config, load_config
from twitter_intel.application.container import Container
from twitter_intel.exceptions import ConfigurationError


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("twitter_intel")


def validate_config(config: Config) -> list[str]:
    """
    Validate configuration and return list of missing required values.

    Args:
        config: Application configuration

    Returns:
        List of missing configuration keys
    """
    missing = []

    if not config.discord_bot_token:
        missing.append("DISCORD_BOT_TOKEN")

    if config.search_provider not in ("manual_only", "xai_x_search"):
        if not config.gemini_api_key:
            missing.append("GEMINI_API_KEY")

    if config.search_provider == "twitterapi_io":
        if not config.twitterapi_io_api_key:
            missing.append("TWITTERAPI_IO_API_KEY")

    if config.search_provider == "xai_x_search":
        if not config.xai_api_key:
            missing.append("XAI_API_KEY")

    if config.search_provider == "twscrape":
        if not config.twscrape_username:
            missing.append("TWSCRAPE_USERNAME")

    auth_mode = (config.discord_command_auth_mode or "enforce").strip().lower()
    if auth_mode not in {"audit", "enforce"}:
        missing.append("DISCORD_COMMAND_AUTH_MODE (must be one of: audit, enforce)")
    elif auth_mode == "enforce":
        has_actor_allowlist = bool(
            config.discord_allowed_user_ids or config.discord_allowed_role_ids
        )
        has_channel_allowlist = bool(config.discord_allowed_channel_ids)
        if not has_actor_allowlist:
            missing.append(
                "DISCORD_ALLOWED_USER_IDS or DISCORD_ALLOWED_ROLE_IDS "
                "(required when DISCORD_COMMAND_AUTH_MODE=enforce)"
            )
        if not has_channel_allowlist:
            missing.append(
                "DISCORD_ALLOWED_CHANNEL_IDS "
                "(required when DISCORD_COMMAND_AUTH_MODE=enforce)"
            )

    return missing


async def run_bot(container: Container) -> None:
    """
    Run the main bot loop.

    Runs three concurrent tasks:
    - Discord gateway (for button interactions and commands)
    - Scan loop (periodic tweet searching and processing)
    - Stats loop (periodic statistics posting)

    Args:
        container: Dependency injection container
    """
    config = container.config

    log.info("Twitter Intelligence Bot starting...")
    log.info("Search provider: %s", config.search_provider)
    log.info("Poll interval: %d seconds", config.poll_interval)

    # Send startup notification
    await container.notification_service.send_status(
        f"Bot started (provider: {config.search_provider})"
    )

    # Get the gateway and scheduler from container
    gateway = container.discord_gateway
    scheduler = container.scheduler

    log.info("Starting concurrent tasks...")

    # Run all tasks concurrently
    try:
        await asyncio.gather(
            gateway.run(),                  # Discord interactions
            scheduler.run_scan_loop(),      # Main scan loop
            scheduler.run_stats_loop(6.0),  # Post stats every 6 hours
        )
    except asyncio.CancelledError:
        log.info("Bot shutting down...")
        await gateway.stop()
        await scheduler.stop()


async def main() -> int:
    """
    Main entry point.

    Returns:
        Exit code (0 for success, 1 for error)
    """
    # Load environment variables
    load_dotenv()

    # Load configuration
    try:
        config = load_config()
    except Exception as e:
        log.error("Failed to load configuration: %s", e)
        return 1

    # Validate configuration
    valid_providers = {"twitterapi_io", "xai_x_search", "twscrape", "manual_only"}
    if config.search_provider not in valid_providers:
        log.error(
            "Unsupported SEARCH_PROVIDER '%s'. Use one of: %s",
            config.search_provider,
            ", ".join(sorted(valid_providers)),
        )
        return 1

    missing = validate_config(config)
    if missing:
        log.error("Missing required configuration: %s", ", ".join(missing))
        return 1

    # Create container
    try:
        container = Container.create(config)
    except ConfigurationError as e:
        log.error("Configuration error: %s", e)
        return 1
    except Exception as e:
        log.error("Failed to initialize: %s", e)
        return 1

    # Run the bot
    try:
        await run_bot(container)
        return 0
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        return 0
    except Exception as e:
        log.exception("Unexpected error: %s", e)
        return 1
    finally:
        container.close()


def cli_main() -> None:
    """CLI entry point."""
    sys.exit(asyncio.run(main()))


if __name__ == "__main__":
    cli_main()
