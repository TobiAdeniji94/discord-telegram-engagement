"""
Structured search lane definitions for Twitter Intelligence Bot.

Defines the SearchQuery dataclass and the default core-brand lanes used by
the xAI x_search workflow.

Implements SRS-YARA-XSS-2026 Section 4 System Features:
- Section 4.1: Competitor Complaint Retrieval
- Section 4.2: Solution-Seeker Discovery
- Section 4.1.2: Prompt Design Requirements (max 500 chars, natural language)
"""

from dataclasses import dataclass, field
import re

from twitter_intel.config.brand_registry import (
    BRAND_REGISTRY,
    BrandConfig,
    get_brand,
)


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _coerce_text_list(raw_value: object, *, strip_at: bool = False) -> list[str]:
    if raw_value is None:
        return []

    if isinstance(raw_value, str):
        values = [part.strip() for part in raw_value.split(",")]
    elif isinstance(raw_value, (list, tuple, set)):
        values = [str(item or "").strip() for item in raw_value]
    else:
        values = [str(raw_value).strip()]

    cleaned: list[str] = []
    for value in values:
        text = value.lstrip("@") if strip_at else value
        if text:
            cleaned.append(text)
    return _unique_strings(cleaned)


def _slugify(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower())
    return normalized.strip("-")


def _lane(
    *,
    lane_id: str,
    description: str,
    category_hint: str,
    intent_summary: str,
    brand_family: str = "",
    brand_aliases: list[str] | None = None,
    brand_handles: list[str] | None = None,
    issue_focus: list[str] | None = None,
    geo_focus: list[str] | None = None,
    cooldown_seconds: int = 900,
    priority: int = 50,
    query_type: str = "Latest",
    query: str = "",
    strategy_mode: str = "always_on",
) -> "SearchQuery":
    return SearchQuery(
        query=query or intent_summary,
        category_hint=category_hint,
        description=description,
        query_type=query_type,
        cooldown_seconds=cooldown_seconds,
        max_pages=1,
        enabled=True,
        lane_id=lane_id,
        intent_summary=intent_summary,
        brand_family=brand_family,
        brand_aliases=brand_aliases or [],
        brand_handles=brand_handles or [],
        exclude_author_handles=brand_handles or [],
        issue_focus=issue_focus or [],
        geo_focus=geo_focus or [],
        priority=priority,
        strategy_mode=strategy_mode,
    )


COMMON_COMPLAINT_ISSUES = [
    "pending or stuck transfers",
    "failed transfers",
    "declined card payments",
    "blocked or restricted accounts",
    "verification or OTP problems",
    "app or login failures",
    "unexpected fees or charges",
    "poor customer support",
]

AFRICA_GEO = ["Nigeria", "Ghana", "Africa"]

DELAY_COMPLAINT_ISSUES = [
    "pending or stuck transfers",
    "delayed deposits or withdrawals",
    "processing and waiting complaints",
    "slow payouts",
]

PAYMENT_COMPLAINT_ISSUES = [
    "failed transfers",
    "declined card payments",
    "unexpected fees or charges",
    "payment failures",
]

ACCESS_COMPLAINT_ISSUES = [
    "blocked or restricted accounts",
    "verification or OTP problems",
    "app or login failures",
    "poor customer support",
]

COMPLAINT_QUERY_TERMS = [
    "failed",
    "issue",
    "problem",
    "error",
    "pending",
    "stuck",
    "delayed",
    "slow",
    "declined",
    "blocked",
    "restricted",
    "verification",
    "verify",
    "otp",
    "support",
    "refund",
    "chargeback",
    "fees",
    "charges",
    "transfer",
    "deposit",
    "withdrawal",
    "card",
    "login",
    "bug",
    "processing",
]

SOLUTION_SEEKER_TOPIC_TERMS = [
    "send money",
    "receive money",
    "cross-border",
    "transfer",
    "fiat",
    "crypto",
    "conversion",
    "cash out",
    "off-ramp",
    "virtual card",
    "dollar card",
    "usd card",
    "stablecoin",
    "usdt",
    "usdc",
]

SOLUTION_SEEKER_INTENT_TERMS = [
    "best",
    "recommend",
    "recommendation",
    "alternative",
    "alternatives",
    "looking",
    "need",
    "how",
    "where",
    "which",
    "issue",
    "problem",
    "failed",
    "stuck",
    "declined",
]

QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "app",
    "apps",
    "are",
    "for",
    "from",
    "how",
    "in",
    "looking",
    "of",
    "on",
    "or",
    "options",
    "people",
    "posts",
    "recent",
    "real",
    "reliable",
    "seeking",
    "seekers",
    "send",
    "the",
    "to",
    "users",
    "ways",
}


def _looks_like_explicit_query(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False

    markers = (
        "lang:",
        "since:",
        "until:",
        "since_time:",
        "until_time:",
        "-is:",
        "min_",
        "to:",
        "from:",
        "@",
        " OR ",
    )
    return any(marker in raw for marker in markers) or ("(" in raw and ")" in raw)


def _format_query_term(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    if value.startswith(("to:", "from:", "@")):
        return value
    if re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        return value
    return f'"{value}"'


def _or_block(values: list[str]) -> str:
    cleaned = [_format_query_term(value) for value in _unique_strings(values)]
    cleaned = [value for value in cleaned if value]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return "(" + " OR ".join(cleaned) + ")"


def _keywords_from_phrases(phrases: list[str], *, limit: int = 12) -> list[str]:
    keywords: list[str] = []
    for phrase in phrases:
        text = str(phrase or "").strip()
        if not text:
            continue
        if " " in text and len(text.split()) <= 4:
            keywords.append(text)
        for token in re.findall(r"[A-Za-z0-9$+-]+", text.lower()):
            if token in QUERY_STOPWORDS:
                continue
            if len(token) >= 3 or token in {"usd", "otp", "kyc"}:
                keywords.append(token)
    return _unique_strings(keywords)[:limit]


def _brand_reference_terms(query: "SearchQuery") -> list[str]:
    terms: list[str] = []
    terms.extend(query.brand_aliases)
    for handle in query.brand_handles:
        normalized = str(handle or "").strip().lstrip("@")
        if not normalized:
            continue
        terms.append(f"@{normalized}")
        terms.append(f"to:{normalized}")
    return _unique_strings(terms)


@dataclass
class SearchQuery:
    """
    Search lane configuration.

    `query` is preserved for backward compatibility with existing env-driven
    `SEARCH_QUERIES` payloads. The structured fields are preferred by the xAI
    prompt builder.
    """

    query: str
    category_hint: str
    description: str
    query_type: str = "Top"
    cooldown_seconds: int = 3600
    max_pages: int = 1
    enabled: bool = True
    lane_id: str = ""
    intent_summary: str = ""
    brand_family: str = ""
    brand_aliases: list[str] = field(default_factory=list)
    brand_handles: list[str] = field(default_factory=list)
    exclude_author_handles: list[str] = field(default_factory=list)
    issue_focus: list[str] = field(default_factory=list)
    geo_focus: list[str] = field(default_factory=list)
    priority: int = 50
    strategy_mode: str = "always_on"

    def __post_init__(self) -> None:
        self.query = str(self.query or "").strip()
        self.category_hint = str(self.category_hint or "").strip().lower()
        self.description = str(self.description or "").strip()
        self.query_type = "Latest" if str(self.query_type or "").strip().lower() == "latest" else "Top"
        self.cooldown_seconds = max(60, int(self.cooldown_seconds or 3600))
        self.max_pages = max(1, int(self.max_pages or 1))
        self.enabled = bool(self.enabled)
        self.lane_id = str(self.lane_id or "").strip().lower()
        self.intent_summary = str(self.intent_summary or "").strip()
        self.brand_family = str(self.brand_family or "").strip().lower()
        self.brand_aliases = _coerce_text_list(self.brand_aliases)
        self.brand_handles = _coerce_text_list(self.brand_handles, strip_at=True)
        self.exclude_author_handles = _coerce_text_list(
            self.exclude_author_handles, strip_at=True
        )
        self.issue_focus = _coerce_text_list(self.issue_focus)
        self.geo_focus = _coerce_text_list(self.geo_focus)
        self.priority = int(self.priority or 50)

        strategy_mode = str(self.strategy_mode or "always_on").strip().lower()
        self.strategy_mode = (
            strategy_mode if strategy_mode in {"always_on", "anchored_event"} else "always_on"
        )

        if not self.intent_summary:
            self.intent_summary = self.description or self.query
        if not self.lane_id:
            base = self.brand_family or self.description or self.query or self.category_hint
            self.lane_id = _slugify(base)
        if not self.query:
            self.query = self.intent_summary or self.description or self.lane_id
        if not self.exclude_author_handles and self.brand_handles:
            self.exclude_author_handles = list(self.brand_handles)


def build_standard_search_query(query: SearchQuery) -> str:
    """
    Compile a structured lane into a raw keyword/operator query.

    Standard providers expect X-style keyword searches. When `query.query`
    already contains an explicit operator-based query, preserve it. Otherwise
    derive one from the lane metadata.
    """
    raw_query = str(query.query or "").strip()
    use_raw_query = (
        raw_query
        and raw_query != str(query.intent_summary or "").strip()
        and _looks_like_explicit_query(raw_query)
    )
    if use_raw_query:
        return raw_query

    geo_block = _or_block(query.geo_focus)

    if query.category_hint == "competitor_complaint" and query.brand_aliases:
        brand_block = _or_block(_brand_reference_terms(query))
        issue_terms = COMPLAINT_QUERY_TERMS + _keywords_from_phrases(query.issue_focus)
        issue_block = _or_block(issue_terms[:20])
        parts = [brand_block, issue_block]
        if geo_block:
            parts.append(geo_block)
        parts.append("lang:en -is:retweet")
        return " ".join(part for part in parts if part)

    if query.category_hint == "solution_seeker":
        topic_terms = _unique_strings(
            SOLUTION_SEEKER_TOPIC_TERMS + _keywords_from_phrases(query.issue_focus)
        )
        topic_block = _or_block(topic_terms[:18])
        intent_block = _or_block(SOLUTION_SEEKER_INTENT_TERMS)
        parts = [topic_block, intent_block]
        if geo_block:
            parts.append(geo_block)
        parts.append("lang:en -is:retweet")
        return " ".join(part for part in parts if part)

    if query.category_hint == "brand_mention" and (query.brand_aliases or query.brand_handles):
        brand_block = _or_block(_brand_reference_terms(query))
        parts = [brand_block]
        if geo_block:
            parts.append(geo_block)
        parts.append("lang:en -is:retweet")
        return " ".join(part for part in parts if part)

    return raw_query or query.intent_summary or query.description


def _brand_complaint_lanes(
    *,
    brand_family: str,
    brand_label: str,
    brand_aliases: list[str],
    brand_handles: list[str],
    priority_start: int,
    delay_extras: list[str] | None = None,
    payment_extras: list[str] | None = None,
    access_extras: list[str] | None = None,
) -> list[SearchQuery]:
    return [
        _lane(
            lane_id=f"complaint-{brand_family}-delay",
            description=f"{brand_label} delay complaints from real users",
            category_hint="competitor_complaint",
            intent_summary=(
                f"Find recent English complaints from real users about {brand_label} "
                "delays, pending transfers, stuck processing, or slow payouts."
            ),
            brand_family=brand_family,
            brand_aliases=brand_aliases,
            brand_handles=brand_handles,
            issue_focus=DELAY_COMPLAINT_ISSUES + (delay_extras or []),
            geo_focus=AFRICA_GEO,
            cooldown_seconds=900,
            priority=priority_start,
            query_type="Latest",
        ),
        _lane(
            lane_id=f"complaint-{brand_family}-payment",
            description=f"{brand_label} payment complaints from real users",
            category_hint="competitor_complaint",
            intent_summary=(
                f"Find recent English complaints from real users about {brand_label} "
                "payment failures, declined cards, or unexpected charges."
            ),
            brand_family=brand_family,
            brand_aliases=brand_aliases,
            brand_handles=brand_handles,
            issue_focus=PAYMENT_COMPLAINT_ISSUES + (payment_extras or []),
            geo_focus=AFRICA_GEO,
            cooldown_seconds=900,
            priority=priority_start + 10,
            query_type="Latest",
        ),
        _lane(
            lane_id=f"complaint-{brand_family}-access",
            description=f"{brand_label} account and support complaints from real users",
            category_hint="competitor_complaint",
            intent_summary=(
                f"Find recent English complaints from real users about {brand_label} "
                "account restrictions, verification issues, app failures, or bad support."
            ),
            brand_family=brand_family,
            brand_aliases=brand_aliases,
            brand_handles=brand_handles,
            issue_focus=ACCESS_COMPLAINT_ISSUES + (access_extras or []),
            geo_focus=AFRICA_GEO,
            cooldown_seconds=900,
            priority=priority_start + 20,
            query_type="Latest",
        ),
    ]


def _brand_lane_from_registry(brand_key: str, priority_start: int) -> list[SearchQuery]:
    """
    Generate the SRS complaint lane for a brand from the registry.

    The SRS defines one complaint retrieval call per brand per search cycle,
    with a single semantic prompt covering the supported complaint categories.

    Args:
        brand_key: Key from BRAND_REGISTRY
        priority_start: Starting priority for lane ordering

    Returns:
        List containing the single SearchQuery lane for this brand
    """
    brand = BRAND_REGISTRY.get(brand_key)
    if not brand:
        return []

    brand_label = " or ".join(brand.aliases)
    disambiguation = f" ({brand.disambiguation_context})" if brand.disambiguation_context else ""

    return [
        _lane(
            lane_id=f"complaint-{brand.brand_key}",
            description=f"{brand_label}{disambiguation} complaints from real users",
            category_hint="competitor_complaint",
            intent_summary=(
                f"Find recent English complaints from real users about "
                f"{brand_label}{disambiguation}: failed transfers, pending payments, "
                "blocked accounts, verification issues, unexpected fees, app failures, "
                "or poor customer support."
            ),
            brand_family=brand.brand_key,
            brand_aliases=list(brand.aliases),
            brand_handles=list(brand.handles),
            issue_focus=list(COMMON_COMPLAINT_ISSUES),
            geo_focus=AFRICA_GEO,
            cooldown_seconds=900,
            priority=priority_start,
            query_type="Latest",
        )
    ]


# SRS-compliant default search queries
# Uses brand registry from Section 4.1.3 Supported Brands
DEFAULT_SEARCH_QUERIES: list[SearchQuery] = [
    # Competitor complaint lanes per SRS Section 4.1
    *_brand_lane_from_registry("chipper", priority_start=10),
    *_brand_lane_from_registry("grey", priority_start=11),
    *_brand_lane_from_registry("lemfi", priority_start=12),
    *_brand_lane_from_registry("raenest", priority_start=13),
    *_brand_lane_from_registry("wise", priority_start=14),
    *_brand_lane_from_registry("cleva", priority_start=15),
    *_brand_lane_from_registry("remitly", priority_start=16),

    # Solution-seeker lanes per SRS Section 4.2
    _lane(
        lane_id="solution-seeker-usd-payments",
        description="Freelancers and remote workers seeking USD payment solutions",
        category_hint="solution_seeker",
        intent_summary=(
            "Find recent English posts from freelancers or remote workers in Nigeria, "
            "Ghana, or other African markets seeking advice or alternatives for receiving "
            "USD payments internationally."
        ),
        issue_focus=[
            "USD payment receiving",
            "freelancer payouts",
            "Upwork/Fiverr withdrawal",
            "international payment options",
            "comparing Payoneer, Wise, Grey",
        ],
        geo_focus=AFRICA_GEO,
        cooldown_seconds=900,
        priority=100,
        query_type="Latest",
    ),
]
