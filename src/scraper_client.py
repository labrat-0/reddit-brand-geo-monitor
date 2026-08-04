"""Local Reddit scrape using the vendored labrat011/reddit-scraper engine.

Decision: we do NOT call the published reddit-scraper actor over the network.
Its proven Playwright engine is vendored into src/vendor so lead-finder runs as
a single actor: one run, one proxy bill, no hidden child-actor charges, full
cost transparency. Tradeoff: keep src/vendor in sync with upstream.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from apify import Actor

from .models import BrandMonitorInput
from .vendor.models import ScraperInput, ScrapingMode
from .vendor.scraper import RedditScraper
from .vendor.utils import PageFetcher, RateLimiter

logger = logging.getLogger(__name__)

# Per-run cost circuit breaker (mirrors reddit-scraper). Abort a run once its
# estimated compute + proxy spend exceeds a hard ceiling, so a wedged/blocked
# run can never bleed past what one scan is worth.
CU_RATE_USD_PER_HR = 0.40  # conservative (Starter is $0.20; assume worse)
RESIDENTIAL_USD_PER_GB = 8.0
MAX_RUN_COST_USD = 0.12  # ceiling for one scan; ~2x normal floor


def _build_scraper_input(cfg: BrandMonitorInput, keywords: list[str], raw_cap: int) -> ScraperInput:
    """Search mode over brand terms + competitor names."""
    queries = keywords + list(cfg.competitors)
    # A monitor wants the most RECENT brand mentions, so we sort by new. Reddit's
    # relevance sort ignores the time filter and returns years-old top posts,
    # which the recency window in main.py would then throw away. New sort honors
    # timeFilter and surfaces fresh mentions, exactly what a scheduled monitor and
    # its delta state need.
    return ScraperInput(
        mode=ScrapingMode.SEARCH,
        search_queries_list=queries,
        search_subreddit=cfg.subreddits[0] if len(cfg.subreddits) == 1 else "",
        search_sort="new",
        time_filter=cfg.timeFilter,
        max_results=raw_cap,
        include_comments=False,
    )


def _estimate_cost_usd(fetcher: PageFetcher, mem_mb: int) -> float:
    elapsed = asyncio.get_event_loop().time() - fetcher.start_time
    compute = (elapsed / 3600) * (mem_mb / 1024) * CU_RATE_USD_PER_HR
    proxy = (fetcher.total_bytes / 1e9) * RESIDENTIAL_USD_PER_GB
    return compute + proxy


async def fetch_raw_posts(
    cfg: BrandMonitorInput, keywords: list[str], raw_cap: int, mem_mb: int
) -> list[dict[str, Any]]:
    """Scrape Reddit and return raw post dicts (posts only, no comments).

    Returns whatever was gathered before a block, cap, or cost ceiling. Never
    raises: on total failure returns []. Empty return => caller charges nothing.
    """
    scraper_input = _build_scraper_input(cfg, keywords, raw_cap)
    proxy_config = await Actor.create_proxy_configuration(
        actor_proxy_input=cfg.proxyConfiguration
        or {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]}
    )

    posts: list[dict[str, Any]] = []
    try:
        async with PageFetcher(RateLimiter(), proxy_config) as fetcher:
            scraper = RedditScraper(fetcher, scraper_input)
            async for item in scraper.scrape():
                if item.get("type") != "post":
                    continue
                posts.append(item)
                if len(posts) >= raw_cap:
                    break
                # Cost circuit breaker: stop scraping if this run is getting
                # expensive relative to what one scan earns.
                if _estimate_cost_usd(fetcher, mem_mb) >= MAX_RUN_COST_USD:
                    logger.warning(
                        "Cost ceiling hit at %s posts; stopping scan early.", len(posts)
                    )
                    break
    except Exception as exc:  # noqa: BLE001 - never fail the whole run on a scrape error
        logger.warning("Scrape error (returning %s posts gathered): %s", len(posts), exc)

    return posts
