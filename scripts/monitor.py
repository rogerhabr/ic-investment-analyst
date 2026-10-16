"""
monitor.py: daily re-score of one watchlist deal. Deterministic: same inputs, same seeds, same numbers.

CLI:
  python monitor.py deal_doc.json overrides.json [--burn-in] [--now ISO] [--mode DAILY] [--thresholds path]

deal_doc.json  the watchlist `deals` document (the object with deal_json, key_metrics, gate_checks, ...),
               either bare or wrapped as {"id":..., "data": {...}} as ArtifactData saves it.
overrides.json {"set":   {"project.debt_rate": 0.0912, "benchmark_rate": 0.0425, "lombard.loan_rate": 0.0561},
                "as_of": {"project.debt_rate": "2026-10-19", ...},      # date each refreshed input was observed
                "sources": {"project.debt_rate": "SOFR 3.88% + 5.24% margin, CME 2026-10-19"}}
               {} or {"set": {}} = re-run on stored inputs (proves reproducibility).

stdout: {"deal_id", "deal_update": {...fields to merge into the deal doc...},
         "run": {...runs row...}, "alerts": [...], "material": bool, "carried": [...]}

Alert rules (references/state_and_scheduling.md): verdict change, a gate check newly FAIL or newly
passing, P(margin call) up >= 2 pts, equity IRR moved >= 100 bps, refreshed input more than 2 business
days old. Each alert carries a deterministic `id` (deal, UTC day, type, subject): write it as alerts/<id>.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from engine import ProjectInputs, run_project            # noqa: E402
from montecarlo import (LombardInputs, ProjectMCSpec,     # noqa: E402
                        simulate_lombard, simulate_project)
from risk_gate import DEFAULT_THRESHOLDS, evaluate        # noqa: E402

STALE_BUSINESS_DAYS = 2      # weekday-only age; a Friday print read on Monday is 1 business day old
IRR_MOVE = 0.01
PCALL_MOVE = 0.02


def _unwrap(doc: dict) -> tuple[str | None, dict]:
    if "data" in doc and isinstance(doc["data"], dict) and "deal_json" in doc["data"]:
        return doc.get("id"), doc["data"]
    return doc.get("id"), doc


def apply_overrides(deal_json: dict, sets: dict) -> dict:
    dj = copy.deepcopy(deal_json)
    for path, val in (sets or {}).items():
        node, keys = dj, path.split(".")
        for k in keys[:-1]:
            if k not in node or not isinstance(node[k], dict):
                raise KeyError(f"override path not found: {path}")
            node = node[k]
        if keys[-1] not in node:
            raise KeyError(f"override path not found: {path} (refusing to invent an input)")
        node[keys[-1]] = val
    return dj


def compute_metrics(dj: dict) -> tuple[dict, dict]:
    """Every metric here comes from code. Returns (metrics, detail)."""
    m, detail = {}, {}
    if dj.get("project"):
        p = ProjectInputs(**dj["project"])
        r = run_project(p)
        for k in ("equity_irr", "project_irr", "llcr", "min_dscr_amort", "min_dscr", "moic"):
            v = getattr(r, k)
            if v is not None:
                m[k] = v
        if dj.get("project_mc"):
            mc = simulate_project(p, ProjectMCSpec(**dj["project_mc"]))
            for k in ("p_irr_below_hurdle", "p_equity_loss", "p_dscr_breach"):
                if mc.get(k) is not None:
                    m[k] = mc[k]
            detail["mc"] = {k: mc.get(k) for k in ("irr_p10", "irr_p50", "irr_p90")}
        der = dj.get("derived") or {}
        if der.get("total_acquisition_cost"):
            noi = p.revenue_y1 * (1 - p.opex_pct_revenue) - p.fixed_opex
            m["net_yield"] = noi / der["total_acquisition_cost"]
        if der.get("loan") and der.get("price"):
            m["ltv"] = der["loan"] / der["price"]
        if "equity_irr" in m and dj.get("benchmark_rate") is not None:
            m["excess_vs_benchmark"] = m["equity_irr"] - dj["benchmark_rate"]
    elif dj.get("lombard"):
        r = simulate_lombard(LombardInputs(**dj["lombard"]))
        for k in ("ltv0", "ltv0_lending", "p_margin_call", "p_forced_liquidation", "cvar_95", "carry_spread"):
            if r.get(k) is not None:
                m[k] = r[k]
        detail["drawdown_to_call"] = r.get("drawdown_to_call")
    return m, detail


def _kill_payload(kr) -> list[dict]:
    out = []
    for k in kr or []:
        out.append(k if isinstance(k, dict) else {"risk": k, "mitigated": False})
    return out


def _driver(checks: list[dict]) -> dict | None:
    scored = [c for c in checks if c.get("value") is not None]
    if not scored:
        return None
    worst = max(scored, key=lambda c: c.get("shortfall_rel", 0))
    return {"name": worst["metric"], "rule": worst["rule"], "value": worst["value"]}


def _business_days(a: dt.date, b: dt.date) -> int:
    """Weekdays strictly after a, up to and including b."""
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        n += d.weekday() < 5
    return n


def _parse_ts(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def rescore(deal_doc: dict, overrides: dict, thresholds: dict, now: dt.datetime,
            burn_in: bool = False, mode: str = "DAILY") -> dict:
    deal_id, d = _unwrap(deal_doc)
    deal_id = deal_id or d.get("name")
    dj = apply_overrides(d["deal_json"], overrides.get("set", {}))
    metrics, detail = compute_metrics(dj)

    prev = d.get("key_metrics") or {}
    carried = sorted(k for k in prev if k not in metrics)
    for k in carried:                       # e.g. sharpe from a historical series: not recomputable here
        metrics[k] = prev[k]

    gate = evaluate({"asset_class": d["asset_class"], "country": d.get("country", "DEFAULT"),
                     "metrics": metrics, "kill_risks": _kill_payload(d.get("kill_risks"))}, thresholds)

    ts = now.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    name = d.get("name", deal_id)
    alerts, changed = [], []

    day = now.astimezone(dt.timezone.utc).strftime("%Y%m%d")

    def alert(kind, sev, msg):
        # deterministic id: a re-run on the same day overwrites instead of duplicating
        aid = f"{deal_id}-{day}-{kind}-{hashlib.sha1(msg.split(':', 1)[-1].split(' observed')[0].encode()).hexdigest()[:8]}"
        alerts.append({"id": aid, "deal_id": deal_id, "ts_utc": ts, "type": kind, "severity": sev, "message": msg,
                       "acknowledged": False, "burn_in": burn_in})
        changed.append(msg)

    pv = d.get("last_verdict")
    if pv and pv != gate["verdict"]:
        alert("verdict_change", "critical" if gate["verdict"] == "DECLINE" or pv == "PROCEED" else "warning",
              f"{name}: verdict {pv} -> {gate['verdict']}")
    prev_status = {c["metric"]: c.get("status") for c in d.get("gate_checks") or []}
    for c in gate["checks"]:
        was, now_s = prev_status.get(c["metric"]), c["status"]
        if was is None or was == now_s:
            continue
        if now_s == "FAIL":
            alert("threshold_cross", "critical", f"{name}: {c['metric']} now FAIL ({c['value']:.4g} vs {c['rule']})")
        elif was in ("FAIL", "SOFT_FAIL") and now_s == "PASS":
            alert("threshold_cross", "info", f"{name}: {c['metric']} now passes ({c['value']:.4g} vs {c['rule']})")
        elif now_s == "SOFT_FAIL":
            alert("threshold_cross", "warning", f"{name}: {c['metric']} within band ({c['value']:.4g} vs {c['rule']})")
    if "p_margin_call" in prev and "p_margin_call" in metrics and "p_margin_call" not in carried:
        dp = metrics["p_margin_call"] - prev["p_margin_call"]
        if dp >= PCALL_MOVE:
            alert("threshold_cross", "warning", f"{name}: P(margin call) up {dp * 100:.1f} pts to {metrics['p_margin_call']:.1%}")
    if "equity_irr" in prev and "equity_irr" in metrics and "equity_irr" not in carried:
        di = metrics["equity_irr"] - prev["equity_irr"]
        if abs(di) >= IRR_MOVE:
            alert("metric_move", "info", f"{name}: equity IRR {'+' if di > 0 else ''}{di * 1e4:.0f} bps to {metrics['equity_irr']:.2%}")
    for path, asof in (overrides.get("as_of") or {}).items():
        obs = _parse_ts(asof if "T" in asof else asof + "T23:59:59+00:00").date()
        bd = _business_days(obs, now.astimezone(dt.timezone.utc).date())
        if bd > STALE_BUSINESS_DAYS:
            alert("stale_data", "warning", f"{name}: {path} observed {asof}, {bd} business days old")

    prev_score = d.get("last_score")
    upd = {
        "deal_json": dj,
        "key_metrics": metrics,
        "gate_checks": gate["checks"],
        "last_verdict": gate["verdict"],
        "last_score": gate["score"],
        "score_delta": round(gate["score"] - prev_score, 1) if isinstance(prev_score, (int, float)) else None,
        "driver_metric": (_driver(gate["checks"])
                          if any(c["status"] in ("FAIL", "SOFT_FAIL") for c in gate["checks"])
                          or not gate["unmitigated_kill_risks"]
                          else {"name": "unmitigated_kill_risks", "rule": "max 0",
                                "value": len(gate["unmitigated_kill_risks"])}),
        "last_run_ts": ts,
    }
    run = {"deal_id": deal_id, "ts_utc": ts, "mode": mode, "verdict": gate["verdict"], "score": gate["score"],
           "key_metrics": metrics, "changed": changed, "carried": carried,
           "data_as_of": overrides.get("as_of") or {}, "sources": overrides.get("sources") or {},
           "overrides": overrides.get("set") or {}}
    return {"deal_id": deal_id, "deal_update": upd, "run": run, "alerts": alerts,
            "material": bool(alerts), "carried": carried, "detail": detail}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("deal_doc")
    ap.add_argument("overrides")
    ap.add_argument("--burn-in", action="store_true")
    ap.add_argument("--now")
    ap.add_argument("--mode", default="DAILY")
    ap.add_argument("--thresholds", default=DEFAULT_THRESHOLDS)
    ap.add_argument("--deal-id", help="watchlist doc id (default: the deal_doc file name)")
    a = ap.parse_args(argv)
    now = _parse_ts(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    doc = json.load(open(a.deal_doc))
    doc_id = a.deal_id or os.path.splitext(os.path.basename(a.deal_doc))[0]
    if isinstance(doc, dict) and not doc.get("id"):
        doc = {**doc, "id": doc_id} if "deal_json" in doc else doc
    out = rescore(doc, json.load(open(a.overrides)), json.load(open(a.thresholds)), now, a.burn_in, a.mode)
    json.dump(out, sys.stdout, indent=2, default=float)


if __name__ == "__main__":
    main()
