"""
Exception hierarchy for Twitter Intelligence Bot.

Provides a structured exception system with base classes and
specific exceptions for different error scenarios.
"""

from twitter_intel.exceptions.base import (
    TwitterIntelError,
    ConfigurationError,
    ValidationError,
)
from twitter_intel.exceptions.api_errors import (
    ExternalServiceError,
    RateLimitError,
    AuthenticationError,
    TwitterApiIoAuthError,
    TwitterApiIoRateLimitError,
    XaiAuthError,
    XaiRateLimitError,
)

__all__ = [
    # Base exceptions
    "TwitterIntelError",
    "ConfigurationError",
    "ValidationError",
    # External service errors
    "ExternalServiceError",
    "RateLimitError",
    "AuthenticationError",
    # TwitterAPI.io specific
    "TwitterApiIoAuthError",
    "TwitterApiIoRateLimitError",
    # xAI specific
    "XaiAuthError",
    "XaiRateLimitError",
]
