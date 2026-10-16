"""
watchlist_doc.py: turn a finished ANALYZE (deal.json + Judge output) into the exact documents the
IC Watchlist page reads, with every number recomputed by code (monitor.rescore), never typed.

CLI:
  python watchlist_doc.py deal.json judge.json --deal-id lagos-10mw-dc [--status watch]
                          [--request-id r_20261016_0912] [--memo-url URL] [--now ISO]
judge.json = {"kill_risks": ["...unmitigated..."], "thesis": "...", "sizing": "...",
              "mitigated_kill_risks": ["..."]}            # mitigated ones are recorded, not gated
stdout: {"deal_id", "deal_doc", "run", "request_update" | null}
Write with ONE ArtifactData batch: set deals/<deal_id>, set runs/<deal_id>-<YYYYMMDDTHHMM>,
update requests/<request_id>.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from monitor import _parse_ts, rescore           # noqa: E402
from risk_gate import DEFAULT_THRESHOLDS          # noqa: E402


def build(deal: dict, judge: dict, deal_id: str, status: str, thresholds: dict, now: dt.datetime,
          request_id: str | None = None, memo_url: str | None = None) -> dict:
    if status not in ("watch", "active", "closed", "declined"):
        raise ValueError("status must be watch|active|closed|declined")
    doc = {"id": deal_id, "name": deal.get("name", deal_id), "asset_class": deal["asset_class"],
           "country": deal.get("country", "DEFAULT"), "status": status, "deal_json": deal,
           "kill_risks": judge.get("kill_risks", []), "thesis": judge.get("thesis", ""),
           "sizing": judge.get("sizing", "")}
    out = rescore(doc, {}, thresholds, now, mode="ANALYZE")
    deal_doc = {k: v for k, v in doc.items() if k != "id"} | out["deal_update"]
    deal_doc["score_delta"] = None
    if judge.get("mitigated_kill_risks"):
        deal_doc["mitigated_kill_risks"] = judge["mitigated_kill_risks"]
    if memo_url:
        deal_doc["memo_url"] = memo_url
    if request_id:
        deal_doc["request_id"] = request_id
    run = out["run"] | {"changed": ["Initial analysis"]}
    req = None
    if request_id:
        req = {"status": "done", "done_ts": out["run"]["ts_utc"],
               "result": {"deal_id": deal_id, "verdict": deal_doc["last_verdict"], "score": deal_doc["last_score"],
                          "driver": deal_doc["driver_metric"], "sizing": deal_doc["sizing"],
                          "memo_url": memo_url}}
    return {"deal_id": deal_id, "deal_doc": deal_doc, "run": run, "request_update": req}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("deal_json")
    ap.add_argument("judge_json")
    ap.add_argument("--deal-id", required=True)
    ap.add_argument("--status", default="watch")
    ap.add_argument("--request-id")
    ap.add_argument("--memo-url")
    ap.add_argument("--now")
    ap.add_argument("--thresholds", default=DEFAULT_THRESHOLDS)
    a = ap.parse_args(argv)
    now = _parse_ts(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    out = build(json.load(open(a.deal_json)), json.load(open(a.judge_json)), a.deal_id, a.status,
                json.load(open(a.thresholds)), now, a.request_id, a.memo_url)
    json.dump(out, sys.stdout, indent=2, default=float)


if __name__ == "__main__":
    main()
