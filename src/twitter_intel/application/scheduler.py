"""
Scheduler for periodic tasks.

Manages the scan loop and stats loop for the Twitter Intelligence Bot.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from twitter_intel.application.use_cases import ScanAndNotifyUseCase
    from twitter_intel.config import Config, SearchRuntime
    from twitter_intel.domain.interfaces import NotificationService, TweetRepository

log = logging.getLogger(__name__)


class ScanScheduler:
    """
    Scheduler for periodic scan operations.

    Manages the main scan loop that periodically searches for and
    processes tweet candidates. Also runs a stats loop to post
    periodic statistics.
    """

    def __init__(
        self,
        config: "Config",
        scan_use_case: "ScanAndNotifyUseCase",
        notification_service: "NotificationService",
        repository: "TweetRepository",
        runtime: "SearchRuntime",
    ):
        """
        Initialize the scheduler.

        Args:
            config: Application configuration
            scan_use_case: Use case for scanning and notifying
            notification_service: Service for sending notifications
            repository: Tweet repository for stats
            runtime: Runtime state tracking
        """
        self._config = config
        self._scan_use_case = scan_use_case
        self._notification_service = notification_service
        self._repository = repository
        self._runtime = runtime
        self._running = False

    async def run_scan_loop(self) -> None:
        """
        Run the main scan loop.

        Periodically executes scans based on the configured poll interval.
        Handles manual_only mode and provider pauses.
        """
        self._running = True
        poll_interval = self._config.poll_interval

        log.info(
            "Starting scan loop (provider=%s, interval=%ds)",
            self._config.search_provider,
            poll_interval,
        )

        if self._config.search_provider == "manual_only":
            log.info(
                "Search provider is manual_only; scan loop will sleep indefinitely. "
                "Use !ingest or !smoke to add candidates."
            )
            while self._running:
                await asyncio.sleep(60)
            return

        # Initial delay to let Discord gateway connect first
        await asyncio.sleep(5)

        while self._running:
            try:
                await self._execute_scan_cycle()
            except Exception as exc:
                log.error(f"Scan cycle error: {exc}", exc_info=True)
                await self._notification_service.send_status(
                    f"Scan error: {exc}"
                )

            await asyncio.sleep(poll_interval)

    async def _execute_scan_cycle(self) -> None:
        """Execute a single scan cycle."""
        log.info("Starting scan cycle...")

        result = await self._scan_use_case.execute()

        log.info(
            "Scan cycle complete: %s queued, %s total, %s filtered",
            result.queued_count,
            result.total_candidates,
            result.filtered_count,
        )

        # Update runtime stats
        self._runtime.scans_completed += 1

    async def run_stats_loop(self, interval_hours: float = 6.0) -> None:
        """
        Run the stats posting loop.

        Periodically posts statistics to the status channel.

        Args:
            interval_hours: Hours between stats posts (default 6)
        """
        interval_seconds = interval_hours * 3600
        self._running = True

        log.info("Starting stats loop (interval=%.1f hours)", interval_hours)

        # Wait before first stats post
        await asyncio.sleep(interval_seconds)

        while self._running:
            try:
                await self._post_stats()
            except Exception as exc:
                log.error(f"Stats post error: {exc}", exc_info=True)

            await asyncio.sleep(interval_seconds)

    async def _post_stats(self) -> None:
        """Post current stats to the status channel."""
        stats = self._repository.get_stats()

        # Build stats message
        total = stats["total_processed"] or 1
        replied_pct = (stats["replied"] / total) * 100
        rejected_pct = (stats["rejected"] / total) * 100

        cat_summary = ", ".join(
            f"{k}: {v}" for k, v in stats["by_category"].items()
        )

        runtime_stats = (
            f"Scans: {self._runtime.scans_completed} | "
            f"Fetched: {self._runtime.tweets_fetched} | "
            f"To Discord: {self._runtime.queued_to_discord} | "
            f"Filtered: {self._runtime.locally_filtered_out} | "
            f"Dupes: {self._runtime.duplicates_dropped}"
        )

        message = (
            f"**Periodic Stats Update**\n"
            f"Total: {stats['total_processed']} | "
            f"Replied: {stats['replied']} ({replied_pct:.1f}%) | "
            f"Rejected: {stats['rejected']} ({rejected_pct:.1f}%) | "
            f"Pending: {stats['pending']}\n"
            f"Categories: {cat_summary}\n"
            f"Runtime: {runtime_stats}"
        )

        await self._notification_service.send_status(message)
        log.info("Posted periodic stats update")

    async def stop(self) -> None:
        """Stop the scheduler loops."""
        self._running = False
        log.info("Scheduler stopping...")
