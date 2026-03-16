"""
AI classifier interface.

Defines the abstract contract for tweet classification and reply generation.
"""

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from twitter_intel.domain.entities.tweet import TweetCandidate


class AIClassifier(ABC):
    """
    Abstract interface for AI-powered tweet classification.

    This interface defines the contract for classifying tweets and
    generating reply options. Implementations may use Gemini, GPT,
    or other AI models.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Get the classifier name for logging and identification.

        Returns:
            Classifier name (e.g., "gemini", "gpt4")
        """
        pass

    @abstractmethod
    async def classify_and_generate(
        self,
        tweet: "TweetCandidate",
        brand_context: str,
        num_reply_options: int = 4,
    ) -> dict[str, Any] | None:
        """
        Classify a tweet and generate reply options.

        Args:
            tweet: The tweet to classify
            brand_context: Brand positioning and tone guidelines
            num_reply_options: Number of reply options to generate

        Returns:
            Analysis dict with:
            - category: competitor-complaints|solution-seekers|brand-mentions|irrelevant
            - sentiment: positive|negative|neutral|mixed
            - confidence: 0.0-1.0
            - replies: List of reply options with tone, text, strategy

            Returns None if classification fails.
        """
        pass
