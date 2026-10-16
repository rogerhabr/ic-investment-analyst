"""monitor.py proofs: reproduces the stored watchlist numbers exactly, moves the right way under
overrides, and raises exactly the alerts the MONITOR rules define."""
import copy
import datetime as dt
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from monitor import rescore  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
THR = json.load(open(os.path.join(ROOT, "config", "gate_thresholds.json")))
NOW = dt.datetime(2026, 10, 19, 2, 45, tzinfo=dt.timezone.utc)
DEALS = ["dubai-hills-2br-offplan", "lagos-10mw-dc", "lombard-2-2m-balanced"]


def load(name):
    d = json.load(open(os.path.join(FIX, name + ".json")))
    return {**d, "id": name}


@pytest.mark.parametrize("name", DEALS)
def test_reproduces_stored_run_exactly(name):
    doc = load(name)
    out = rescore(doc, {}, THR, NOW)
    km = out["deal_update"]["key_metrics"]
    for k, v in doc["key_metrics"].items():
        assert km[k] == pytest.approx(v, abs=1e-12), k
    assert out["carried"] == []
    assert out["deal_update"]["last_verdict"] == doc["last_verdict"]
    assert out["deal_update"]["last_score"] == pytest.approx(doc["last_score"])
    assert out["alerts"] == [] and out["deal_id"] == name


def test_higher_loan_rate_cuts_carry_and_flags_cross():
    doc = load("lombard-2-2m-balanced")
    out = rescore(doc, {"set": {"lombard.loan_rate": 0.0748}}, THR, NOW)   # +200 bp
    km = out["deal_update"]["key_metrics"]
    assert km["carry_spread"] == pytest.approx(doc["key_metrics"]["carry_spread"] - 0.02, abs=1e-9)
    assert any("carry_spread now FAIL" in a["message"] for a in out["alerts"])


def test_vol_shock_raises_margin_call_alert():
    out = rescore(load("lombard-2-2m-balanced"), {"set": {"lombard.sigma": 0.16}}, THR, NOW)
    assert any("P(margin call) up" in a["message"] for a in out["alerts"])


def test_irr_move_and_verdict_change():
    doc = load("lagos-10mw-dc")
    out = rescore(doc, {"set": {"project.revenue_y1": 30_000_000}}, THR, NOW)
    msgs = " ".join(a["message"] for a in out["alerts"])
    assert "equity IRR +" in msgs
    doc2 = copy.deepcopy(doc)
    doc2["last_verdict"] = "PROCEED"
    out2 = rescore(doc2, {}, THR, NOW)
    assert out2["alerts"][0]["type"] == "verdict_change" and out2["alerts"][0]["severity"] == "critical"


def test_stale_input_alert_and_burn_in_flag():
    out = rescore(load("lagos-10mw-dc"), {"set": {"benchmark_rate": 0.0428},
                                          "as_of": {"benchmark_rate": "2026-10-14"}}, THR, NOW, burn_in=True)
    st = [a for a in out["alerts"] if a["type"] == "stale_data"]
    assert st and st[0]["burn_in"] is True


def test_unknown_override_refused():
    with pytest.raises(KeyError):
        rescore(load("lagos-10mw-dc"), {"set": {"project.debt_ratee": 0.1}}, THR, NOW)


def test_watchlist_doc_rebuilds_stored_deal_from_deal_json_only():
    from watchlist_doc import build
    doc = load("lombard-2-2m-balanced")
    out = build(doc["deal_json"], {"kill_risks": doc["kill_risks"], "thesis": "t", "sizing": "s"},
                "lombard-2-2m-balanced", "watch", THR, NOW, request_id="r1")
    dd = out["deal_doc"]
    assert dd["last_verdict"] == doc["last_verdict"] and dd["last_score"] == pytest.approx(doc["last_score"])
    for k, v in doc["key_metrics"].items():
        assert dd["key_metrics"][k] == pytest.approx(v, abs=1e-12)
    assert out["request_update"]["status"] == "done"
    assert out["request_update"]["result"]["verdict"] == doc["last_verdict"]
