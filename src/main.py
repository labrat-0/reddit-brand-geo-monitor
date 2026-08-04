"""Reddit Brand / GEO Monitor -- Apify Actor entry point.

Pipeline: validate input -> scrape Reddit (vendored engine, one proxy bill) ->
classify each brand mention (sentiment, buzz, mention type, GEO signal) -> in
monitor mode drop mentions seen on a prior run -> push + charge only the new,
above-threshold mentions.
"""

from __future__ import annotations

import logging
import os

from apify import Actor

from .delta_state import load_seen, save_seen
from .embeddings import EmbeddingGate
from .inputs import resolve_keywords
from .mention_gate import ChargeLedger, charge_scan, push_and_charge_mention
from .models import BrandMonitorInput, Mention
from .scoring import classify_mention, post_text, sharpen_sentiment_llm
from .scraper_client import fetch_raw_posts

logger = logging.getLogger(__name__)

# How many raw posts to pull per mention we want. Keeps the scrape tight so
# proxy cost stays low; capped so a wide run cannot balloon bandwidth.
RAW_FETCH_MULTIPLIER = 15
RAW_FETCH_CAP = 1000

# timeFilter -> max mention age in hours. Relevance search ignores Reddit's
# time param, so we enforce recency ourselves to keep timeFilter honest.
AGE_WINDOW_HOURS = {"day": 24, "week": 168, "month": 730, "year": 8760, "all": None}


async def main() -> None:
    async with Actor:
        raw_input = await Actor.get_input() or {}
        try:
            cfg = BrandMonitorInput.model_validate(raw_input)
        except Exception as exc:  # noqa: BLE001
            await Actor.fail(status_message=f"Invalid input: {exc}")
            return

        ledger = ChargeLedger(_read_max_charge_usd())

        # 0. Resolve brand terms from inline list + dataset id + CSV/TXT url.
        keywords = await resolve_keywords(cfg)
        if not keywords:
            await Actor.fail(
                status_message="No brand terms provided. Add keywords, a keywordsDatasetId, or a keywordsFileUrl."
            )
            return
        cfg.keywords = keywords  # authoritative for the match check

        # 1. Scrape (vendored, local). Returns [] on block/failure => no charge.
        raw_cap = min(cfg.maxMentions * RAW_FETCH_MULTIPLIER, RAW_FETCH_CAP)
        mem_mb = int(os.environ.get("ACTOR_MEMORY_MBYTES", "1024"))
        raw_posts = await fetch_raw_posts(cfg, keywords, raw_cap, mem_mb)
        logger.info("Fetched %s raw posts (cap %s).", len(raw_posts), raw_cap)

        # 2. Scan fee: charged once, ONLY because the scrape delivered data.
        if raw_posts:
            await charge_scan(ledger)

        # 3. Monitor state + optional semantic gate (only when there is work).
        seen = await load_seen(cfg) if raw_posts else set()
        gate = EmbeddingGate()
        if raw_posts and cfg.brandContext.strip():
            await gate.setup(cfg.brandContext)

        window = AGE_WINDOW_HOURS.get(cfg.timeFilter)
        emitted = 0
        new_ids: list[str] = []
        for post in raw_posts:
            if emitted >= cfg.maxMentions:
                break

            mention: Mention | None = classify_mention(post, cfg)
            if mention is None:
                continue  # no brand term matched
            # Recency: enforce the timeFilter window ourselves (free).
            if window is not None and mention.ageHours is not None and mention.ageHours > window:
                continue
            # Monitor delta: skip anything already emitted on a prior run.
            if cfg.sinceLastRun and mention.mentionId in seen:
                continue
            # Buzz threshold (free filter before any spend).
            if mention.buzzScore < cfg.minBuzz:
                continue
            # Semantic gate: only meaningful for ambiguous brand names (opt-in).
            if gate.enabled and not gate.passes(post_text(post)):
                continue
            # Optional BYOK LLM sentiment sharpener on the mentions we keep.
            if cfg.scoringMode == "llm":
                mention = await sharpen_sentiment_llm(mention, post, cfg)

            keep_going = await push_and_charge_mention(mention, ledger)
            if not keep_going:
                break
            new_ids.append(mention.mentionId)
            emitted += 1

        # 4. Persist state so the next run only sees what is genuinely new.
        if raw_posts:
            await save_seen(cfg, seen, new_ids)

        await Actor.set_value(
            "RUN_STATS",
            {
                "rawPosts": len(raw_posts),
                "mentionsEmitted": emitted,
                "sinceLastRun": cfg.sinceLastRun,
                "priorSeen": len(seen),
                "scanCharged": ledger.scan_charged,
                "chargedUsd": round(ledger.charged_usd, 4),
                "capHit": ledger.cap_hit,
            },
        )
        msg = f"{emitted} new mentions from {len(raw_posts)} posts scanned. Billed ${ledger.charged_usd:.2f}."
        if not raw_posts:
            msg = "Scrape returned no posts (blocked or no matches). Nothing charged."
        elif emitted == 0:
            msg += " No new mentions: all matches were already seen, below minBuzz, or off-topic."
        Actor.log.info(msg)


def _read_max_charge_usd() -> float | None:
    """Read ACTOR_MAX_TOTAL_CHARGE_USD if the platform set it."""
    raw = os.environ.get("ACTOR_MAX_TOTAL_CHARGE_USD")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
