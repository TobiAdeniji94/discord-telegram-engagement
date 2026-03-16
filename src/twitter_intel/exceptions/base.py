"""
Base exception classes for Twitter Intelligence Bot.

Defines the root exception hierarchy that all other exceptions inherit from.
"""


class TwitterIntelError(Exception):
    """
    Base exception for all Twitter Intelligence Bot errors.

    All custom exceptions in the application should inherit from this class
    to enable catching all application-specific errors with a single except clause.
    """
    pass


class ConfigurationError(TwitterIntelError):
    """
    Raised when there is an invalid or missing configuration.

    Examples:
    - Missing required environment variable
    - Invalid value for a configuration option
    - Incompatible configuration combination
    """
    pass


class ValidationError(TwitterIntelError):
    """
    Raised when domain validation fails.

    Examples:
    - Invalid tweet data format
    - Missing required fields
    - Value out of acceptable range
    """
    pass
