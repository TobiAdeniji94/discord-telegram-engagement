"""
Search query definitions for Twitter Intelligence Bot.

Defines the SearchQuery dataclass and default search queries for
monitoring competitor complaints, solution seekers, and brand mentions.
"""

from dataclasses import dataclass


@dataclass
class SearchQuery:
    """
    A search query configuration with category context.

    Attributes:
        query: Twitter/X search query string
        category_hint: Helps AI classify the intent (competitor_complaint, solution_seeker, brand_mention)
        description: Human-readable description of what this query targets
        query_type: "Top" for popular tweets, "Latest" for recent tweets
        cooldown_seconds: Minimum time between executions of this query
        max_pages: Maximum pages to fetch (1 page = ~20 tweets)
        enabled: Whether this query is active
    """
    query: str
    category_hint: str
    description: str
    query_type: str = "Top"
    cooldown_seconds: int = 3600
    max_pages: int = 1
    enabled: bool = True


# Default search queries organized by business intent
# Updated March 2026 - expanded competitor coverage, improved operators
DEFAULT_SEARCH_QUERIES: list[SearchQuery] = [
    # --- COMPETITOR COMPLAINTS ---
    SearchQuery(
        query=(
            '("chipper" OR "chipper cash" OR "raenest" OR "cleva" OR "grey.co" OR '
            '"greyfinance" OR "lemfi" OR "eversend" OR "geegpay" OR "nala" OR '
            '"sendwave" OR "afriex" OR "wise" OR "remitly") '
            "(down OR failed OR issue OR complaint OR slow OR fees OR pending OR "
            "stuck OR transaction OR processing OR waiting OR deposit OR fix OR "
            "verify OR verification OR otp OR tired OR frustrated OR blocked OR "
            "declined OR chargeback OR fraud OR mcc OR restricted) "
            "lang:en -is:retweet min_faves:1"
        ),
        category_hint="competitor_complaint",
        description="Africa fintech competitor complaints (2026 expanded)",
        query_type="Latest",
        cooldown_seconds=900,
        max_pages=2,
    ),
    SearchQuery(
        query=(
            '("virtual card" OR "usd card" OR "dollar card" OR "grey card" OR '
            '"virtual dollar") '
            "(failed OR declined OR restricted OR mcc OR blocked OR chargeback OR "
            'fraud OR pending OR stuck OR withdrawal OR "can\'t pay" OR zoom OR '
            'shopify OR "international payment") '
            "(nigeria OR ghana OR naira OR NGN OR cedi OR GHS) "
            "lang:en -is:retweet min_faves:1"
        ),
        category_hint="competitor_complaint",
        description="Virtual USD card failure & MCC complaints",
        query_type="Latest",
        cooldown_seconds=600,
    ),
    SearchQuery(
        query=(
            '("greyfinance" OR "@greyfinance" OR "grey.co") '
            "(fix OR verify OR verification OR transaction OR tired OR issue OR "
            'pending OR failed OR blocked OR declined OR kyc OR "account frozen" '
            "OR chargeback OR withdrawal) "
            "lang:en -is:retweet min_faves:1 -from:greyfinance"
        ),
        category_hint="competitor_complaint",
        description="Grey verification and account issues",
        query_type="Latest",
        cooldown_seconds=600,
    ),
    SearchQuery(
        query=(
            '("chipper" OR "chipper cash") '
            "(down OR failed OR issue OR slow OR fees OR pending OR stuck OR "
            "deposit OR withdrawal OR frozen OR blocked) "
            "lang:en -is:retweet min_faves:1"
        ),
        category_hint="competitor_complaint",
        description="Chipper Cash service issues",
        query_type="Latest",
        cooldown_seconds=900,
    ),
    SearchQuery(
        query=(
            '("bank" OR "naira card" OR "dollar payment") '
            "(back OR resumed OR working) "
            '(grey OR lemfi OR chipper OR "virtual card" OR fintech) '
            '(better OR switch OR "no need") '
            "lang:en -is:retweet"
        ),
        category_hint="competitor_complaint",
        description="Traditional bank vs fintech churn risk",
        query_type="Latest",
        cooldown_seconds=900,
    ),
    # --- SOLUTION SEEKERS ---
    SearchQuery(
        query=(
            '("receive usd" OR "receive dollars" OR "freelancer" OR "remote work" '
            'OR "upwork" OR "fiverr") '
            "(nigeria OR ghana OR africa) "
            "(issue OR problem OR best OR recommend OR which OR how) "
            "lang:en -is:retweet min_faves:1"
        ),
        category_hint="solution_seeker",
        description="Freelancers seeking USD receive solutions",
        query_type="Latest",
        cooldown_seconds=300,
        max_pages=2,
    ),
    SearchQuery(
        query=(
            '("send money" OR "transfer money" OR "cross border") '
            "(nigeria OR ghana OR africa OR naira) "
            '(best OR cheapest OR fastest OR recommend OR "which app" OR '
            '"looking for") '
            "lang:en -is:retweet min_faves:1"
        ),
        category_hint="solution_seeker",
        description="People seeking cross-border payment solutions",
        query_type="Latest",
        cooldown_seconds=300,
        max_pages=2,
    ),
    SearchQuery(
        query=(
            '("dollar card" OR "virtual card" OR "usd card") '
            "(nigeria OR ghana OR africa) "
            "(best OR recommend OR which OR need OR looking OR where) "
            "lang:en -is:retweet min_faves:1"
        ),
        category_hint="solution_seeker",
        description="People seeking virtual dollar cards",
        query_type="Latest",
        cooldown_seconds=300,
        max_pages=2,
    ),
    SearchQuery(
        query=(
            '("grey" OR "greyfinance" OR "grey.co") '
            '(good OR best OR love OR recommend OR "works well" OR alternative '
            "OR switch OR better) "
            "(usd OR dollar OR card OR payout OR freelancer) "
            "lang:en -is:retweet"
        ),
        category_hint="solution_seeker",
        description="Positive Grey mentions + switch intent",
        query_type="Latest",
        cooldown_seconds=600,
    ),
    # --- BRAND MENTIONS ---
    SearchQuery(
        query='("yara.cash" OR "yara cash") lang:en -is:retweet',
        category_hint="brand_mention",
        description="Direct brand mentions",
        query_type="Latest",
        cooldown_seconds=300,
    ),
    SearchQuery(
        query=(
            '("yara" OR "yara.cash") '
            '(fintech OR "dollar card" OR "send money" OR nigeria OR africa) '
            "lang:en -is:retweet"
        ),
        category_hint="brand_mention",
        description="Brand awareness volume tracking",
        query_type="Top",
        cooldown_seconds=3600,
    ),
]
