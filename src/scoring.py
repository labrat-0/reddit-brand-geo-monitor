"""Mention classification. Deterministic lexicon engine + optional LLM sentiment.

For each raw Reddit post that mentions a tracked brand term, produce a Mention
with: sentiment, mention type, a buzz score (engagement weighted by recency),
a GEO signal (how likely the thread is to surface in AI search / Google AI
Overviews), and a suggested priority. Deterministic: same post + same config
always yields the same scores. No API key required; the LLM path is an opt-in
BYOK sharpener for sentiment only.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any

from .models import BrandMonitorInput, Mention

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lexicons. Lowercase phrases that signal one dimension of a mention.
# ---------------------------------------------------------------------------
POSITIVE_WORDS = set(
    "love loved great awesome amazing excellent best fantastic perfect "
    "recommend recommended reliable solid worth happy impressed favorite "
    "smooth intuitive lifesaver".split()
)
NEGATIVE_WORDS = set(
    "hate awful terrible buggy expensive overpriced frustrated disappointed "
    "crashing broken useless worst annoying slow scam garbage clunky "
    "unreliable avoid regret".split()
)

COMPARISON_CUES = [
    " vs ", " vs.", "versus", "compared to", "comparison", "or should i",
    "better than", "instead of", "alternative to", "alternatives to",
    "switch from", "switching from", "which is better",
]
COMPLAINT_CUES = [
    "fed up with", "sick of", "hate that", "hate how", "frustrated with",
    "too expensive", "so expensive", "keeps crashing", "so buggy", "terrible",
    "stopped working", "waste of money", "not worth", "overpriced",
    "price hike", "raised their prices", "customer support is", "cancelled my",
    "canceling my", "disappointed with",
]
RECOMMENDATION_CUES = [
    "i recommend", "would recommend", "highly recommend", "i use", "we use",
    "i switched to", "we switched to", "go with", "check out", "try",
    "you should use", "love using", "big fan of", "can't recommend",
]
REVIEW_CUES = [
    "my experience with", "review", "after using", "been using", "months with",
    "years with", "honest thoughts", "my take on", "verdict", "tried",
]
QUESTION_HINTS = ["?", "anyone use", "anyone tried", "is it worth", "how is",
                  "how's", "thoughts on", "worth it", "should i"]


def _text_of(post: dict[str, Any]) -> str:
    return f"{post.get('title', '')}\n{post.get('selftext', '')}".lower()


def post_text(post: dict[str, Any]) -> str:
    """Public: original-case title + body, for the semantic gate."""
    return f"{post.get('title', '')}\n{post.get('selftext', '')}".strip()


_STOP = {"a", "an", "the", "to", "for", "of", "vs", "or", "and", "app", "apps",
         "tool", "tools", "software", "best"}


def _keyword_hits(text: str, terms: list[str]) -> list[str]:
    """Return the terms that appear in `text` as a contiguous phrase.

    Trims leading/trailing stopwords then requires the remaining tokens to be
    adjacent (whitespace/punctuation only between them), so distinctive brand
    and competitor names match reliably without scattered false positives.
    """
    hits: list[str] = []
    for term in terms:
        tokens = re.findall(r"[a-z0-9]+", term.lower())
        while tokens and tokens[0] in _STOP:
            tokens = tokens[1:]
        while tokens and tokens[-1] in _STOP:
            tokens = tokens[:-1]
        if not tokens:
            tokens = re.findall(r"[a-z0-9]+", term.lower())
        if not tokens:
            continue
        pattern = r"\b" + r"[\s\W]+".join(re.escape(t) for t in tokens) + r"\b"
        if re.search(pattern, text):
            hits.append(term)
    return hits


def _age_hours(created_iso: str) -> float | None:
    if not created_iso:
        return None
    try:
        dt = datetime.fromisoformat(created_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)
    except ValueError:
        return None


def _sentiment(text: str) -> str:
    pos = sum(1 for w in POSITIVE_WORDS if w in text)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


def _mention_type(text: str, title: str) -> str:
    """Classify the shape of the mention. Order = priority when several match."""
    if any(c in text for c in COMPLAINT_CUES):
        return "complaint"
    if any(c in text for c in COMPARISON_CUES):
        return "comparison"
    if title.strip().endswith("?") or any(q in text for q in QUESTION_HINTS):
        return "question"
    if any(c in text for c in RECOMMENDATION_CUES):
        return "recommendation"
    if any(c in text for c in REVIEW_CUES):
        return "review"
    return "discussion"


def _buzz_score(upvotes: int, comments: int, age_hours: float | None) -> int:
    """Engagement weighted by recency, 0-100. Newer + more engaged = higher."""
    age_days = max((age_hours or 0) / 24, 0.04)  # floor ~1h
    recency = max(0.1, min(1.0, 1 - age_days / 45))
    engagement = max(0, upvotes) + 2 * max(0, comments)
    raw = math.sqrt(engagement) * 6 * recency
    return max(0, min(100, round(raw)))


def _geo_signal(upvotes: int, comments: int, age_hours: float | None,
                title_match: bool) -> tuple[int, str]:
    """Heuristic likelihood the thread is surfaced by Google AI Overviews /
    AI search: high-upvote, high-comment, recent, on-topic threads rank and get
    cited. Returns (0-100 score, tier)."""
    up_n = min(1.0, math.log10(1 + max(0, upvotes)) / 3)      # ~1000 upvotes -> 1
    cm_n = min(1.0, math.log10(1 + max(0, comments)) / 2.5)   # ~300 comments -> 1
    age_days = (age_hours or 0) / 24
    rec_n = max(0.0, min(1.0, 1 - age_days / 60))
    score = round(100 * (0.4 * up_n + 0.25 * cm_n + 0.2 * (1 if title_match else 0) + 0.15 * rec_n))
    tier = "high" if score >= 66 else "medium" if score >= 33 else "low"
    return score, tier


def _priority(sentiment: str, mtype: str, buzz: int, geo_tier: str) -> str:
    """What the marketer should do with this mention."""
    if sentiment == "negative" or mtype in ("complaint", "question", "comparison"):
        return "respond"
    if geo_tier == "high" or buzz >= 50:
        return "watch"
    return "ignore"


def classify_mention(post: dict[str, Any], cfg: BrandMonitorInput) -> Mention | None:
    """Turn one raw post into a scored Mention, or None if no brand term matches."""
    text = _text_of(post)
    if not text.strip():
        return None

    matched = _keyword_hits(text, cfg.keywords)
    if not matched:
        return None  # not a brand mention, skip
    competitors = _keyword_hits(text, cfg.competitors)

    title = post.get("title", "")
    upvotes = int(post.get("score") or 0)
    comments = int(post.get("numComments") or 0)
    age = _age_hours(post.get("created", ""))
    title_match = bool(_keyword_hits(title.lower(), cfg.keywords))

    sentiment = _sentiment(text)
    mtype = _mention_type(text, title)
    buzz = _buzz_score(upvotes, comments, age)
    geo, geo_tier = _geo_signal(upvotes, comments, age, title_match)
    priority = _priority(sentiment, mtype, buzz, geo_tier)

    body = post.get("selftext", "") or title
    snippet = re.sub(r"\s+", " ", body).strip()[:280]

    return Mention(
        mentionId=f"t3_{post.get('id', '')}",
        type="post",
        matchedTerms=matched,
        competitorMentioned=competitors[0] if competitors else None,
        sentiment=sentiment,  # type: ignore[arg-type]
        mentionType=mtype,    # type: ignore[arg-type]
        buzzScore=buzz,
        geoSignal=geo,
        geoTier=geo_tier,     # type: ignore[arg-type]
        suggestedPriority=priority,  # type: ignore[arg-type]
        title=title,
        snippet=snippet,
        url=post.get("url", ""),
        subreddit=post.get("subreddit", ""),
        author=post.get("author", "[deleted]"),
        score=post.get("score"),
        numComments=post.get("numComments"),
        createdAt=post.get("created") or None,
        ageHours=age,
    )


async def sharpen_sentiment_llm(mention: Mention, post: dict[str, Any],
                                cfg: BrandMonitorInput) -> Mention:
    """Optional BYOK LLM pass to correct sentiment on the mentions we keep.

    Only re-scores sentiment (the noisiest lexicon dimension). Falls back to the
    lexicon verdict on any error or missing key, so a run never fails on the LLM.
    """
    if not cfg.openaiApiKey:
        return mention
    try:
        verdict = await _llm_sentiment(post, cfg)
    except Exception as exc:  # noqa: BLE001 - never fail a run on LLM error
        logger.warning("LLM sentiment failed (%s); keeping lexicon verdict.", exc)
        return mention
    s = (verdict or {}).get("sentiment")
    if s in ("positive", "negative", "neutral"):
        mention.sentiment = s  # type: ignore[assignment]
        mention.suggestedPriority = _priority(  # type: ignore[assignment]
            s, mention.mentionType, mention.buzzScore, mention.geoTier
        )
    return mention


async def _llm_sentiment(post: dict[str, Any], cfg: BrandMonitorInput) -> dict[str, Any] | None:
    import httpx

    prompt = (
        f"Brand context: {cfg.brandContext or ', '.join(cfg.keywords)}\n"
        f"Reddit post title: {post.get('title', '')}\n"
        f"Body: {(post.get('selftext') or '')[:1200]}\n\n"
        "What is the sentiment TOWARD the brand in this post? Reply JSON only: "
        '{"sentiment": one of ["positive","negative","neutral"]}'
    )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {cfg.openaiApiKey}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
        )
        resp.raise_for_status()
        return json.loads(resp.json()["choices"][0]["message"]["content"])
