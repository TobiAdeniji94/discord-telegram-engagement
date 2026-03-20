"""
Structured search lane definitions for Twitter Intelligence Bot.

Defines the SearchQuery dataclass and the default core-brand lanes used by
the xAI x_search workflow.
"""

from dataclasses import dataclass, field
import re


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


DEFAULT_SEARCH_QUERIES: list[SearchQuery] = [
    _lane(
        lane_id="complaint-chipper",
        description="Chipper complaints from real users",
        category_hint="competitor_complaint",
        intent_summary=(
            "Find recent English complaints from real users about Chipper or Chipper Cash."
        ),
        brand_family="chipper",
        brand_aliases=["Chipper", "Chipper Cash"],
        brand_handles=["chippercashapp"],
        issue_focus=COMMON_COMPLAINT_ISSUES,
        geo_focus=AFRICA_GEO,
        cooldown_seconds=900,
        priority=10,
        query_type="Latest",
    ),
    _lane(
        lane_id="complaint-grey",
        description="Grey complaints from real users",
        category_hint="competitor_complaint",
        intent_summary="Find recent English complaints from real users about Grey.",
        brand_family="grey",
        brand_aliases=["Grey", "greyfinance", "grey.co"],
        brand_handles=["greyfinance", "greyfinanceEA", "greyfinanceMENA"],
        issue_focus=COMMON_COMPLAINT_ISSUES + ["onboarding and KYC friction"],
        geo_focus=AFRICA_GEO,
        cooldown_seconds=900,
        priority=11,
        query_type="Latest",
    ),
    _lane(
        lane_id="complaint-lemfi",
        description="LemFi complaints from real users",
        category_hint="competitor_complaint",
        intent_summary="Find recent English complaints from real users about LemFi.",
        brand_family="lemfi",
        brand_aliases=["LemFi", "Lemfi"],
        brand_handles=["UseLemfi"],
        issue_focus=COMMON_COMPLAINT_ISSUES,
        geo_focus=AFRICA_GEO,
        cooldown_seconds=900,
        priority=12,
        query_type="Latest",
    ),
    _lane(
        lane_id="complaint-raenest",
        description="Raenest and Geegpay complaints from real users",
        category_hint="competitor_complaint",
        intent_summary=(
            "Find recent English complaints from real users about Raenest or Geegpay."
        ),
        brand_family="raenest",
        brand_aliases=["Raenest", "Geegpay"],
        brand_handles=["RaenestApp", "RaenestHQ"],
        issue_focus=COMMON_COMPLAINT_ISSUES,
        geo_focus=AFRICA_GEO,
        cooldown_seconds=900,
        priority=13,
        query_type="Latest",
    ),
    _lane(
        lane_id="complaint-wise",
        description="Wise complaints from real users",
        category_hint="competitor_complaint",
        intent_summary="Find recent English complaints from real users about Wise.",
        brand_family="wise",
        brand_aliases=["Wise", "TransferWise"],
        brand_handles=["Wise"],
        issue_focus=COMMON_COMPLAINT_ISSUES + ["corridor-specific pricing complaints"],
        geo_focus=AFRICA_GEO,
        cooldown_seconds=900,
        priority=14,
        query_type="Latest",
    ),
    _lane(
        lane_id="complaint-cleva",
        description="Cleva complaints from real users",
        category_hint="competitor_complaint",
        intent_summary="Find recent English complaints from real users about Cleva.",
        brand_family="cleva",
        brand_aliases=["Cleva"],
        brand_handles=["clevabanking"],
        issue_focus=COMMON_COMPLAINT_ISSUES,
        geo_focus=AFRICA_GEO,
        cooldown_seconds=900,
        priority=15,
        query_type="Latest",
    ),
    _lane(
        lane_id="complaint-remitly",
        description="Remitly complaints from real users",
        category_hint="competitor_complaint",
        intent_summary="Find recent English complaints from real users about Remitly.",
        brand_family="remitly",
        brand_aliases=["Remitly"],
        brand_handles=["remitly", "remitlysupport"],
        issue_focus=COMMON_COMPLAINT_ISSUES + ["exchange-rate frustration"],
        geo_focus=AFRICA_GEO,
        cooldown_seconds=900,
        priority=16,
        query_type="Latest",
    ),
    _lane(
        lane_id="seekers-payments-and-conversion",
        description="Solution seekers for transfers, conversion, crypto off-ramp, and virtual cards",
        category_hint="solution_seeker",
        intent_summary=(
            "Find recent English posts from people in Africa looking for better ways to "
            "send or receive money, convert fiat or crypto, cash out crypto, or use "
            "reliable virtual dollar cards."
        ),
        issue_focus=[
            "best app recommendations",
            "freelancer payouts",
            "USD receiving options",
            "cross-border transfer alternatives",
            "fiat or crypto conversion",
            "crypto off-ramp and cash-out options",
            "virtual USD cards",
            "international online payments",
        ],
        geo_focus=AFRICA_GEO,
        cooldown_seconds=1800,
        priority=30,
        query_type="Latest",
    ),
]
