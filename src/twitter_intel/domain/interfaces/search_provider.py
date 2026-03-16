"""
Search provider interface.

Defines the abstract contract for tweet search operations.
"""

from abc import ABC, abstractmethod
from typing import Any


class SearchProvider(ABC):
    """
    Abstract interface for tweet search providers.

    This interface defines the contract that any tweet search implementation
    must fulfill. Implementations may use TwitterAPI.io, xAI, twscrape, etc.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Get the provider name for logging and identification.

        Returns:
            Provider name (e.g., "twitterapi_io", "xai_x_search")
        """
        pass

    @abstractmethod
    async def search(
        self,
        query: str,
        query_type: str = "Top",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Execute a search query and return raw results.

        Args:
            query: The search query string
            query_type: Type of search ("Top" or "Latest")
            **kwargs: Provider-specific options

        Returns:
            Raw search results from the provider

        Raises:
            AuthenticationError: If authentication fails
            RateLimitError: If rate limit is exceeded
        """
        pass

    @abstractmethod
    async def get_user_mentions(self, username: str) -> dict[str, Any]:
        """
        Get mentions for a specific user.

        Args:
            username: The username to get mentions for (without @)

        Returns:
            Raw mention results from the provider

        Raises:
            AuthenticationError: If authentication fails
            RateLimitError: If rate limit is exceeded
        """
        pass
