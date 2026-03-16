"""
Search provider factory.

Creates search provider instances based on configuration.
"""

from typing import TYPE_CHECKING

from twitter_intel.domain.interfaces.search_provider import SearchProvider
from twitter_intel.exceptions import ConfigurationError
from twitter_intel.infrastructure.search.twitterapi_io import TwitterApiIoClient

if TYPE_CHECKING:
    from twitter_intel.config import Config


class NullSearchProvider(SearchProvider):
    """
    Null search provider for manual-only mode.

    Returns empty results for all searches. Used when search
    is disabled and only manual ingestion is allowed.
    """

    @property
    def name(self) -> str:
        return "manual_only"

    async def search(self, query: str, query_type: str = "Top", **kwargs) -> dict:
        return {"tweets": []}

    async def get_user_mentions(self, username: str) -> dict:
        return {"tweets": []}


class SearchProviderFactory:
    """
    Factory for creating search provider instances.

    Supports multiple provider types:
    - twitterapi_io: TwitterAPI.io service
    - xai_x_search: xAI with x_search tool (special case - not a standard provider)
    - twscrape: Legacy Twitter scraper
    - manual_only: No search, manual ingestion only
    """

    @staticmethod
    def create(config: "Config") -> SearchProvider:
        """
        Create a search provider based on configuration.

        Args:
            config: Application configuration

        Returns:
            SearchProvider instance

        Raises:
            ConfigurationError: If provider type is unknown or misconfigured
        """
        provider_type = config.search_provider.lower().strip()

        if provider_type == "twitterapi_io":
            if not config.twitterapi_io_api_key:
                raise ConfigurationError(
                    "TWITTERAPI_IO_API_KEY required for twitterapi_io provider"
                )
            return TwitterApiIoClient(config.twitterapi_io_api_key)

        elif provider_type == "manual_only":
            return NullSearchProvider()

        elif provider_type == "xai_x_search":
            # xAI is a special case - it combines search and classification
            # Return null provider; actual xAI usage is handled separately
            return NullSearchProvider()

        elif provider_type == "twscrape":
            # twscrape requires special async setup
            # Return null provider; actual setup is done elsewhere
            return NullSearchProvider()

        else:
            raise ConfigurationError(
                f"Unknown search provider: {provider_type}. "
                f"Valid options: twitterapi_io, xai_x_search, twscrape, manual_only"
            )

    @staticmethod
    def get_supported_providers() -> list[str]:
        """Get list of supported provider names."""
        return ["twitterapi_io", "xai_x_search", "twscrape", "manual_only"]
