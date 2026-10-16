"""
risk_gate.py — turns metrics into a rule-based verdict. The model's opinion never sets the verdict.

Rule:
  verdict = DECLINE      if any kill risk is flagged unmitigated
                         or any metric misses its threshold by more than conditional_band
          = CONDITIONAL  if every miss is within conditional_band (listed as conditions)
          = PROCEED      if no misses
  hurdle-type minimums (equity_irr, net_yield, expected_return) are raised by the country uplift.
  score = 100 * (1 - weighted shortfall), capped [0,100] — for ranking a watchlist, not for the verdict.
Missing metric -> reported as DATA GAP; a data gap on a threshold metric caps the verdict at CONDITIONAL.

CLI: python risk_gate.py metrics.json [thresholds.json]
metrics.json = {"asset_class": "...", "country": "NG", "metrics": {...}, "kill_risks": [{"risk": "...", "mitigated": false}]}
"""
from __future__ import annotations

import json
import os
import sys

HURDLE_METRICS = {"equity_irr", "net_yield", "expected_return"}
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_THRESHOLDS = os.path.join(HERE, "..", "config", "gate_thresholds.json")


def evaluate(payload: dict, thresholds: dict) -> dict:
    ac = payload["asset_class"]
    rules = thresholds["asset_classes"][ac]
    band = thresholds.get("conditional_band", 0.15)
    country = payload.get("country", "DEFAULT")
    uplift_tbl = thresholds.get("country_hurdle_uplift", {})
    uplift = uplift_tbl.get(country, uplift_tbl.get("DEFAULT", 0.0))
    m = payload.get("metrics", {})

    checks, gaps = [], []
    for kind in ("min", "max"):
        for name, thr in rules.get(kind, {}).items():
            if kind == "min" and name in HURDLE_METRICS:
                thr = thr + uplift
            val = m.get(name)
            if val is None:
                gaps.append(name)
                checks.append({"metric": name, "rule": f"{kind} {thr:.4g}", "value": None, "status": "DATA_GAP"})
                continue
            if kind == "min":
                ok = val >= thr
                miss = 0.0 if ok else (thr - val) / abs(thr) if thr else 1.0
            else:
                ok = val <= thr
                miss = 0.0 if ok else (val - thr) / abs(thr) if thr else 1.0
            status = "PASS" if ok else ("SOFT_FAIL" if miss <= band else "FAIL")
            checks.append({"metric": name, "rule": f"{kind} {thr:.4g}", "value": val,
                           "shortfall_rel": round(miss, 4), "status": status})

    kills = [k for k in payload.get("kill_risks", []) if not k.get("mitigated", False)]
    fails = [c for c in checks if c["status"] == "FAIL"]
    softs = [c for c in checks if c["status"] == "SOFT_FAIL"]

    if kills or fails:
        verdict = "DECLINE"
    elif softs or gaps:
        verdict = "CONDITIONAL"
    else:
        verdict = "PROCEED"

    scored = [c for c in checks if c["status"] != "DATA_GAP"]
    shortfall = sum(min(c.get("shortfall_rel", 0), 1.0) for c in scored) / max(len(scored), 1)
    score = max(0.0, min(100.0, 100 * (1 - shortfall) - 25 * len(kills) - 5 * len(gaps)))

    return {
        "verdict": verdict,
        "score": round(score, 1),
        "asset_class": ac,
        "country": country,
        "country_hurdle_uplift": uplift,
        "checks": checks,
        "data_gaps": gaps,
        "unmitigated_kill_risks": kills,
        "conditions": [f"Bring {c['metric']} to {c['rule']} (now {c['value']:.4g})" for c in softs]
                      + [f"Obtain data for {g}" for g in gaps],
        "reasons_to_decline": [f"{c['metric']} {c['value']:.4g} vs {c['rule']}" for c in fails]
                              + [f"Kill risk: {k['risk']}" for k in kills],
    }


if __name__ == "__main__":
    payload = json.load(open(sys.argv[1]))
    thr = json.load(open(sys.argv[2] if len(sys.argv) > 2 else DEFAULT_THRESHOLDS))
    json.dump(evaluate(payload, thr), sys.stdout, indent=2)
