"""The single point where money is charged.

Every Actor.charge in this actor flows through here: charge_scan() (once per
run that scraped) and push_and_charge_mention() (once per delivered new
mention). Nothing else calls Actor.charge. One gate, auditable.

RULES (see models.py):
  - scan-performed: charged once, only after the scrape delivered >=1 post.
  - new-mention: push the mention to the dataset THEN charge, in that order,
    exactly once per pushed item, only for mentions new since the last run.
  - Before ANY charge we check the remaining ACTOR_MAX_TOTAL_CHARGE_USD budget;
    if the next charge would exceed it, we stop rather than charge past the cap.
  - Empty / blocked / already-seen mentions never reach the charge calls, so
    they are never charged. We never charge for a mention we did not deliver.
"""

from __future__ import annotations

import logging

from apify import Actor

from .models import (
    EVENT_NEW_MENTION,
    EVENT_SCAN_PERFORMED,
    PRICE_PER_MENTION_USD,
    PRICE_SCAN_USD,
    Mention,
)

logger = logging.getLogger(__name__)


class ChargeLedger:
    """Tracks all charges for one run. Enforces the max-charge cap."""

    def __init__(self, max_total_usd: float | None) -> None:
        self.max_total = max_total_usd
        self.mentions_charged = 0
        self.scan_charged = False
        self.charged_usd = 0.0
        self.cap_hit = False

    def _would_exceed_cap(self, next_charge_usd: float) -> bool:
        if self.max_total is None:
            return False
        return (self.charged_usd + next_charge_usd) > self.max_total + 1e-9


async def charge_scan(ledger: ChargeLedger) -> None:
    """Charge the one-per-run scan fee. Call ONLY after scrape delivered data."""
    if ledger.scan_charged or ledger.cap_hit:
        return
    if ledger._would_exceed_cap(PRICE_SCAN_USD):
        ledger.cap_hit = True
        logger.info("Max-charge cap reached before scan fee; not charging.")
        return
    await _charge(EVENT_SCAN_PERFORMED)
    ledger.scan_charged = True
    ledger.charged_usd += PRICE_SCAN_USD


async def push_and_charge_mention(mention: Mention, ledger: ChargeLedger) -> bool:
    """Push one mention, then charge for it unless the cap is reached.

    Returns True if the caller should keep going, False if the cap is hit.
    """
    if ledger.cap_hit:
        return False
    if ledger._would_exceed_cap(PRICE_PER_MENTION_USD):
        ledger.cap_hit = True
        logger.info(
            "Max-charge cap reached at %s mentions ($%.4f). Stopping without charging further.",
            ledger.mentions_charged,
            ledger.charged_usd,
        )
        return False

    # Push first so the item exists even if the charge call is interrupted.
    await Actor.push_data(mention.model_dump())

    await _charge(EVENT_NEW_MENTION)
    ledger.mentions_charged += 1
    ledger.charged_usd += PRICE_PER_MENTION_USD
    return True


async def _charge(event_name: str) -> None:
    """Fire one PPE charge. Fail-open: a pricing misconfig must not crash a
    customer run (we lose the charge, they still get their data)."""
    try:
        await Actor.charge(event_name)
    except Exception as exc:  # noqa: BLE001
        logger.error("Charge for '%s' failed (continuing uncharged): %s", event_name, exc)
