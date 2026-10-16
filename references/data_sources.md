# Data sources — routing, budget, provenance

## Source hierarchy (try in order, stop at first that answers)

| Need | 1st | 2nd | 3rd |
|---|---|---|---|
| Listed prices, FX, ETF, vol | Twelve Data connector (8/min, 800/day free) | `market_snapshot.json` (local fetcher, yfinance) | WebSearch, label "delayed / unverified" |
| Fundamentals, ratios, filings | FMP connector (250/day free, US-heavy) | Twelve Data | Company IR page via WebFetch |
| US rates, spreads, CPI | `market_snapshot.json` → `fred:*` | WebSearch (fred.stlouisfed.org) | — |
| Country macro (GDP, CPI, reserves) | `market_snapshot.json` → `wb:*` | WebSearch IMF / central bank | — |
| Sovereign rating, CDS | WebSearch (rating agency press releases) | — | — |
| DC / infra comps ($/MW, PPA tariffs, cap rates, colo pricing) | Deal-history store (past verdicts) | WebSearch: press releases, broker reports (CBRE, JLL, Cushman), regulator tariff orders | — |
| New products / opportunities | WebSearch + WebFetch on issuer/fund pages | — | — |

Check which connectors are live before planning calls (ToolSearch for `twelvedata`, `fmp`; or ListConnectors).
If neither is connected, say so once in the memo's Data Gaps section. Don't silently substitute web text for prices.

## Budget rules (free tiers)
- Count calls before making them. One deal re-score ≈ 3–5 prices + 2–4 FX + 1–3 fundamentals.
- Batch symbols where the API allows it. Cache within a run; never fetch the same series twice.
- When you hit a 429, stop calling that source for the run, fall to the next source, and log it.

## Provenance (non-negotiable — this is what makes outputs auditable)
Every number that enters a model gets an assumption-register row:
`{field, value, source (URL or "connector:twelvedata"), as_of, confidence H/M/L}`
- **H**: primary source (filing, signed term sheet, connector price < 2 days old)
- **M**: reputable secondary source (broker report, press release, data > 2 days old)
- **L**: inference, analogy, or an unsourced sponsor claim

Rules:
- A value with no source is confidence L and is listed under Data Gaps.
- Any `stale_days` above these limits downgrades confidence by one level: rates/FX 3, equities 3, macro 400.
- Sponsor projections are claims, not data. Record them as `source: sponsor IM p.X`, confidence L, until independently corroborated.

## Local snapshot
`market_snapshot.json` is produced by `scripts/local_fetcher.py` on Roger's always-on machine.
The cloud workspace cannot reach Yahoo, FRED or the World Bank (403 at the proxy, verified 2026-10-16).
Read the snapshot through the device bridge or from wherever Roger syncs it. If it's missing, or its `as_of_utc` is over 36h old, log that as a data gap.
