# Reddit Brand & GEO Monitor

Track what Reddit says about your brand, and how likely it is to shape AI search. **No API key. No login.** Pure HTTP scraping with built-in sentiment, buzz, and a GEO signal. Built for scheduled monitoring and automated workflows.

---

## What it does

Give it your brand terms. Each run scans Reddit and returns every mention, scored and ready to act on:

- **Sentiment** toward your brand: positive, negative, neutral
- **Mention type**: review, comparison, complaint, recommendation, question, discussion
- **Buzz score** (0-100): engagement weighted by recency, so threads gaining traction rise to the top
- **GEO signal** (0-100 + tier): how likely the thread is to be surfaced by Google AI Overviews and AI search, based on upvotes, comments, recency, and title match
- **Suggested priority**: respond, watch, or ignore
- **Competitor flag**: when a mention compares you to a competitor you track

In **monitor mode** (default), each scheduled run emits only mentions it has not seen before, so your alerts fire only on genuinely new activity.

---

## Why the GEO signal matters

Reddit is the single most-cited domain in AI answers, and Google's AI Overviews pull heavily from it. What people say about you on Reddit now shapes what ChatGPT and Google tell your prospects. This actor scores each thread's likelihood of being surfaced by AI search, so you can prioritize the conversations that actually move your brand's AI visibility, not just the loudest ones.

---

## Built for workflows

This actor is designed to be a node in your automation, not a manual tool:

- **Run on a schedule** and emit only new mentions each run (monitor mode).
- **Webhook on finish** into n8n, Make, or Zapier to route new mentions to Slack, a Google Sheet, Notion, or your CRM.
- **MCP-ready** for AI agents: query it in natural language via Apify's hosted MCP server.

A typical setup: schedule this actor hourly, webhook the dataset into n8n, filter to `suggestedPriority = respond`, and post those to a Slack channel. New negative mention on a high-GEO thread, your team hears about it within the hour.

---

## Output

Each mention is one dataset item:

```json
{
  "mentionId": "t3_1a2b3c",
  "type": "post",
  "matchedTerms": ["Notion"],
  "competitorMentioned": "Obsidian",
  "sentiment": "negative",
  "mentionType": "comparison",
  "buzzScore": 61,
  "geoSignal": 72,
  "geoTier": "high",
  "suggestedPriority": "respond",
  "title": "Switching from Notion to Obsidian, here's why",
  "snippet": "After two years on Notion I finally moved to Obsidian because...",
  "url": "https://www.reddit.com/r/productivity/comments/1a2b3c/...",
  "subreddit": "productivity",
  "author": "some_user",
  "score": 340,
  "numComments": 118,
  "createdAt": "2026-08-03T14:20:00+00:00",
  "ageHours": 21.5
}
```

---

## Use cases

### Reputation monitoring
Catch negative mentions and complaints early, prioritized by buzz and GEO signal, so you respond where it counts before a thread gains traction.

### Competitor and switching tracking
See who is comparing you to a competitor, or publicly switching away, with the competitor named on each mention.

### GEO / AI-search visibility
Track the high-GEO threads that AI search is most likely to cite about your brand, and focus your community effort there.

### Crisis detection
Schedule it tight (hourly) and alert on any negative mention above a buzz threshold. A spike surfaces as new high-priority mentions.

---

## Input reference

| Field | Type | Default | Description |
|---|---|---|---|
| `keywords` | string[] | - | Brand terms and variations to track |
| `competitors` | string[] | - | Also track these; comparisons are flagged |
| `brandContext` | string | - | One sentence, only for common-word names, to filter off-topic matches |
| `subreddits` | string[] | all | Restrict to specific subreddits |
| `sinceLastRun` | boolean | `true` | Monitor mode: emit only mentions new since the last run |
| `timeFilter` | select | `week` | Look-back window per run |
| `minBuzz` | integer | `0` | Skip mentions below this buzz score |
| `maxMentions` | integer | `100` | Cap on new mentions per run |
| `scoringMode` | select | `lexicon` | `lexicon` (no key) or `llm` (BYOK sentiment) |
| `openaiApiKey` | secret | - | Your key, only if `scoringMode = llm` |
| `keywordsDatasetId` / `keywordsFileUrl` | string | - | Bulk brand terms from a dataset or CSV/TXT URL |
| `proxyConfiguration` | object | RESIDENTIAL | Residential proxies recommended |

---

## Pricing

Pay-per-event, two line items, both shown on every run:

- **Scan performed**: charged once per run, only when the scrape actually returned posts. Blocked or empty runs are free.
- **New mention**: charged once per delivered mention, only for mentions new since the last run and above your `minBuzz`. You are never charged for a mention you were already shown or that was filtered out.

See [docs/PRICING.md](docs/PRICING.md) for the full breakdown.

---

## How it works

- Scrapes Reddit through a vendored HTTP engine (no child-actor calls), so a run is **one proxy bill with no hidden charges**, and a per-run cost ceiling aborts a wedged run before it can overspend.
- Sentiment and classification are **deterministic** by default (same input, same output, no API key). The optional LLM mode re-checks sentiment on kept mentions using your own key.
- Monitor state lives in a named key-value store keyed to your brand terms, so distinct monitors never share state and the seen-set is bounded.

---

## Limitations

- Reddit rate-limits and blocks aggressively; residential proxies are strongly recommended for reliable runs.
- The GEO signal is a heuristic (engagement + recency + relevance), not a live AI-citation lookup. It ranks which threads are most likely to matter for AI visibility, it does not query the AI engines directly.
- Sentiment in `lexicon` mode is fast and deterministic but coarse; use `llm` mode with your own key for sharper reads on ambiguous mentions.
