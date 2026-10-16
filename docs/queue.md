# QUEUE mode: "process my IC queue"

Requests are queued from the **New request** form on the IC Watchlist page
(https://claude.ai/artifact/5igovkrXLThTbUTxMKin3Y). They run only when Roger asks in any Claude chat
("process my IC queue"). Nothing polls the queue.

## 0. Code
The watchlist scripts (`monitor.py`, `watchlist_doc.py`) live in this repo, not in the installed skill.
If `ic-investment-analyst/scripts/watchlist_doc.py` is not on disk: attach the repo with `add_repo`
(owner `rogerhabr`, repo `ic-investment-analyst`), clone it, then
`pip install --break-system-packages -q -r requirements.txt`. Run every script with `PYTHONDONTWRITEBYTECODE=1`.

## 1. Read the queue
ArtifactData `query` on collection `requests` with `where status == queued`. Empty → reply
"Queue empty" in one line and stop. Process oldest first, one at a time.

## 2. Claim it
`update` the request to `{"status": "running", "started_ts": <now UTC>}`, pinned with `if_version`.
A version conflict means another session claimed it: skip it.

## 3. Run it
| `kind` / `asset_class` | What to do |
|---|---|
| `deal`, class with gate thresholds (dc_digital_infra, energy_grid, real_estate, listed_equities, lombard) | Full ANALYZE workflow from the skill. The request is the stage-1 input. |
| `fixed_income`, `other` | No gate thresholds yet: set `status: "error"`, `error: "No gate thresholds for <class> yet. Add them to config/gate_thresholds.json, then re-queue."` |
| `holding` | Personal portfolio module (S2) not built yet: `status: "error"`, `error: "Personal holdings run once the portfolio module ships."` |

Request fields map to ANALYZE inputs:
- `inputs[]` ({key, value, unit}): Roger's own numbers. Register each in `deal.json["sources"]` as
  `{"source": "Roger, request <id>", "date": <ts_utc date>, "confidence": "H"}`. They override market data.
- `files[]`: read each with the Artifact tool, `action: "read"`, `url` = watchlist URL, `path` = the file's `id`.
- `links[]`: WebFetch. `question`: the memo must answer it in its first paragraph. `notes`: context.
- Missing an input the engine needs and no defensible source: `status: "error"`,
  `error: "Needs: <list of inputs>"`. Never invent it.

## 4. Write back (numbers from code only)
Write `deal.json` and `judge.json` ({kill_risks: [unmitigated], mitigated_kill_risks, thesis, sizing}), then
```
python scripts/watchlist_doc.py deal.json judge.json --deal-id <slug> --request-id <id> \
       --status <request.after> [--memo-url <url>] > out.json
```
One ArtifactData `batch`: `set deals/<slug>` ← `out.deal_doc`, `set runs/<slug>-<YYYYMMDDTHHMM>` ← `out.run`,
`update requests/<id>` ← `out.request_update` (pinned). When `after` is `"none"`, write only the request update.
On any failure: `update requests/<id>` to `{"status": "error", "error": "<what failed, in one line>"}`.

## 5. Reply
One line per request: name, verdict, the number that drives it, sizing answer. Then the memo and model files.
