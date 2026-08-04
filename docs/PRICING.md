# Pricing (plain and honest)

Two line items. That is the whole bill. You see both on every run.

## 1. Scan performed

Charged **once per run**, and only when the scrape actually returned posts to
score. If Reddit blocks the run or nothing matches, the scan delivered nothing
and you are **not charged**.

## 2. New mention

Charged **once per mention delivered**, and only for mentions that are:

- genuinely **new since the last run** (in monitor mode), and
- at or above your `minBuzz` threshold.

You are never charged for a mention you were already shown on a prior run, nor
for one filtered out below `minBuzz` or as off-topic. Push happens before the
charge, so you always have the item you paid for.

## Cost control

- A per-run **cost ceiling** aborts a wedged or blocked run before it can
  overspend on proxy or compute, keeping and returning whatever it gathered.
- Set **Max total charge (USD)** on the run to hard-cap spend. The actor stops
  before crossing it rather than charging past it.
- Turn off monitor mode (`sinceLastRun = false`) for a one-shot pull; turn it
  on for scheduled monitoring so you only pay for new activity.

## Note on configuration

The actual per-event prices are set in the Apify Console monetization config.
The constants in `src/models.py` (`PRICE_SCAN_USD`, `PRICE_PER_MENTION_USD`)
must match those Console values so the max-charge cap math stays accurate.
