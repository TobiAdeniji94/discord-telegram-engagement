"""
AI prompt templates for classification and reply generation.

Contains all prompt templates used by AI classifiers.
"""


def build_classification_prompt(
    brand_context: str,
    author_username: str,
    author_name: str,
    tweet_text: str,
    likes: int,
    replies: int,
    retweets: int,
    views: int,
    age_minutes: float,
    search_query: str,
    category_hint: str,
    num_reply_options: int,
) -> str:
    """
    Build the classification and reply generation prompt.

    Args:
        brand_context: Brand positioning and tone guidelines
        author_username: Tweet author's username
        author_name: Tweet author's display name
        tweet_text: The tweet text content
        likes: Number of likes
        replies: Number of replies
        retweets: Number of retweets
        views: Number of views
        age_minutes: Tweet age in minutes
        search_query: The query that found this tweet
        category_hint: Suggested category based on search
        num_reply_options: Number of reply options to generate

    Returns:
        Complete prompt for the AI model
    """
    return f"""{brand_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK: Analyze this tweet and generate strategic replies for Yara.cash
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TWEET:
  Author: @{author_username} ({author_name})
  Text: "{tweet_text}"
  Engagement: {likes} likes, {replies} replies, {retweets} RTs, {views} views
  Age: {age_minutes:.0f} minutes old
  Search query that found it: "{search_query}"
  Category hint: {category_hint}

STEP 1 - CLASSIFY this tweet into exactly ONE category:
  - "competitor-complaints": User is frustrated/complaining about a competitor product
  - "solution-seekers": User is looking for a solution that yara.cash provides
  - "brand-mentions": User is talking about yara.cash directly
  - "irrelevant": Not useful for engagement (spam, unrelated, etc.)

STEP 2 - SENTIMENT: positive / negative / neutral / mixed

STEP 3 - GENERATE {num_reply_options} REPLY OPTIONS:

  For "competitor-complaints":
    - NEVER directly trash the competitor
    - Empathize with the user's frustration first
    - Subtly position yara.cash as the solution
    - Vary approaches: empathetic → helpful → cheeky-but-respectful → question-based
    - Example vibe: "That transfer anxiety is real 😤 Switched to Yara.cash last month — zero failed transfers since. Might be worth a look?"

  For "solution-seekers":
    - Directly address what they're looking for
    - Be helpful first, promotional second
    - Include a specific feature that solves their need
    - Example vibe: "For freelancer USD payments in Nigeria, check Yara.cash — virtual dollar cards + multi-currency wallet. No hidden fees on conversions."

  For "brand-mentions":
    - If positive: amplify and engage
    - If negative: address with empathy and solutions
    - If neutral: add value and personality

RULES:
  - Under 280 characters per reply
  - Sound like a real person, not a brand bot
  - Reference specifics from the tweet
  - Max 1-2 emojis per reply
  - Never start with "Hey" or "Hi there"
  - Don't use "we" excessively
  - Vary the call-to-action: some link to yara.cash, some just plant the seed

Respond ONLY in this JSON format:
{{
  "category": "competitor-complaints|solution-seekers|brand-mentions|irrelevant",
  "sentiment": "positive|negative|neutral|mixed",
  "confidence": 0.0-1.0,
  "themes": ["theme1", "theme2"],
  "urgency": "low|medium|high",
  "competitor_mentioned": "name or null",
  "yara_angle": "Brief description of how yara.cash solves this",
  "replies": [
    {{"tone": "tone_label", "text": "reply text", "strategy": "brief note on the approach"}},
    ...
  ]
}}

Return ONLY valid JSON."""


def clean_json_response(text: str) -> str:
    """
    Clean markdown fences and extra content from AI response.

    Args:
        text: Raw response text from AI model

    Returns:
        Cleaned JSON string
    """
    text = text.strip()

    # Remove markdown code fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]

    # Remove json language identifier
    if text.startswith("json"):
        text = text[4:]

    return text.strip()
