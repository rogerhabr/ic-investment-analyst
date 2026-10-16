"""Proof suite. Run: python -m pytest -q scripts/test_engine.py
Each test checks the engine against an INDEPENDENT reference (numpy_financial,
closed-form barrier math, hand-computed annuity), not against itself."""
import json
import math
import os

import numpy as np
import numpy_financial as npf
import pytest

import engine as E
import montecarlo as MC
import risk_gate as G

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.parametrize("cf", [
    [-100, 30, 30, 30, 30],
    [-1000, 0, 0, 200, 400, 600, 800],
    [-50, -50, 20, 40, 60, 80],
    [-100, 110],
    [-100, 5, 5, 5, 5, 105],
])
def test_irr_matches_numpy_financial(cf):
    assert E.irr(cf) == pytest.approx(npf.irr(cf), abs=1e-8)


def test_irr_none_when_no_sign_change():
    assert E.irr([10, 20, 30]) is None


@pytest.mark.parametrize("r", [0.0, 0.05, 0.12, 0.3])
def test_npv_matches_numpy_financial(r):
    cf = [-500, 100, 150, 200, 250]
    assert E.npv(r, cf) == pytest.approx(npf.npv(r, cf), rel=1e-12)


def test_annuity_matches_pmt():
    assert E.annuity_payment(1_000_000, 0.08, 12) == pytest.approx(-npf.pmt(0.08, 12, 1_000_000), rel=1e-12)


def test_project_debt_fully_amortised_and_dscr_identity():
    p = E.ProjectInputs(capex=[60e6, 40e6], revenue_y1=25e6, ramp=[0.5, 0.8, 1.0],
                        debt_pct=0.6, debt_rate=0.08, debt_tenor=12, operating_years=20)
    r = E.run_project(p)
    pay = E.annuity_payment(60e6, 0.08, 12)
    assert sum(r.debt_service) == pytest.approx(pay * 12, rel=1e-6)
    # NPV of debt service at debt rate == principal (amortisation identity)
    ds = [d for d in r.debt_service if d > 0]
    assert sum(d / 1.08 ** (i + 1) for i, d in enumerate(ds)) == pytest.approx(60e6, rel=1e-6)
    for c, d, s in zip(r.cfads, r.debt_service, r.dscr):
        if d > 0:
            assert s == pytest.approx(c / d, rel=1e-4)


def test_leverage_raises_equity_irr_when_project_irr_above_debt_cost():
    base = dict(capex=[100e6], revenue_y1=22e6, ramp=[1.0], debt_rate=0.07, operating_years=20)
    unlev = E.run_project(E.ProjectInputs(**base, debt_pct=0.0))
    lev = E.run_project(E.ProjectInputs(**base, debt_pct=0.6))
    assert unlev.project_irr > 0.07
    assert lev.equity_irr > unlev.equity_irr  # Modigliani-Miller sign check


def test_tornado_sorted_by_swing():
    p = E.ProjectInputs(capex=[100e6], revenue_y1=20e6, ramp=[1.0])
    rows = E.tornado(p, {"revenue_y1": (0.8, 1.2), "debt_rate": (0.9, 1.1)})
    assert rows[0]["swing"] >= rows[1]["swing"]
    assert rows[0]["driver"] == "revenue_y1"


# ---------------------------------------------------------------- Monte Carlo
def test_barrier_closed_form_limits():
    assert MC.barrier_hit_prob(100, 100, 0.05, 0.2, 1) == 1.0
    assert MC.barrier_hit_prob(100, 1, 0.05, 0.2, 1) < 1e-6


@pytest.mark.parametrize("sigma,ltv_call,ltv0", [(0.15, 0.70, 0.55), (0.25, 0.65, 0.50), (0.10, 0.60, 0.50)])
def test_lombard_mc_matches_closed_form_barrier(sigma, ltv_call, ltv0):
    """No jumps, no income, zero loan rate => loan constant, call = GBM hitting fixed barrier.
    Daily-monitored MC vs continuous closed form with BGK-shifted barrier."""
    v0, L = 100.0, 100.0 * ltv0
    p = MC.LombardInputs(portfolio_value=v0, loan=L, mu=0.05, sigma=sigma, income_yield=0.0,
                         loan_rate=0.0, ltv_call=ltv_call, ltv_liquidate=0.999, horizon_years=1.0,
                         n_paths=60000, seed=3)
    out = MC.simulate_lombard(p)
    B = L / ltv_call
    dt = 1 / 252
    ref = MC.barrier_hit_prob(v0, B * math.exp(-MC.BGK * sigma * math.sqrt(dt)), 0.05, sigma, 1.0)
    se = math.sqrt(max(ref * (1 - ref), 1e-4) / p.n_paths)
    assert abs(out["p_margin_call"] - ref) < max(5 * se, 0.003), (out["p_margin_call"], ref)


def test_jumps_increase_call_probability():
    base = dict(portfolio_value=100, loan=50, sigma=0.12, ltv_call=0.65, ltv_liquidate=0.8, n_paths=20000)
    a = MC.simulate_lombard(MC.LombardInputs(**base))
    b = MC.simulate_lombard(MC.LombardInputs(**base, jump_lambda=1.0, jump_mean=-0.15, jump_std=0.1))
    assert b["p_margin_call"] > a["p_margin_call"]


def test_project_mc_runs_and_orders_percentiles():
    p = E.ProjectInputs(capex=[60e6, 40e6], revenue_y1=25e6, ramp=[0.5, 0.8, 1.0])
    r = MC.simulate_project(p, MC.ProjectMCSpec(n=400))
    assert r["irr_p10"] <= r["irr_p50"] <= r["irr_p90"]
    assert 0 <= r["p_irr_below_hurdle"] <= 1


# ---------------------------------------------------------------- gate
THR = json.load(open(os.path.join(HERE, "..", "config", "gate_thresholds.json")))


def test_gate_proceed_conditional_decline():
    good = {"asset_class": "lombard", "country": "AE",
            "metrics": {"carry_spread": 0.03, "p_margin_call": 0.02, "p_forced_liquidation": 0.0,
                        "ltv0": 0.4, "ltv0_lending": 0.5, "cvar_95": 0.2}}
    assert G.evaluate(good, THR)["verdict"] == "PROCEED"
    soft = json.loads(json.dumps(good)); soft["metrics"]["p_margin_call"] = 0.055
    assert G.evaluate(soft, THR)["verdict"] == "CONDITIONAL"
    bad = json.loads(json.dumps(good)); bad["metrics"]["p_margin_call"] = 0.20
    assert G.evaluate(bad, THR)["verdict"] == "DECLINE"


def test_gate_country_uplift_and_kill_risk():
    m = {"equity_irr": 0.17, "min_dscr_amort": 1.4, "llcr": 1.5, "p_equity_loss": 0.05,
         "p_irr_below_hurdle": 0.2, "p_dscr_breach": 0.05}
    ae = G.evaluate({"asset_class": "dc_digital_infra", "country": "AE", "metrics": m}, THR)
    ng = G.evaluate({"asset_class": "dc_digital_infra", "country": "NG", "metrics": m}, THR)
    assert ae["verdict"] == "PROCEED"
    assert ng["verdict"] != "PROCEED"          # 17% < 14% + 6% Nigeria uplift
    killed = G.evaluate({"asset_class": "dc_digital_infra", "country": "AE", "metrics": m,
                         "kill_risks": [{"risk": "no grid connection agreement", "mitigated": False}]}, THR)
    assert killed["verdict"] == "DECLINE"


def test_gate_data_gap_caps_at_conditional():
    r = G.evaluate({"asset_class": "lombard", "country": "AE",
                    "metrics": {"carry_spread": 0.03, "p_margin_call": 0.01, "ltv0": 0.4}}, THR)
    assert r["verdict"] == "CONDITIONAL" and set(r["data_gaps"]) == {"p_forced_liquidation", "cvar_95", "ltv0_lending"}


def test_grace_period_interest_only_then_amortises():
    p = E.ProjectInputs(capex=[100e6], revenue_y1=20e6, ramp=[0.4, 1.0], debt_pct=0.5,
                        debt_rate=0.08, debt_tenor=10, debt_grace_years=2)
    r = E.run_project(p)
    assert r.debt_service[1] == pytest.approx(50e6 * 0.08)
    assert r.debt_service[2] == pytest.approx(50e6 * 0.08)
    assert r.debt_service[3] == pytest.approx(E.annuity_payment(50e6, 0.08, 10))
    assert r.debt_service[13] == 0


# ---------------------------------------------------------------- iteration 2
def test_capitalised_grace_interest_rolls_up_then_amortises_larger_balance():
    p = E.ProjectInputs(capex=[100e6], revenue_y1=20e6, ramp=[0.4, 1.0], debt_pct=0.5, debt_rate=0.08,
                        debt_tenor=10, debt_grace_years=2, capitalise_grace_interest=True)
    r = E.run_project(p)
    assert r.debt_service[1] == 0 and r.debt_service[2] == 0
    rolled = 50e6 * 1.08 ** 2
    assert r.debt_service[3] == pytest.approx(-npf.pmt(0.08, 10, rolled), rel=1e-9)


def test_mortgage_drawn_at_handover_with_balloon_at_sale():
    """Off-plan: capex years 0-1, 50% mortgage drawn at t=2 (handover/COD), 25y tenor, sold after 5 operating years.
    Balloon must equal the independent closed-form outstanding balance after 5 annuity payments."""
    P, r, n = 50.0, 0.046, 25
    p = E.ProjectInputs(capex=[40.0, 60.0], revenue_y1=5.0, ramp=[1.0], debt_pct=0.5, debt_rate=r,
                        debt_tenor=n, debt_draw_year=2, operating_years=5, terminal_multiple_ebitda=20)
    res = E.run_project(p)
    pmt = -npf.pmt(r, n, P)
    expected_bal = P * (1 + r) ** 5 - pmt * ((1 + r) ** 5 - 1) / r
    assert res.balloon == pytest.approx(expected_bal, rel=1e-9)
    assert res.equity_cf[0] == pytest.approx(-40.0) and res.equity_cf[1] == pytest.approx(-60.0)
    assert res.llcr is None          # meaningless when loan repaid at sale


def test_dsra_is_cash_neutral_undiscounted_and_lowers_irr():
    base = dict(capex=[100e6], revenue_y1=22e6, ramp=[1.0], debt_pct=0.6, debt_rate=0.08, debt_tenor=10)
    a = E.run_project(E.ProjectInputs(**base))
    b = E.run_project(E.ProjectInputs(**base, dsra_months=6))
    assert sum(b.equity_cf) == pytest.approx(sum(a.equity_cf), rel=1e-9)
    assert b.equity_irr < a.equity_irr


def test_min_dscr_amort_excludes_grace_years():
    p = E.ProjectInputs(capex=[100e6], revenue_y1=20e6, ramp=[0.3, 1.0], debt_pct=0.6, debt_rate=0.08,
                        debt_tenor=10, debt_grace_years=1)
    r = E.run_project(p)
    assert r.min_dscr < r.min_dscr_amort
    assert r.ticket == pytest.approx(r.equity_invested)


def test_lombard_lending_value_equivalent_to_scaled_market_ltv():
    base = dict(portfolio_value=100, loan=40, sigma=0.15, income_yield=0, loan_rate=0, n_paths=20000, seed=5)
    a = MC.simulate_lombard(MC.LombardInputs(**base, ltv_call=0.65, ltv_liquidate=0.75, advance_rate=0.76))
    b = MC.simulate_lombard(MC.LombardInputs(**base, ltv_call=0.65 * 0.76, ltv_liquidate=0.75 * 0.76))
    assert a["p_margin_call"] == pytest.approx(b["p_margin_call"], abs=1e-12)
    assert a["ltv0_lending"] == pytest.approx(40 / 76)


def test_lombard_external_interest_keeps_loan_flat_and_haircut_cuts_raise_calls():
    base = dict(portfolio_value=2.2, loan=1.0, sigma=0.11, loan_rate=0.055, advance_rate=0.76,
                ltv_call=0.65, ltv_liquidate=0.75, n_paths=20000)
    a = MC.simulate_lombard(MC.LombardInputs(**base, interest_paid_externally=True))
    b = MC.simulate_lombard(MC.LombardInputs(**base, interest_paid_externally=True, haircut_cut=0.10,
                                             haircut_trigger_dd=0.05, haircut_cut_prob=1.0))
    assert b["p_margin_call"] > a["p_margin_call"]
    assert b["immediate_call_if_haircut_cut"] is True       # 1.0/(2.2*0.66)=0.689 >= 0.65
    c = MC.simulate_lombard(MC.LombardInputs(**base, proceeds_yield=0.09, proceeds_credit_loss=0.012))
    assert c["carry_spread"] == pytest.approx(0.09 - 0.012 - 0.055)


def test_project_mc_can_produce_losses_with_tenant_default_and_fx():
    p = E.ProjectInputs(capex=[60e6, 35e6], revenue_y1=19.2e6, ramp=[0.4, 0.75, 0.95], debt_pct=0.65,
                        debt_rate=0.09, debt_tenor=10, debt_grace_years=2, terminal_multiple_ebitda=8)
    calm = MC.simulate_project(p, MC.ProjectMCSpec(n=600))
    hard = MC.simulate_project(p, MC.ProjectMCSpec(n=600, tenant_default_prob=0.4, relet_years=4,
                                                   fx_deval_prob_yr=0.15, fx_local_share=0.6, exit_multiple_sd=0.3))
    assert hard["p_equity_loss"] > calm["p_equity_loss"]
    assert hard["p_equity_loss"] > 0


def test_gate_benchmark_excess_not_country_uplifted():
    m = {"equity_irr": 0.13, "net_yield": 0.065, "p_equity_loss": 0.05, "ltv": 0.5, "excess_vs_benchmark": 0.02}
    r = G.evaluate({"asset_class": "real_estate", "country": "AE", "metrics": m}, THR)
    chk = {c["metric"]: c for c in r["checks"]}
    assert chk["excess_vs_benchmark"]["rule"] == "min 0.03" and chk["excess_vs_benchmark"]["status"] == "FAIL"


def test_build_xlsx_reconciles_all_engine_features(tmp_path):
    """End-to-end proof: Excel (LibreOffice recalc) == Python for capitalised IDC, DSRA, grace, handover
    mortgage with balloon, and the Lombard mechanics workbook."""
    import build_xlsx as B
    p1 = E.ProjectInputs(capex=[120e6, 80e6], revenue_y1=42e6, ramp=[0.4, 0.75, 0.95], tax_rate=0.09,
                         debt_tenor=12, debt_grace_years=2, capitalise_grace_interest=True, dsra_months=6,
                         terminal_multiple_ebitda=9, equity_share=0.25)
    p2 = E.ProjectInputs(capex=[1.4e6, 2.0e6], revenue_y1=180000, ramp=[0.95], debt_pct=0.45, debt_rate=0.046,
                         debt_tenor=25, debt_draw_year=2, operating_years=6, terminal_multiple_ebitda=22,
                         depreciation_years=0, currency="AED")
    for i, p in enumerate((p1, p2)):
        out = str(tmp_path / f"p{i}.xlsx")
        B.build(p, {}, out, None, None, {"down": {"revenue_y1": p.revenue_y1 * 0.8}})
        rep = B.recalc_and_reconcile(out)
        assert rep["ok"], rep
    out = str(tmp_path / "l.xlsx")
    B.build_lombard(MC.LombardInputs(portfolio_value=2.2e6, loan=1e6, advance_rate=0.76, proceeds_yield=0.09),
                    B.DEFAULT_STRESSES, out, None, None)
    assert B.recalc_and_reconcile(out)["ok"]


def test_solve_for_rent_hits_target_irr():
    p = E.ProjectInputs(capex=[95e6], revenue_y1=19.2e6, ramp=[0.4, 0.75, 0.95], debt_tenor=10, debt_grace_years=2)
    rent = E.solve_for(p, "revenue_y1", "equity_irr", 0.20, 5e6, 80e6)
    from dataclasses import replace
    assert E.run_project(replace(p, revenue_y1=rent)).equity_irr == pytest.approx(0.20, abs=1e-5)
    m = E.scale_capex_solve(p, "equity_irr", 0.10)
    assert E.run_project(replace(p, capex=[c * m for c in p.capex])).equity_irr == pytest.approx(0.10, abs=1e-5)


def test_max_loan_for_gate_is_feasible_and_tight():
    p = MC.LombardInputs(portfolio_value=2.2e6, loan=1e6, advance_rate=0.76, sigma=0.11, mu=0.055,
                         loan_rate=0.0548, interest_paid_externally=True, n_paths=8000)
    r = MC.max_loan_for_gate(p)
    from dataclasses import replace
    at = MC.simulate_lombard(replace(p, loan=r["max_loan"]))
    above = MC.simulate_lombard(replace(p, loan=r["max_loan"] + 5000))
    assert at["p_margin_call"] <= 0.05 and at["ltv0_lending"] <= 0.55
    assert above["p_margin_call"] > 0.05 or above["ltv0_lending"] > 0.55 or above["p_forced_liquidation"] > 0.01
