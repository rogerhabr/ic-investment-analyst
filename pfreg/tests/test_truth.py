"""The truth engine is itself tested: against hand values, a closed form, and the earlier
standalone scripts (pf_truth.py / pf_truth_v2.py / v2_flex.py) whose numbers were already
confirmed by the plugin's own workbook on 2026-10-06."""
import math

import pytest

from pfreg.truth import PF, dcf, project_finance

A = lambda x, y, tol=1e-4: abs(x - y) < tol


def test_dcf_midyear():
    r = dcf([10] * 5, 0.08, 0.03, midyear=True)
    assert A(r["ev"], 187.1937) and A(r["df_1"], 0.96225) and A(r["pv_tv"], 145.7003)


def test_dcf_end_of_year_and_flex():
    assert A(dcf([10] * 5, 0.08, 0.03, midyear=False)["ev"], 180.1272)
    assert A(dcf([10] * 5, 0.09, 0.03, midyear=True)["ev"], 157.09, 5e-3)


V1 = PF()
V2 = PF(cap_basis="uses", drawdown="prorata", fee=0.02, dsra=0.5)


def test_v1_base():
    r = project_finance(V1)
    assert A(r["sculpt_capacity"], 90.6133) and A(r["debt"], 70.0) and r["binding"] == "gearing"
    assert A(r["min_dscr"], 1.7475) and A(r["equity_irr"], 0.176184, 1e-6)
    assert A(r["project_irr"], 0.121229, 1e-6) and A(r["equity_t2"], -10.0)
    assert abs(r["end_balance"]) < 1e-9


def test_v1_cap95_dscr_binds():
    r = project_finance(PF(cap=0.95))
    assert r["binding"] == "DSCR" and A(r["debt"], 90.6133) and A(r["min_dscr"], 1.35)


def test_v2_base_matches_closed_form():
    r = project_finance(V2)
    sc, rt, k = r["sculpt_capacity"], 0.07, 14 / 1.35 / r["sculpt_capacity"]
    a = 0.7 * (0.02 + rt * 0.7 * 0.02 + 0.5 * k)
    b = 0.7 * (100 + rt * 0.7 * 40)
    assert A(r["debt"], b / (1 - a), 1e-8)
    assert A(r["total_uses"], 107.8648) and A(r["idc"], 2.0340) and A(r["dsra"], 4.3207)
    assert A(r["equity_irr"], 0.175228, 1e-6) and A(r["min_dscr"], 1.6201)


@pytest.mark.parametrize("kw,debt,irr_", [
    (dict(fee=0.03), 76.0965, 0.172083),
    (dict(cap=0.90), 90.6133, 0.215639),
])
def test_v2_flex(kw, debt, irr_):
    p = PF(**{**V2.__dict__, **kw})
    r = project_finance(p)
    assert A(r["debt"], debt) and A(r["equity_irr"], irr_, 1e-6)


def test_v2_cap90_lambda_and_idc():
    r = project_finance(PF(**{**V2.__dict__, "cap": 0.90}))
    assert r["binding"] == "DSCR" and A(r["gearing"], 0.828114, 1e-6) and A(r["idc"], 2.4238)
    # sources = uses, always
    assert A(r["draw_t1"] + r["draw_t2"], r["debt"], 1e-9)
    assert A(r["draw_t1"] + r["draw_t2"] + r["equity_t1"] + r["equity_t2"], r["total_uses"], 1e-9)


def test_identities_random_grid():
    for cap in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        for fee in (0.0, 0.01, 0.03):
            r = project_finance(PF(**{**V2.__dict__, "cap": cap, "fee": fee}))
            assert abs(r["end_balance"]) < 1e-8
            assert r["min_dscr"] >= 1.35 - 1e-9
            assert r["debt"] <= r["sculpt_capacity"] + 1e-9
            assert r["debt"] <= cap * r["total_uses"] + 1e-9
