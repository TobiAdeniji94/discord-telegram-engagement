"""
Dependency injection container.

Provides centralized dependency management for the application.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from twitter_intel.application.scheduler import ScanScheduler
from twitter_intel.application.use_cases import (
    ApproveTweetUseCase,
    ManualIngestUseCase,
    RejectTweetUseCase,
    ScanAndNotifyUseCase,
    SmokeTestUseCase,
)
from twitter_intel.config import Config, SearchRuntime
from twitter_intel.config.settings import DEFAULT_XAI_MODEL, DEFAULT_XAI_MODEL_FALLBACK
from twitter_intel.domain.interfaces import (
    AIClassifier,
    NotificationService,
    SearchProvider,
    TweetRepository,
)
from twitter_intel.infrastructure.ai import GeminiClassifier, NullClassifier
from twitter_intel.infrastructure.database import SqliteTweetRepository
from twitter_intel.infrastructure.notifications import DiscordBot, DiscordGateway, TelegramNotifier
from twitter_intel.infrastructure.search import SearchProviderFactory, XaiClient
from twitter_intel.infrastructure.twitter import XPoster

if TYPE_CHECKING:
    pass


@dataclass
class Container:
    """
    Dependency injection container for the application.

    Holds all service instances and provides a centralized place
    for dependency management.
    """
    config: Config
    repository: TweetRepository
    search_provider: SearchProvider
    classifier: AIClassifier | None
    notification_service: NotificationService
    telegram_notifier: TelegramNotifier | None = None
    runtime: SearchRuntime = field(default_factory=SearchRuntime)

    # Infrastructure components
    x_poster: XPoster | None = None
    xai_client: XaiClient | None = None

    # Use cases (lazily created)
    _approve_use_case: ApproveTweetUseCase | None = None
    _reject_use_case: RejectTweetUseCase | None = None
    _smoke_use_case: SmokeTestUseCase | None = None
    _ingest_use_case: ManualIngestUseCase | None = None
    _scan_use_case: ScanAndNotifyUseCase | None = None

    # Orchestration components (lazily created)
    _discord_gateway: DiscordGateway | None = None
    _scheduler: ScanScheduler | None = None

    @classmethod
    def create(cls, config: Config) -> "Container":
        """
        Create a container with all dependencies wired up.

        Args:
            config: Application configuration

        Returns:
            Configured Container instance
        """
        # Create repository
        repository = SqliteTweetRepository(config.db_path)

        # Create search provider
        search_provider = SearchProviderFactory.create(config)

        # Create classifier (if needed)
        classifier: AIClassifier | None = None
        if config.search_provider not in ("xai_x_search", "manual_only"):
            if config.gemini_api_key:
                classifier = GeminiClassifier(
                    api_key=config.gemini_api_key,
                    model=config.gemini_model,
                )
            else:
                classifier = NullClassifier()

        # Create notification services
        notification_service = DiscordBot(config, repository)

        telegram_notifier: TelegramNotifier | None = None
        if config.telegram_enabled:
            telegram_notifier = TelegramNotifier(config)

        # Create xAI client for the modular x_search path
        xai_client: XaiClient | None = None
        if config.search_provider == "xai_x_search":
            xai_client = XaiClient(
                api_key=config.xai_api_key,
                timeout_seconds=config.xai_request_timeout_seconds,
                enable_prompt_caching=config.xai_enable_prompt_caching,
                prompt_cache_namespace=config.xai_prompt_cache_namespace,
                max_retries=config.xai_max_retries,
                backoff_base_seconds=config.xai_backoff_base_seconds,
                primary_default_model=DEFAULT_XAI_MODEL,
                fallback_model=DEFAULT_XAI_MODEL_FALLBACK,
            )

        # Create X poster
        x_poster = XPoster(
            csrf_token=config.x_csrf_token,
            cookie=config.x_cookie,
            dry_run=config.x_posting_dry_run,
        )

        return cls(
            config=config,
            repository=repository,
            search_provider=search_provider,
            classifier=classifier,
            notification_service=notification_service,
            telegram_notifier=telegram_notifier,
            x_poster=x_poster,
            xai_client=xai_client,
        )

    @property
    def approve_use_case(self) -> ApproveTweetUseCase:
        """Get or create the approve tweet use case."""
        if self._approve_use_case is None:
            self._approve_use_case = ApproveTweetUseCase(
                repository=self.repository,
                x_poster=self.x_poster,
                notification_service=self.notification_service,
            )
        return self._approve_use_case

    @property
    def reject_use_case(self) -> RejectTweetUseCase:
        """Get or create the reject tweet use case."""
        if self._reject_use_case is None:
            self._reject_use_case = RejectTweetUseCase(
                repository=self.repository,
                notification_service=self.notification_service,
            )
        return self._reject_use_case

    @property
    def smoke_use_case(self) -> SmokeTestUseCase:
        """Get or create the smoke test use case."""
        if self._smoke_use_case is None:
            self._smoke_use_case = SmokeTestUseCase(
                repository=self.repository,
                notification_service=self.notification_service,
            )
        return self._smoke_use_case

    @property
    def ingest_use_case(self) -> ManualIngestUseCase:
        """Get or create the manual ingest use case."""
        if self._ingest_use_case is None:
            self._ingest_use_case = ManualIngestUseCase(
                repository=self.repository,
                notification_service=self.notification_service,
            )
        return self._ingest_use_case

    @property
    def scan_use_case(self) -> ScanAndNotifyUseCase:
        """Get or create the scan and notify use case."""
        if self._scan_use_case is None:
            self._scan_use_case = ScanAndNotifyUseCase(
                config=self.config,
                repository=self.repository,
                search_provider=self.search_provider,
                classifier=self.classifier,
                notification_service=self.notification_service,
                runtime=self.runtime,
                xai_client=self.xai_client,
            )
        return self._scan_use_case

    @property
    def discord_gateway(self) -> DiscordGateway:
        """Get or create the Discord gateway."""
        if self._discord_gateway is None:
            self._discord_gateway = DiscordGateway(
                config=self.config,
                repository=self.repository,
                approve_use_case=self.approve_use_case,
                reject_use_case=self.reject_use_case,
                smoke_use_case=self.smoke_use_case,
                ingest_use_case=self.ingest_use_case,
                runtime=self.runtime,
            )
        return self._discord_gateway

    @property
    def scheduler(self) -> ScanScheduler:
        """Get or create the scan scheduler."""
        if self._scheduler is None:
            self._scheduler = ScanScheduler(
                config=self.config,
                scan_use_case=self.scan_use_case,
                notification_service=self.notification_service,
                repository=self.repository,
                runtime=self.runtime,
            )
        return self._scheduler

    def close(self) -> None:
        """Clean up resources."""
        if hasattr(self.repository, 'close'):
            self.repository.close()
