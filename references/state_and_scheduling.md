# State, scheduling, alerts (the "24x7" layer)

A skill only runs when invoked. Autonomy comes from three pieces outside the skill:

```
Scheduled tasks (fresh session each run)  ──►  this skill (MONITOR / SCOUT)
        ▲                                             │
        │                                    reads/writes
 local_fetcher.py on an always-on machine    State store (artifact database)
 writes market_snapshot.json                 watchlist · runs · alerts · scout log
```

## State store
Use an Artifact with the `db` capability ("IC Watchlist" dashboard) so that runs, Roger and the
dashboard share one durable record. Load the `artifact-capabilities` skill before creating it.
Collections:
- `deals`: {id, name, asset_class, country, status: watch|active|closed|declined, deal_json, owner_notes}
- `runs`: {deal_id, ts_utc, verdict, score, key_metrics{...}, data_as_of, changed_vs_prev[...]}
- `alerts`: {deal_id, ts_utc, type: verdict_change|threshold_cross|stale_data, message, acknowledged}
- `scout`: {ts_utc, candidate, source_url, prescreen_verdict, reason}. Prevents re-screening rejects.

Each scheduled run starts with no memory, so the first step is always to read `deals` and the latest `runs` row per deal.

## Cadence (sample at about twice the speed the signal actually changes)
| Task | When (Asia/Dubai) | Scope |
|---|---|---|
| Daily re-score | weekdays ~06:45 | rates/FX/listed prices refresh; Lombard MC; gate on all `active` + `watch` |
| Weekly scout | Mondays ~07:10 | SCOUT within the mandate; shortlist ≤5 |
| Monthly deep | 1st business day | full ANALYZE refresh on `active` deals incl. comps + agent debate |

## Alert policy
- Push / email only for: verdict change, Lombard P(call) ≥ threshold, any FAIL newly appearing,
  snapshot stale > 36h.
- Everything else goes to the dashboard only. Alert fatigue is a real failure mode: a monitor that
  cries wolf gets ignored when the real call comes.
- Burn-in: for the first week, log alerts without sending them, then report the false-alarm count
  before enabling delivery.

## Safety
Scheduled runs analyse and alert only. They never place orders, move money or contact counterparties.
