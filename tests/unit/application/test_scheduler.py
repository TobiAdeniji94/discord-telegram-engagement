"""
Tests for ScanScheduler.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from twitter_intel.application.scheduler import ScanScheduler
from twitter_intel.application.use_cases.scan_and_notify import ScanResult
from twitter_intel.config import SearchRuntime


class TestScanScheduler:
    """Tests for ScanScheduler class."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = MagicMock()
        config.search_provider = "twitterapi_io"
        config.poll_interval = 60
        return config

    @pytest.fixture
    def mock_scan_use_case(self):
        """Create mock scan use case."""
        use_case = MagicMock()
        use_case.execute = AsyncMock(return_value=ScanResult(
            queued_count=3,
            total_candidates=10,
            filtered_count=5,
            message="Test scan complete",
        ))
        return use_case

    @pytest.fixture
    def mock_notification_service(self):
        """Create mock notification service."""
        service = MagicMock()
        service.send_status = AsyncMock()
        return service

    @pytest.fixture
    def mock_repository(self):
        """Create mock repository."""
        repo = MagicMock()
        repo.get_stats = MagicMock(return_value={
            "total_processed": 100,
            "replied": 40,
            "rejected": 30,
            "pending": 30,
            "by_category": {
                "brand-mentions": 50,
                "competitor-complaints": 30,
                "solution-seekers": 20,
            }
        })
        repo.get_runtime_value = MagicMock(return_value=None)
        repo.set_runtime_value = MagicMock()
        return repo

    @pytest.fixture
    def runtime(self):
        """Create runtime instance."""
        return SearchRuntime()

    @pytest.fixture
    def scheduler(
        self,
        mock_config,
        mock_scan_use_case,
        mock_notification_service,
        mock_repository,
        runtime,
    ):
        """Create scheduler with mocks."""
        return ScanScheduler(
            config=mock_config,
            scan_use_case=mock_scan_use_case,
            notification_service=mock_notification_service,
            repository=mock_repository,
            runtime=runtime,
        )

    async def test_execute_scan_cycle(self, scheduler, mock_scan_use_case, runtime):
        """Should execute a scan cycle."""
        await scheduler._execute_scan_cycle()

        mock_scan_use_case.execute.assert_called_once()
        assert runtime.scans_completed == 1
        scheduler._repository.set_runtime_value.assert_called_once()

    async def test_multiple_scan_cycles(self, scheduler, mock_scan_use_case, runtime):
        """Should increment scan count for each cycle."""
        await scheduler._execute_scan_cycle()
        await scheduler._execute_scan_cycle()
        await scheduler._execute_scan_cycle()

        assert mock_scan_use_case.execute.call_count == 3
        assert runtime.scans_completed == 3

    async def test_post_stats(self, scheduler, mock_notification_service, mock_repository):
        """Should post stats to notification service."""
        await scheduler._post_stats()

        mock_notification_service.send_status.assert_called_once()
        call_args = mock_notification_service.send_status.call_args[0][0]

        assert "Periodic Stats Update" in call_args
        assert "Total: 100" in call_args
        assert "Replied: 40" in call_args
        assert "40.0%" in call_args

    async def test_post_stats_includes_runtime(
        self, scheduler, mock_notification_service, runtime
    ):
        """Should include runtime stats in post."""
        runtime.scans_completed = 5
        runtime.tweets_fetched = 100
        runtime.queued_to_discord = 25
        runtime.locally_filtered_out = 50
        runtime.duplicates_dropped = 10

        await scheduler._post_stats()

        call_args = mock_notification_service.send_status.call_args[0][0]

        assert "Scans: 5" in call_args
        assert "Fetched: 100" in call_args
        assert "To Discord: 25" in call_args
        assert "Filtered: 50" in call_args
        assert "Dupes: 10" in call_args

    async def test_post_stats_includes_xai_telemetry(
        self,
        scheduler,
        mock_notification_service,
        runtime,
    ):
        scheduler._config.search_provider = "xai_x_search"
        scheduler._config.max_api_requests_per_scan = 8
        scheduler._config.search_queries = []
        scheduler._config.xai_model = "grok-4.20-0309-reasoning"
        scheduler._config.xai_requests_per_minute_limit = 600
        scheduler._config.xai_tokens_per_minute_limit = 3500000
        runtime.provider_paused_until = 1893456000.0
        runtime.provider_pause_reason = (
            "xAI rate limited the bot. Search is paused until the retry window."
        )
        runtime.xai_requests_made = 2
        runtime.xai_http_attempts_made = 2
        runtime.xai_recent_usage_events = [
            {
                "timestamp": 9999999999.0,
                "http_attempts": 2,
                "prompt_tokens": 20,
                "prompt_text_tokens": 15,
                "completion_tokens": 40,
                "reasoning_tokens": 10,
                "cached_prompt_tokens": 5,
            }
        ]

        await scheduler._post_stats()

        call_args = mock_notification_service.send_status.call_args[0][0]

        assert "xAI:" in call_args
        assert "HTTP RPM" in call_args
        assert "limits RPM" in call_args
        assert "2030-01-01T00:00:00Z" in call_args

    async def test_stop_scheduler(self, scheduler):
        """Should stop when stop is called."""
        scheduler._running = True
        await scheduler.stop()
        assert scheduler._running is False

    def test_initialize_restart_catchup_sets_runtime_window(
        self,
        scheduler,
        mock_repository,
        runtime,
    ):
        """Scheduler should activate one-cycle catch-up after downtime."""
        five_minutes_ago = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).replace(microsecond=0)
        mock_repository.get_runtime_value.return_value = (
            five_minutes_ago.isoformat().replace("+00:00", "Z")
        )
        scheduler._config.poll_interval = 60

        scheduler._initialize_restart_catchup()

        assert runtime.restart_catchup_start_utc == five_minutes_ago
        assert runtime.restart_catchup_end_utc is not None


class TestScanLoopManualOnly:
    """Tests for manual_only mode in scan loop."""

    @pytest.fixture
    def manual_scheduler(self):
        """Create scheduler in manual_only mode."""
        config = MagicMock()
        config.search_provider = "manual_only"
        config.poll_interval = 60

        return ScanScheduler(
            config=config,
            scan_use_case=MagicMock(),
            notification_service=MagicMock(),
            repository=MagicMock(),
            runtime=SearchRuntime(),
        )

    async def test_manual_only_does_not_scan(self, manual_scheduler):
        """Should not execute scans in manual_only mode."""
        # Run for a very short time
        task = asyncio.create_task(manual_scheduler.run_scan_loop())
        await asyncio.sleep(0.1)
        await manual_scheduler.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Scan use case should never be called
        manual_scheduler._scan_use_case.execute.assert_not_called()
