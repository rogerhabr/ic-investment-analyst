"""
montecarlo.py — path-dependent risk for ic-investment-analyst.

A. Lombard / leveraged portfolio (path-dependent: a down-and-out barrier)
   V_t   = collateral value, GBM with optional Merton jumps
           dV/V = (mu - q - lam*k) dt + sigma dW + (J-1) dN,  ln J ~ N(mj, sj^2)
   L_t   = loan, accrues at r_loan (interest capitalised unless paid from income)
   LTV_t = L_t / V_t
   Margin call    when LTV_t >= ltv_call
   Forced sale    when LTV_t >= ltv_liquidate  (sale at that day's price, equity locked)
   Closed-form check (no jumps, no income, loan fixed):
     P(min V <= B by T) = N((ln(B/V0) - nu T)/(s sqrt T)) + (B/V0)^(2nu/s^2) N((ln(B/V0) + nu T)/(s sqrt T)),
     nu = mu - s^2/2.  Discrete daily monitoring is corrected with Broadie-Glasserman-Kou:
     B_eff = B * exp(0.5826 * s * sqrt(dt)).

B. Project Monte Carlo — samples engine.ProjectInputs drivers and reports
   P(equity IRR < hurdle), P(equity loss), P(min DSCR < covenant), percentiles.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, asdict

import numpy as np

try:
    from engine import ProjectInputs, run_project
except ImportError:  # pragma: no cover
    from .engine import ProjectInputs, run_project

BGK = 0.5826  # Broadie-Glasserman-Kou continuity correction constant


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def barrier_hit_prob(v0: float, barrier: float, mu: float, sigma: float, T: float) -> float:
    """Continuous-monitoring probability that GBM falls to barrier (< v0) within T."""
    if barrier >= v0:
        return 1.0
    nu = mu - 0.5 * sigma ** 2
    x = math.log(barrier / v0)
    st = sigma * math.sqrt(T)
    return norm_cdf((x - nu * T) / st) + (barrier / v0) ** (2 * nu / sigma ** 2) * norm_cdf((x + nu * T) / st)


@dataclass
class LombardInputs:
    portfolio_value: float          # V0
    loan: float                     # L0
    mu: float = 0.06                # expected total return of collateral (annual)
    sigma: float = 0.12             # annual vol
    income_yield: float = 0.02      # dividends/coupons, used to pay interest first
    loan_rate: float = 0.06         # all-in (base + margin)
    ltv_call: float = 0.65          # lender margin-call LTV (lender-specific; usually on haircut value)
    ltv_liquidate: float = 0.75     # forced-sale LTV
    horizon_years: float = 1.0
    steps_per_year: int = 252
    jump_lambda: float = 0.0        # jumps/year (0 = pure GBM)
    jump_mean: float = -0.10        # mean log jump
    jump_std: float = 0.08
    n_paths: int = 20000
    seed: int = 7
    advance_rate: float = 1.0       # weighted lender advance rate; LTV tested on lending value = V * advance_rate
                                    # (1.0 = test on market value)
    interest_paid_externally: bool = False  # interest paid from cash outside the pledged account (loan stays flat)
    haircut_cut: float = 0.0        # absolute cut to advance_rate the lender may impose in stress (e.g. 0.10)
    haircut_trigger_dd: float = 0.15  # portfolio drawdown from start that makes a cut possible
    haircut_cut_prob: float = 0.0   # prob the lender actually cuts once the trigger is hit
    proceeds_yield: float | None = None    # yield on what the loan buys (use of proceeds)
    proceeds_credit_loss: float = 0.0      # expected annual loss on the proceeds asset


def simulate_lombard(p: LombardInputs) -> dict:
    rng = np.random.default_rng(p.seed)
    n_steps = int(round(p.horizon_years * p.steps_per_year))
    dt = 1.0 / p.steps_per_year
    k = math.exp(p.jump_mean + 0.5 * p.jump_std ** 2) - 1
    drift = (p.mu - p.income_yield - p.jump_lambda * k - 0.5 * p.sigma ** 2) * dt

    V = np.full(p.n_paths, p.portfolio_value, float)
    adv = np.full(p.n_paths, p.advance_rate, float)
    cut_done = np.zeros(p.n_paths, bool)
    ext_interest = np.zeros(p.n_paths, float)
    L = np.full(p.n_paths, p.loan, float)
    alive = np.ones(p.n_paths, bool)
    called = np.zeros(p.n_paths, bool)
    first_call_t = np.full(p.n_paths, np.nan)
    liquidated = np.zeros(p.n_paths, bool)
    equity_at_liq = np.full(p.n_paths, np.nan)
    min_V = V.copy()

    for i in range(n_steps):
        z = rng.standard_normal(p.n_paths)
        logret = drift + p.sigma * math.sqrt(dt) * z
        if p.jump_lambda > 0:
            nj = rng.poisson(p.jump_lambda * dt, p.n_paths)
            logret += nj * p.jump_mean + np.sqrt(nj) * p.jump_std * rng.standard_normal(p.n_paths)
        V = np.where(alive, V * np.exp(logret), V)
        # income pays interest; shortfall capitalises; surplus reinvested
        interest = L * p.loan_rate * dt
        income = V * p.income_yield * dt
        if p.interest_paid_externally:
            ext_interest += np.where(alive, interest, 0)
            V = np.where(alive, V + income, V)           # income reinvested, loan flat
        else:
            L = np.where(alive, L + np.maximum(interest - income, 0), L)
            V = np.where(alive, V + np.maximum(income - interest, 0), V)
        min_V = np.minimum(min_V, V)
        if p.haircut_cut > 0 and p.haircut_cut_prob > 0:
            trig = alive & ~cut_done & (V / p.portfolio_value - 1 <= -p.haircut_trigger_dd)
            hit = trig & (rng.random(p.n_paths) < p.haircut_cut_prob)
            adv = np.where(hit, np.maximum(adv - p.haircut_cut, 0.01), adv)
            cut_done |= trig
        ltv = L / (V * adv)
        new_call = alive & ~called & (ltv >= p.ltv_call)
        first_call_t[new_call] = (i + 1) * dt
        called |= new_call
        liq = alive & (ltv >= p.ltv_liquidate)
        equity_at_liq[liq] = V[liq] - L[liq]
        liquidated |= liq
        alive &= ~liq

    eq0 = p.portfolio_value - p.loan
    eq_T = np.where(liquidated, equity_at_liq, V - L) - ext_interest
    ret = eq_T / eq0 - 1
    q05 = float(np.quantile(ret, 0.05))
    q01 = float(np.quantile(ret, 0.01))
    unlev_ret_mean = p.mu * p.horizon_years
    return {
        "inputs": asdict(p),
        "ltv0": p.loan / p.portfolio_value,                       # on market value
        "ltv0_lending": p.loan / (p.portfolio_value * p.advance_rate),  # what the lender tests
        "drawdown_to_call": 1 - (p.loan / (p.ltv_call * p.advance_rate)) / p.portfolio_value,
        "drawdown_to_liquidation": 1 - (p.loan / (p.ltv_liquidate * p.advance_rate)) / p.portfolio_value,
        "immediate_call_if_haircut_cut": (p.loan / (p.portfolio_value * max(p.advance_rate - p.haircut_cut, 0.01))
                                          >= p.ltv_call) if p.haircut_cut > 0 else None,
        "p_haircut_cut": float((adv < p.advance_rate).mean()),
        "p_margin_call": float(called.mean()),
        "p_forced_liquidation": float(liquidated.mean()),
        "median_days_to_first_call": float(np.nanmedian(first_call_t) * 252) if called.any() else None,
        "equity_return_mean": float(ret.mean()),
        "equity_return_median": float(np.median(ret)),
        "var_95": -q05,
        "cvar_95": float(-ret[ret <= q05].mean()),
        "var_99": -q01,
        "p_equity_loss": float((ret < 0).mean()),
        # carry on what the loan BUYS (the economic trade); falls back to collateral return if not given
        "carry_spread": ((p.proceeds_yield - p.proceeds_credit_loss - p.loan_rate)
                         if p.proceeds_yield is not None else p.mu - p.loan_rate),
        "carry_spread_collateral": p.mu - p.loan_rate,
        "unlevered_expected_return": unlev_ret_mean,
        "closed_form_p_call_no_jumps": barrier_hit_prob(
            p.portfolio_value, p.loan / (p.ltv_call * p.advance_rate),
            p.mu - (0 if p.interest_paid_externally else p.income_yield), p.sigma, p.horizon_years)
            if p.jump_lambda == 0 and p.haircut_cut == 0 else None,
    }


# ------------------------------------------------------------ project MC
@dataclass
class ProjectMCSpec:
    revenue_sd: float = 0.12        # lognormal sd on revenue_y1
    capex_overrun_mean: float = 0.08
    capex_overrun_sd: float = 0.10  # right-skewed: lognormal on (1+overrun)
    opex_sd: float = 0.10
    rate_sd: float = 0.01           # absolute bp-type shock on debt rate
    ramp_delay_prob: float = 0.20   # prob ramp slips one year
    hurdle: float = 0.14
    dscr_covenant: float = 1.20
    # discrete events — without these the MC can't produce a loss on a deal with exit value
    tenant_default_prob: float = 0.0   # prob anchor defaults at some point in the life
    tenant_share: float = 0.6          # share of revenue the anchor represents
    relet_years: int = 2               # years to re-let the anchor's capacity
    fx_deval_prob_yr: float = 0.0      # annual prob of a step devaluation
    fx_deval_size: float = 0.30        # size of each step (local currency loss vs USD)
    fx_local_share: float = 0.0        # share of revenue effectively exposed to local currency
    exit_multiple_sd: float = 0.0      # lognormal sd on the exit multiple
    n: int = 3000
    seed: int = 11


def simulate_project(base: ProjectInputs, s: ProjectMCSpec) -> dict:
    rng = np.random.default_rng(s.seed)
    irrs, mins, npvs, moics = [], [], [], []
    for _ in range(s.n):
        d = asdict(base)
        d["revenue_y1"] *= float(np.exp(rng.normal(-0.5 * s.revenue_sd ** 2, s.revenue_sd)))
        over = float(np.exp(rng.normal(math.log(1 + s.capex_overrun_mean) - 0.5 * s.capex_overrun_sd ** 2,
                                       s.capex_overrun_sd)))
        d["capex"] = [c * over for c in d["capex"]]
        d["opex_pct_revenue"] *= float(np.exp(rng.normal(0, s.opex_sd)))
        d["debt_rate"] = max(0.0, d["debt_rate"] + rng.normal(0, s.rate_sd))
        ops = d["operating_years"]
        ramp = list(d["ramp"])
        if rng.random() < s.ramp_delay_prob:
            ramp = [0.0] + ramp
        ramp = [ramp[min(k, len(ramp) - 1)] for k in range(ops)]   # explicit per-year vector
        if s.tenant_default_prob > 0 and rng.random() < s.tenant_default_prob:
            y = int(rng.integers(0, ops))
            for k in range(y, min(ops, y + s.relet_years)):
                ramp[k] *= (1 - s.tenant_share)
        if s.fx_deval_prob_yr > 0 and s.fx_local_share > 0:
            mult = 1.0
            for k in range(ops):
                if rng.random() < s.fx_deval_prob_yr:
                    mult *= 1 - s.fx_deval_size * s.fx_local_share
                ramp[k] *= mult
        d["ramp"] = ramp
        if s.exit_multiple_sd > 0 and d["terminal_multiple_ebitda"] > 0:
            d["terminal_multiple_ebitda"] *= float(np.exp(rng.normal(-0.5 * s.exit_multiple_sd ** 2,
                                                                     s.exit_multiple_sd)))
        r = run_project(ProjectInputs(**d))
        irrs.append(np.nan if r.equity_irr is None else r.equity_irr)
        mins.append(np.nan if r.min_dscr_amort is None else r.min_dscr_amort)
        moics.append(np.nan if r.moic is None else r.moic)
        npvs.append(r.equity_npv)
    irrs, mins, npvs = map(np.asarray, (irrs, mins, npvs))
    finite = irrs[~np.isnan(irrs)]
    return {
        "n": s.n,
        "p_irr_below_hurdle": float(np.mean(np.nan_to_num(irrs, nan=-1) < s.hurdle)),
        # loss = money back < money in (MOIC < 1); IRR<0 is equivalent but undefined on some paths
        "p_equity_loss": float(np.mean(np.nan_to_num(np.asarray(moics), nan=0) < 1)),
        "p_dscr_breach": float(np.nanmean(mins < s.dscr_covenant)) if np.isfinite(mins).any() else None,
        "irr_p10": float(np.quantile(finite, 0.10)) if finite.size else None,
        "irr_p50": float(np.quantile(finite, 0.50)) if finite.size else None,
        "irr_p90": float(np.quantile(finite, 0.90)) if finite.size else None,
        "npv_p10": float(np.quantile(npvs, 0.10)),
        "npv_p50": float(np.quantile(npvs, 0.50)),
        "spec": asdict(s),
    }


if __name__ == "__main__":
    spec = json.load(open(sys.argv[1]))
    out = {}
    if "lombard" in spec:
        out["lombard"] = simulate_lombard(LombardInputs(**spec["lombard"]))
    if "project" in spec:
        out["project_mc"] = simulate_project(ProjectInputs(**spec["project"]),
                                             ProjectMCSpec(**spec.get("project_mc", {})))
    json.dump(out, sys.stdout, indent=2)


def max_loan_for_gate(p: LombardInputs, p_call_max: float = 0.05, ltv_lending_max: float = 0.55,
                      p_liq_max: float = 0.01, cvar_max: float = 0.35, tol: float = 1000.0) -> dict:
    """Largest loan that passes the Lombard gate limits (monotone in loan size -> bisection).
    Uses common random numbers (fixed seed) so the search is smooth."""
    from dataclasses import replace
    def passes(L):
        r = simulate_lombard(replace(p, loan=L))
        return (r["p_margin_call"] <= p_call_max and r["ltv0_lending"] <= ltv_lending_max
                and r["p_forced_liquidation"] <= p_liq_max and r["cvar_95"] <= cvar_max), r
    lo, hi = 0.0, p.portfolio_value * p.advance_rate * p.ltv_call
    ok_hi, _ = passes(hi)
    ok_lo, _ = passes(tol)
    if not ok_lo:
        return {"max_loan": 0.0, "binding": "no positive loan passes the limits"}
    if ok_hi:
        return {"max_loan": hi, "binding": "call LTV at t0"}
    while hi - lo > tol:
        mid = (lo + hi) / 2
        ok, _ = passes(mid)
        lo, hi = (mid, hi) if ok else (lo, mid)
    _, r = passes(lo)
    binding = min((("p_margin_call", p_call_max - r["p_margin_call"]),
                   ("ltv0_lending", ltv_lending_max - r["ltv0_lending"]),
                   ("p_forced_liquidation", p_liq_max - r["p_forced_liquidation"]),
                   ("cvar_95", cvar_max - r["cvar_95"])), key=lambda x: x[1])[0]
    return {"max_loan": lo, "binding": binding, "at_max": {k: r[k] for k in
            ("ltv0_lending", "p_margin_call", "p_forced_liquidation", "carry_spread")},
            "annual_carry_at_max": lo * r["carry_spread"]}
