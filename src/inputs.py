"""Bulk keyword resolution: inline list + Apify dataset + CSV/TXT URL.

Lets a customer drive the actor from a spreadsheet or a prior run's dataset
instead of typing keywords by hand. All sources are merged and de-duplicated
(case-insensitive, order preserved).
"""

from __future__ import annotations

import csv
import io
import logging

from apify import Actor

from .models import BrandMonitorInput

logger = logging.getLogger(__name__)

# Field names we accept as "the keyword" when reading dataset items / CSV rows.
KEYWORD_FIELDS = ("keyword", "keywords", "query", "term", "phrase")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        k = (raw or "").strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out


async def _from_dataset(dataset_id: str) -> list[str]:
    """Pull keywords from an Apify dataset. Accepts items that are plain strings
    or dicts with a keyword-ish field."""
    out: list[str] = []
    try:
        dataset = await Actor.open_dataset(id=dataset_id)
        async for item in dataset.iterate_items():
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                for field in KEYWORD_FIELDS:
                    if item.get(field):
                        val = item[field]
                        out.extend(val if isinstance(val, list) else [str(val)])
                        break
    except Exception as exc:  # noqa: BLE001 - bad id should not crash the run
        logger.warning("Could not read keywords dataset %s: %s", dataset_id, exc)
    return out


async def _from_file_url(url: str) -> list[str]:
    """Fetch a CSV or newline-delimited TXT of keywords."""
    import httpx

    out: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
        if "," in text and ("\n" in text or text.count(",") > 1):
            reader = csv.reader(io.StringIO(text))
            rows = list(reader)
            header = [h.strip().lower() for h in rows[0]] if rows else []
            col = next((i for i, h in enumerate(header) if h in KEYWORD_FIELDS), 0)
            start = 1 if any(h in KEYWORD_FIELDS for h in header) else 0
            for row in rows[start:]:
                if len(row) > col and row[col].strip():
                    out.append(row[col].strip())
        else:
            out.extend(line.strip() for line in text.splitlines() if line.strip())
    except Exception as exc:  # noqa: BLE001 - bad url should not crash the run
        logger.warning("Could not read keywords file %s: %s", url, exc)
    return out


async def resolve_keywords(cfg: BrandMonitorInput) -> list[str]:
    """Merge inline keywords + dataset + file URL into one de-duplicated list."""
    merged = list(cfg.keywords)
    if cfg.keywordsDatasetId:
        merged += await _from_dataset(cfg.keywordsDatasetId)
    if cfg.keywordsFileUrl:
        merged += await _from_file_url(cfg.keywordsFileUrl)
    return _dedupe(merged)
