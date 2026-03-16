"""
Tests for twitter_intel.exceptions module.
"""

import pytest
from twitter_intel.exceptions import (
    TwitterIntelError,
    ConfigurationError,
    ValidationError,
    ExternalServiceError,
    RateLimitError,
    AuthenticationError,
    TwitterApiIoAuthError,
    TwitterApiIoRateLimitError,
    XaiAuthError,
    XaiRateLimitError,
)


class TestBaseExceptions:
    """Tests for base exception classes."""

    def test_twitter_intel_error_is_exception(self):
        """TwitterIntelError should inherit from Exception."""
        assert issubclass(TwitterIntelError, Exception)

    def test_configuration_error_inheritance(self):
        """ConfigurationError should inherit from TwitterIntelError."""
        assert issubclass(ConfigurationError, TwitterIntelError)
        error = ConfigurationError("Missing API key")
        assert isinstance(error, TwitterIntelError)

    def test_validation_error_inheritance(self):
        """ValidationError should inherit from TwitterIntelError."""
        assert issubclass(ValidationError, TwitterIntelError)
        error = ValidationError("Invalid tweet format")
        assert isinstance(error, TwitterIntelError)

    def test_can_catch_all_app_errors(self):
        """All app errors should be catchable with TwitterIntelError."""
        errors = [
            ConfigurationError("test"),
            ValidationError("test"),
            TwitterApiIoAuthError(),
            XaiRateLimitError(),
        ]
        for error in errors:
            try:
                raise error
            except TwitterIntelError:
                pass  # Should be caught
            else:
                pytest.fail(f"{type(error).__name__} not caught by TwitterIntelError")


class TestExternalServiceError:
    """Tests for ExternalServiceError and its subclasses."""

    def test_external_service_error_inheritance(self):
        """ExternalServiceError should inherit from TwitterIntelError."""
        assert issubclass(ExternalServiceError, TwitterIntelError)

    def test_rate_limit_error_basic(self):
        """RateLimitError should capture service name."""
        error = RateLimitError(service="test_service")
        assert error.service == "test_service"
        assert "test_service" in str(error)
        assert "rate limit" in str(error).lower()

    def test_rate_limit_error_with_retry_after(self):
        """RateLimitError should capture retry_after_seconds."""
        error = RateLimitError(service="test", retry_after_seconds=60)
        assert error.retry_after_seconds == 60
        assert "60" in str(error)

    def test_rate_limit_error_custom_message(self):
        """RateLimitError should accept custom message."""
        error = RateLimitError(service="test", message="Custom limit message")
        assert "Custom limit message" in str(error)

    def test_authentication_error_basic(self):
        """AuthenticationError should capture service name."""
        error = AuthenticationError(service="test_service")
        assert error.service == "test_service"
        assert "authentication" in str(error).lower()

    def test_authentication_error_custom_message(self):
        """AuthenticationError should accept custom message."""
        error = AuthenticationError(service="test", message="Invalid credentials")
        assert "Invalid credentials" in str(error)


class TestTwitterApiIoExceptions:
    """Tests for TwitterAPI.io specific exceptions."""

    def test_auth_error_defaults(self):
        """TwitterApiIoAuthError should have sensible defaults."""
        error = TwitterApiIoAuthError()
        assert error.service == "twitterapi.io"
        assert "twitterapi.io" in str(error).lower()
        assert "authentication" in str(error).lower()

    def test_auth_error_custom_message(self):
        """TwitterApiIoAuthError should accept custom message."""
        error = TwitterApiIoAuthError("Invalid key provided")
        assert "Invalid key provided" in str(error)

    def test_auth_error_inheritance(self):
        """TwitterApiIoAuthError should inherit correctly."""
        error = TwitterApiIoAuthError()
        assert isinstance(error, AuthenticationError)
        assert isinstance(error, ExternalServiceError)
        assert isinstance(error, TwitterIntelError)

    def test_rate_limit_error_defaults(self):
        """TwitterApiIoRateLimitError should have sensible defaults."""
        error = TwitterApiIoRateLimitError()
        assert error.service == "twitterapi.io"
        assert error.retry_after_seconds is None

    def test_rate_limit_error_with_retry_after(self):
        """TwitterApiIoRateLimitError should capture retry_after_seconds."""
        error = TwitterApiIoRateLimitError(retry_after_seconds=300)
        assert error.retry_after_seconds == 300
        assert "300" in str(error)

    def test_rate_limit_error_inheritance(self):
        """TwitterApiIoRateLimitError should inherit correctly."""
        error = TwitterApiIoRateLimitError()
        assert isinstance(error, RateLimitError)
        assert isinstance(error, ExternalServiceError)
        assert isinstance(error, TwitterIntelError)


class TestXaiExceptions:
    """Tests for xAI specific exceptions."""

    def test_auth_error_defaults(self):
        """XaiAuthError should have sensible defaults."""
        error = XaiAuthError()
        assert error.service == "xAI"
        assert "xai" in str(error).lower()
        assert "authentication" in str(error).lower()

    def test_auth_error_custom_message(self):
        """XaiAuthError should accept custom message."""
        error = XaiAuthError("Token expired")
        assert "Token expired" in str(error)

    def test_auth_error_inheritance(self):
        """XaiAuthError should inherit correctly."""
        error = XaiAuthError()
        assert isinstance(error, AuthenticationError)
        assert isinstance(error, ExternalServiceError)
        assert isinstance(error, TwitterIntelError)

    def test_rate_limit_error_defaults(self):
        """XaiRateLimitError should have sensible defaults."""
        error = XaiRateLimitError()
        assert error.service == "xAI"
        assert error.retry_after_seconds is None

    def test_rate_limit_error_with_retry_after(self):
        """XaiRateLimitError should capture retry_after_seconds."""
        error = XaiRateLimitError(retry_after_seconds=120)
        assert error.retry_after_seconds == 120

    def test_rate_limit_error_inheritance(self):
        """XaiRateLimitError should inherit correctly."""
        error = XaiRateLimitError()
        assert isinstance(error, RateLimitError)
        assert isinstance(error, ExternalServiceError)
        assert isinstance(error, TwitterIntelError)
