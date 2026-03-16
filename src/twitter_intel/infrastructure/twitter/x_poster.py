"""
X/Twitter reply poster.

Posts replies to X using the GraphQL CreateTweet API.
"""

import logging
from typing import Any

import httpx

log = logging.getLogger("twitter_intel.twitter.x_poster")

# X's public bearer token (used for all authenticated requests)
X_BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# GraphQL endpoint for creating tweets
X_CREATE_TWEET_URL = "https://x.com/i/api/graphql/znq7jUAqRjmPj7IszLem5Q/CreateTweet"
X_CREATE_TWEET_QUERY_ID = "znq7jUAqRjmPj7IszLem5Q"

# Feature flags required by X's GraphQL API
X_TWEET_FEATURES: dict[str, bool] = {
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "articles_preview_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}


def is_local_test_tweet_id(tweet_id: str) -> bool:
    """Check if a tweet ID is a local test (smoke or manual ingest)."""
    return tweet_id.startswith(("smoke-", "manual-"))


class XPoster:
    """
    Posts replies to X/Twitter using the GraphQL API.

    Handles authentication via CSRF token and cookie, and supports
    dry-run mode for testing without actually posting.
    """

    def __init__(
        self,
        csrf_token: str,
        cookie: str,
        dry_run: bool = False,
    ):
        """
        Initialize the X poster.

        Args:
            csrf_token: X-CSRF-Token from authenticated session
            cookie: Cookie header from authenticated session
            dry_run: If True, log posts but don't actually send them
        """
        self._csrf_token = csrf_token
        self._cookie = cookie
        self._dry_run = dry_run

    @property
    def is_configured(self) -> bool:
        """Check if the poster has valid credentials configured."""
        return bool(self._csrf_token and self._cookie)

    def _build_headers(self) -> dict[str, str]:
        """Build request headers for the X API."""
        return {
            "authorization": f"Bearer {X_BEARER_TOKEN}",
            "x-csrf-token": self._csrf_token,
            "cookie": self._cookie,
            "content-type": "application/json",
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
        }

    def _build_payload(self, tweet_id: str, reply_text: str) -> dict[str, Any]:
        """Build the GraphQL mutation payload."""
        return {
            "variables": {
                "tweet_text": reply_text,
                "reply": {
                    "in_reply_to_tweet_id": tweet_id,
                    "exclude_reply_user_ids": [],
                },
                "dark_request": False,
                "media": {
                    "media_entities": [],
                    "possibly_sensitive": False,
                },
                "semantic_annotation_ids": [],
            },
            "features": X_TWEET_FEATURES,
            "queryId": X_CREATE_TWEET_QUERY_ID,
        }

    async def post_reply(self, tweet_id: str, reply_text: str) -> bool:
        """
        Post a reply to a tweet on X.

        Args:
            tweet_id: The ID of the tweet to reply to
            reply_text: The text of the reply (max 280 chars)

        Returns:
            True if the reply was posted (or would be in dry-run), False on error
        """
        # Handle dry-run and local test tweets
        if self._dry_run or is_local_test_tweet_id(tweet_id):
            log.info("Dry-run X reply for %s: %s", tweet_id, reply_text)
            return True

        # Check credentials
        if not self.is_configured:
            log.error("X posting credentials not configured")
            return False

        headers = self._build_headers()
        payload = self._build_payload(tweet_id, reply_text)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    X_CREATE_TWEET_URL,
                    headers=headers,
                    json=payload,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    if "errors" in data:
                        log.error("X API errors: %s", data["errors"])
                        return False
                    log.info("Reply posted to %s", tweet_id)
                    return True
                else:
                    log.error("X reply failed with status %d", resp.status_code)
                    return False

        except httpx.RequestError as exc:
            log.error("X request error: %s", exc)
            return False
        except Exception as exc:
            log.error("X posting error: %s", exc)
            return False
