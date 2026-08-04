"""Monitor delta state: remember which mentions we have already emitted.

In monitor mode the actor should emit only mentions it has not seen on a prior
run, so a scheduled run (and any downstream alert) fires only on genuinely new
activity. State is kept in a NAMED key-value store so it survives across runs,
keyed by a hash of the brand terms + subreddits so distinct monitors do not
share state. The seen-id set is bounded so it cannot grow without limit.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from apify import Actor

from .models import BrandMonitorInput

logger = logging.getLogger(__name__)

STATE_KEY = "SEEN_MENTIONS"
MAX_REMEMBERED = 5000  # cap on retained ids; oldest fall off first


def _store_name(cfg: BrandMonitorInput) -> str:
    """Deterministic named store per monitor config."""
    basis = "|".join(sorted(t.lower() for t in cfg.keywords))
    basis += "||" + "|".join(sorted(s.lower() for s in cfg.subreddits))
    h = hashlib.sha256(basis.encode()).hexdigest()[:16]
    return f"brand-monitor-state-{h}"


async def load_seen(cfg: BrandMonitorInput) -> set[str]:
    """Return the set of mention ids emitted on prior runs. Empty on first run
    or if state is unavailable (fail-open: we would re-emit, never crash)."""
    if not cfg.sinceLastRun:
        return set()
    try:
        store = await Actor.open_key_value_store(name=_store_name(cfg))
        rec = await store.get_value(STATE_KEY)
        if isinstance(rec, dict) and isinstance(rec.get("seenIds"), list):
            return {str(x) for x in rec["seenIds"]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load monitor state (%s); treating all as new.", exc)
    return set()


async def save_seen(cfg: BrandMonitorInput, prior: set[str], new_ids: list[str]) -> None:
    """Persist prior + new ids (bounded, newest kept). No-op when not monitoring."""
    if not cfg.sinceLastRun:
        return
    # newest ids (this run) first, then prior, de-duplicated, capped.
    merged: list[str] = []
    seen: set[str] = set()
    for i in list(new_ids) + list(prior):
        if i not in seen:
            seen.add(i)
            merged.append(i)
        if len(merged) >= MAX_REMEMBERED:
            break
    try:
        store = await Actor.open_key_value_store(name=_store_name(cfg))
        await store.set_value(
            STATE_KEY,
            {"seenIds": merged, "lastRunAt": datetime.now(timezone.utc).isoformat()},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not save monitor state (%s); next run may re-emit.", exc)
