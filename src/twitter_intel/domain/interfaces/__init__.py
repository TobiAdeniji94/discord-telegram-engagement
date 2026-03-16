"""
Domain interfaces for Twitter Intelligence Bot.

Contains abstract base classes that define contracts for infrastructure
implementations (repositories, providers, services).
"""

from twitter_intel.domain.interfaces.ai_classifier import AIClassifier
from twitter_intel.domain.interfaces.notification_service import NotificationService
from twitter_intel.domain.interfaces.search_provider import SearchProvider
from twitter_intel.domain.interfaces.tweet_repository import TweetRepository

__all__ = ["AIClassifier", "NotificationService", "SearchProvider", "TweetRepository"]
