"""
API and external service exception classes.

Defines exceptions for external service interactions including
rate limiting, authentication failures, and service-specific errors.
"""

from twitter_intel.exceptions.base import TwitterIntelError


class ExternalServiceError(TwitterIntelError):
    """
    Base exception for external service errors.

    Attributes:
        service: Name of the external service that failed
        retry_after_seconds: Optional hint for when to retry (in seconds)
    """
    service: str = "unknown"
    retry_after_seconds: int | None = None


class RateLimitError(ExternalServiceError):
    """
    Raised when an external service rate limit is exceeded.

    The retry_after_seconds attribute indicates when the client
    should attempt the request again.
    """

    def __init__(
        self,
        service: str,
        message: str | None = None,
        retry_after_seconds: int | None = None,
    ):
        self.service = service
        self.retry_after_seconds = retry_after_seconds
        msg = message or f"{service} rate limit exceeded"
        if retry_after_seconds:
            msg += f" (retry after {retry_after_seconds}s)"
        super().__init__(msg)


class AuthenticationError(ExternalServiceError):
    """
    Raised when authentication to an external service fails.

    This typically indicates invalid or expired credentials.
    """

    def __init__(self, service: str, message: str | None = None):
        self.service = service
        super().__init__(message or f"{service} authentication failed")


# ---------------------------------------------------------------------------
# TwitterAPI.io specific exceptions
# ---------------------------------------------------------------------------

class TwitterApiIoAuthError(AuthenticationError):
    """
    Raised when TwitterAPI.io authentication fails.

    This indicates the API key is invalid or expired.
    """

    def __init__(self, message: str | None = None):
        super().__init__(
            service="twitterapi.io",
            message=message or "TwitterAPI.io authentication failed - check API key",
        )


class TwitterApiIoRateLimitError(RateLimitError):
    """
    Raised when TwitterAPI.io rate limit is exceeded.

    The retry_after_seconds is typically parsed from the Retry-After header.
    """

    def __init__(self, message: str | None = None, retry_after_seconds: int | None = None):
        super().__init__(
            service="twitterapi.io",
            message=message or "TwitterAPI.io rate limit exceeded",
            retry_after_seconds=retry_after_seconds,
        )


# ---------------------------------------------------------------------------
# xAI specific exceptions
# ---------------------------------------------------------------------------

class XaiAuthError(AuthenticationError):
    """
    Raised when xAI API authentication fails.

    This indicates the API key is invalid or expired.
    """

    def __init__(self, message: str | None = None):
        super().__init__(
            service="xAI",
            message=message or "xAI authentication failed - check API key",
        )


class XaiRateLimitError(RateLimitError):
    """
    Raised when xAI API rate limit is exceeded.

    The retry_after_seconds may be provided by the API response.
    """

    def __init__(self, message: str | None = None, retry_after_seconds: int | None = None):
        super().__init__(
            service="xAI",
            message=message or "xAI rate limit exceeded",
            retry_after_seconds=retry_after_seconds,
        )
