"""Input model, output model, and PPE constants for Reddit Brand / GEO Monitor.

This actor watches Reddit for what people say about a brand, scores each
mention (sentiment, buzz, mention type, and a GEO signal for AI-search
visibility), and in monitor mode emits only mentions new since the last run.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MentionType = Literal[
    "review",
    "comparison",
    "complaint",
    "recommendation",
    "question",
    "discussion",
]

# ---------------------------------------------------------------------------
# PPE billing constants. See mention_gate.py for the single enforcement point.
# Model: a small scan fee per run that actually scraped, plus a per-mention
# fee for each NEW mention delivered. Empty/blocked runs deliver nothing and
# are never charged. Pricing lives in the Apify Console monetization config;
# these values must match it for the max-charge cap math to be accurate.
# ---------------------------------------------------------------------------
EVENT_SCAN_PERFORMED = "scan-performed"  # once per run that scraped
EVENT_NEW_MENTION = "new-mention"        # once per delivered new mention

PRICE_SCAN_USD = 0.05
PRICE_PER_MENTION_USD = 0.02

# TRUTH RULES (enforced in mention_gate.py + main.py, not configurable):
#   1. scan-performed is charged once, only if the scrape delivered >=1 raw
#      post. Total block / hard failure delivers nothing => no charge.
#   2. new-mention is charged exactly once per pushed dataset item, only for
#      mentions genuinely new since the last run (delta mode) and above the
#      buzz threshold. We never charge for a mention we did not deliver, and
#      never twice for the same mention across runs.
CHARGE_ONLY_ON_DELIVERED = True  # do not make this configurable


class BrandMonitorInput(BaseModel):
    """Validated actor input."""

    # Brand terms to track. May be inline OR via a dataset / CSV url (see
    # keywordsDatasetId / keywordsFileUrl). Kept under the name `keywords` so
    # the vendored scraper client and keyword resolver are reused unchanged.
    keywords: list[str] = Field(default_factory=list)
    keywordsDatasetId: str | None = None
    keywordsFileUrl: str | None = None
    competitors: list[str] = Field(default_factory=list)
    subreddits: list[str] = Field(default_factory=list)

    # Optional context sentence describing the brand, used only to disambiguate
    # common-word brand names via the semantic gate (e.g. "Notion the
    # productivity app", not the dictionary word). Empty => gate is skipped.
    brandContext: str = ""

    timeFilter: Literal["day", "week", "month", "year", "all"] = "week"
    # Monitor mode: when true, only emit mentions not seen on a previous run
    # (state persists in a named key-value store). Turn off for a one-shot pull.
    sinceLastRun: bool = True
    minBuzz: int = 0
    maxMentions: int = 100

    scoringMode: Literal["lexicon", "llm"] = "lexicon"
    openaiApiKey: str | None = None
    proxyConfiguration: dict[str, Any] | None = None


class Mention(BaseModel):
    """One brand mention. Shape matches the dataset schema."""

    mentionId: str
    type: Literal["post", "comment"]
    matchedTerms: list[str]
    competitorMentioned: str | None = None
    sentiment: Literal["positive", "negative", "neutral"] = "neutral"
    mentionType: MentionType = "discussion"
    buzzScore: int = 0
    geoSignal: int = 0
    geoTier: Literal["high", "medium", "low"] = "low"
    suggestedPriority: Literal["respond", "watch", "ignore"] = "watch"
    title: str = ""
    snippet: str = ""
    url: str = ""
    subreddit: str = ""
    author: str = "[deleted]"
    score: int | None = None
    numComments: int | None = None
    createdAt: str | None = None
    ageHours: float | None = None
