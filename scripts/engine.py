"""
engine.py — deterministic valuation core for ic-investment-analyst.

Formulas (shown first, per house style):
  NPV(r)   = sum_t CF_t / (1+r)^t
  IRR      = r such that NPV(r) = 0           (Brent root-find, bracketed)
  DSCR_t   = CFADS_t / DebtService_t
  LLCR     = PV(CFADS over loan life, @ debt rate) / Debt outstanding at t0
  MOIC     = sum(positive equity CF) / sum(|negative equity CF|)
  Annuity  = P * r / (1 - (1+r)^-n)

Only numpy is required at runtime so the skill runs anywhere.
CLI:  python engine.py deal.json  -> prints results JSON
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field, asdict
from typing import Callable, Sequence

import numpy as np


# ---------------------------------------------------------------- primitives
def npv(rate: float, cashflows: Sequence[float]) -> float:
    cf = np.asarray(cashflows, dtype=float)
    t = np.arange(cf.size)
    return float(np.sum(cf / (1.0 + rate) ** t))


def irr(cashflows: Sequence[float], lo: float = -0.99, hi: float = 10.0,
        tol: float = 1e-10, maxiter: int = 500) -> float | None:
    """Brent's method on NPV. Returns None when no sign change exists
    (e.g. all-positive flows) — never a fabricated number."""
    cf = list(cashflows)
    if not (any(c < 0 for c in cf) and any(c > 0 for c in cf)):
        return None
    f = lambda r: npv(r, cf)
    # scan for a bracket — handles multiple sign changes by taking the first root above lo
    grid = np.concatenate([np.linspace(lo, 1.0, 400), np.linspace(1.0, hi, 200)[1:]])
    vals = [f(r) for r in grid]
    for a, b, fa, fb in zip(grid[:-1], grid[1:], vals[:-1], vals[1:]):
        if fa == 0:
            return float(a)
        if fa * fb < 0:
            return _brent(f, a, b, fa, fb, tol, maxiter)
    return None


def _brent(f: Callable[[float], float], a, b, fa, fb, tol, maxiter) -> float:
    if abs(fa) < abs(fb):
        a, b, fa, fb = b, a, fb, fa
    c, fc, d, mflag = a, fa, 0.0, True
    for _ in range(maxiter):
        if fa != fc and fb != fc:
            s = (a * fb * fc / ((fa - fb) * (fa - fc))
                 + b * fa * fc / ((fb - fa) * (fb - fc))
                 + c * fa * fb / ((fc - fa) * (fc - fb)))
        else:
            s = b - fb * (b - a) / (fb - fa)
        cond = (not ((3 * a + b) / 4 < s < b or b < s < (3 * a + b) / 4)
                or (mflag and abs(s - b) >= abs(b - c) / 2)
                or (not mflag and abs(s - b) >= abs(c - d) / 2))
        if cond:
            s, mflag = (a + b) / 2, True
        else:
            mflag = False
        fs = f(s)
        d, c, fc = c, b, fb
        if fa * fs < 0:
            b, fb = s, fs
        else:
            a, fa = s, fs
        if abs(fa) < abs(fb):
            a, b, fa, fb = b, a, fb, fa
        if abs(b - a) < tol or fb == 0:
            return float(b)
    return float(b)


def annuity_payment(principal: float, rate: float, n: int) -> float:
    if n <= 0:
        return 0.0
    if rate == 0:
        return principal / n
    return principal * rate / (1 - (1 + rate) ** -n)


def moic(cashflows: Sequence[float]) -> float | None:
    cf = np.asarray(cashflows, float)
    out = -cf[cf < 0].sum()
    return None if out == 0 else float(cf[cf > 0].sum() / out)


def payback_year(cashflows: Sequence[float]) -> float | None:
    cum = np.cumsum(cashflows)
    for i in range(1, len(cum)):
        if cum[i - 1] < 0 <= cum[i]:
            return float(i - 1 + (-cum[i - 1]) / (cum[i] - cum[i - 1]))
    return None


# --------------------------------------------------------- project model
@dataclass
class ProjectInputs:
    """Generic contracted-infrastructure model (DC / energy / powered land / RE).
    All money in base currency (USD default), annual periods, t=0 = financial close."""
    capex: list[float]                     # capex by year from t=0 (positive numbers)
    revenue_y1: float                      # revenue in first operating year at 100% ramp
    ramp: list[float]                      # utilisation by operating year, last value held
    revenue_escalation: float = 0.02
    opex_pct_revenue: float = 0.30
    fixed_opex: float = 0.0
    opex_escalation: float = 0.025
    tax_rate: float = 0.0
    depreciation_years: int = 15
    operating_years: int = 20
    debt_pct: float = 0.60                 # of total capex
    debt_rate: float = 0.08
    debt_tenor: int = 12                   # years of amortisation after the grace period
    debt_grace_years: int = 0              # interest-only years after COD (ramp protection)
    terminal_multiple_ebitda: float = 0.0  # exit value at end, 0 = none
    fx_haircut: float = 0.0                # % revenue lost to FX (local-currency revenue)
    discount_rate: float = 0.12            # equity hurdle used for NPV
    name: str = "deal"
    currency: str = "USD"                  # label only; all fields must be in this currency
    equity_share: float = 1.0              # investor's share of equity (ticket = equity x share)
    capitalise_grace_interest: bool = False  # grace-period interest rolls into the loan (no cash DS)
    debt_draw_year: int | None = None      # None = drawn pro-rata with capex; int = drawn in full at that t
    dsra_months: float = 0.0               # debt service reserve funded at COD (months of first amortising DS),
                                           # funded by equity, released at debt maturity / exit


@dataclass
class ProjectResults:
    years: list[int]
    revenue: list[float]
    ebitda: list[float]
    cfads: list[float]
    debt_service: list[float]
    dscr: list[float | None]
    project_cf: list[float]
    equity_cf: list[float]
    project_irr: float | None
    equity_irr: float | None
    equity_npv: float
    min_dscr: float | None
    min_dscr_amort: float | None           # min DSCR over amortising years only (excludes grace)
    avg_dscr: float | None
    llcr: float | None
    moic: float | None
    payback_year: float | None
    total_capex: float
    debt_amount: float
    equity_invested: float                 # sum of negative equity CF (100%)
    ticket: float                          # equity_invested x equity_share
    balloon: float                         # debt repaid at exit (0 if fully amortised)
    currency: str


def run_project(p: ProjectInputs) -> ProjectResults:
    build = len(p.capex)
    horizon = build + p.operating_years
    years = list(range(horizon))
    capex = np.zeros(horizon)
    capex[:build] = p.capex
    total_capex = float(capex.sum())
    debt = total_capex * p.debt_pct

    revenue = np.zeros(horizon)
    opex = np.zeros(horizon)
    for k in range(p.operating_years):
        t = build + k
        util = p.ramp[min(k, len(p.ramp) - 1)]
        revenue[t] = p.revenue_y1 * util * (1 + p.revenue_escalation) ** k * (1 - p.fx_haircut)
        opex[t] = revenue[t] * p.opex_pct_revenue + p.fixed_opex * (1 + p.opex_escalation) ** k
    ebitda = revenue - opex

    dep = np.zeros(horizon)
    annual_dep = total_capex / p.depreciation_years if p.depreciation_years else 0
    for k in range(min(p.depreciation_years, p.operating_years)):
        dep[build + k] = annual_dep

    # ---- debt
    # draw: pro-rata with capex, or in full at debt_draw_year (e.g. mortgage at handover)
    draw = np.zeros(horizon)
    if p.debt_draw_year is None:
        draw = capex * p.debt_pct
    else:
        draw[min(p.debt_draw_year, horizon - 1)] = debt
    # service starts at COD (t=build): grace years (interest-only, or capitalised), then annuity
    balance = debt
    interest = np.zeros(horizon)
    debt_service = np.zeros(horizon)
    amortising = np.zeros(horizon, bool)
    loan_life = p.debt_grace_years + p.debt_tenor
    pay = None
    for k in range(loan_life):
        t = build + k
        if t >= horizon:
            break
        i = balance * p.debt_rate
        interest[t] = i
        if k < p.debt_grace_years:
            if p.capitalise_grace_interest:
                balance += i                    # rolls up, no cash debt service
            else:
                debt_service[t] = i
        else:
            if pay is None:                     # size annuity on balance at end of grace
                pay = annuity_payment(balance, p.debt_rate, p.debt_tenor)
            debt_service[t] = pay
            amortising[t] = True
            balance -= pay - i
    balloon = max(balance, 0.0) if build + loan_life > horizon else 0.0

    taxable = ebitda - dep - interest
    tax = np.where(taxable > 0, taxable * p.tax_rate, 0.0)
    cfads = ebitda - tax

    terminal = np.zeros(horizon)
    if p.terminal_multiple_ebitda > 0:
        terminal[-1] = ebitda[-1] * p.terminal_multiple_ebitda

    # DSRA: equity-funded at COD, released at the earlier of debt maturity or exit
    dsra = np.zeros(horizon)
    first_amort = next((float(debt_service[t]) for t in years if amortising[t]), 0.0)
    if p.dsra_months > 0 and debt > 0 and build < horizon:
        size = first_amort * p.dsra_months / 12
        dsra[build] -= size
        dsra[min(build + loan_life, horizon) - 1] += size

    project_cf = -capex + cfads + terminal
    equity_cf = -capex + draw + cfads - debt_service + terminal + dsra
    equity_cf[-1] -= balloon

    dscr = [float(cfads[t] / debt_service[t]) if debt_service[t] > 0 else None for t in years]
    valid = [d for d in dscr if d is not None]
    valid_amort = [dscr[t] for t in years if amortising[t] and dscr[t] is not None]

    llcr = None
    if debt > 0 and balloon == 0:   # LLCR is meaningless for a loan refinanced/repaid at sale
        loan_t = [build + k for k in range(loan_life) if build + k < horizon]
        pv = sum(cfads[t] / (1 + p.debt_rate) ** (t - build + 1) for t in loan_t)
        llcr = float(pv / debt)
    equity_invested = float(-equity_cf[equity_cf < 0].sum())

    return ProjectResults(
        years=years,
        revenue=revenue.round(2).tolist(),
        ebitda=ebitda.round(2).tolist(),
        cfads=cfads.round(2).tolist(),
        debt_service=debt_service.round(2).tolist(),
        dscr=dscr,
        project_cf=project_cf.round(2).tolist(),
        equity_cf=equity_cf.round(2).tolist(),
        project_irr=irr(project_cf),
        equity_irr=irr(equity_cf),
        equity_npv=npv(p.discount_rate, equity_cf),
        min_dscr=min(valid) if valid else None,
        min_dscr_amort=min(valid_amort) if valid_amort else None,
        avg_dscr=float(np.mean(valid)) if valid else None,
        llcr=llcr,
        moic=moic(equity_cf),
        payback_year=payback_year(equity_cf),
        total_capex=total_capex,
        debt_amount=debt,
        equity_invested=equity_invested,
        ticket=equity_invested * p.equity_share,
        balloon=float(balloon),
        currency=p.currency,
    )


# --------------------------------------------------------- sensitivities
def tornado(base: ProjectInputs, shocks: dict[str, tuple[float, float]],
            metric: str = "equity_irr") -> list[dict]:
    """shocks: {field: (low_multiplier, high_multiplier)} applied to base value.
    Returns rows sorted by swing (largest first) — the risk-ranking used by the gate."""
    rows = []
    base_val = getattr(run_project(base), metric)
    for fld, (lo, hi) in shocks.items():
        out = {}
        for tag, m in (("low", lo), ("high", hi)):
            d = asdict(base)
            v = d[fld]
            d[fld] = [x * m for x in v] if isinstance(v, list) else v * m
            out[tag] = getattr(run_project(ProjectInputs(**d)), metric)
        swing = (abs((out["high"] or 0) - (out["low"] or 0)))
        rows.append({"driver": fld, "low": out["low"], "high": out["high"],
                     "base": base_val, "swing": swing})
    return sorted(rows, key=lambda r: r["swing"], reverse=True)


def grid_2d(base: ProjectInputs, fx: str, xs: list[float], fy: str, ys: list[float],
            metric: str = "equity_irr") -> list[list[float | None]]:
    """2-way data table (absolute values, not multipliers) — like Excel What-If."""
    out = []
    for y in ys:
        row = []
        for x in xs:
            d = asdict(base)
            d[fx], d[fy] = x, y
            row.append(getattr(run_project(ProjectInputs(**d)), metric))
        out.append(row)
    return out


DEFAULT_SHOCKS = {
    "revenue_y1": (0.80, 1.20),
    "capex": (0.85, 1.25),          # capex overruns skew up
    "opex_pct_revenue": (0.85, 1.20),
    "debt_rate": (0.80, 1.30),
    "ramp": (0.80, 1.05),           # lease-up / utilisation (capped effect upside)
    "terminal_multiple_ebitda": (0.75, 1.20),
    "fx_haircut": (0.0, 1.0),       # dropped automatically when base is 0 — add an absolute FX case in scenarios
}


def main(path: str) -> None:
    with open(path) as fh:
        spec = json.load(fh)
    p = ProjectInputs(**spec["project"])
    res = run_project(p)
    shocks = {k: v for k, v in DEFAULT_SHOCKS.items()
              if (k != "fx_haircut" or p.fx_haircut > 0) and (k != "terminal_multiple_ebitda" or p.terminal_multiple_ebitda > 0)}
    out = {"inputs": asdict(p), "results": asdict(res), "tornado": tornado(p, shocks)}
    json.dump(out, sys.stdout, indent=2, default=lambda o: None)


if __name__ == "__main__":
    main(sys.argv[1])


# --------------------------------------------------------- solvers
def solve_for(base: ProjectInputs, field_: str, metric: str, target: float,
              lo: float, hi: float, tol: float = 1e-6, maxiter: int = 200) -> float | None:
    """Bisection: value of `field_` (scalar) at which `metric` == target, e.g. rent needed for a 20% IRR,
    or entry price at which the deal beats the money-market fund. Returns None if not bracketed."""
    def g(x):
        d = asdict(base)
        d[field_] = x
        v = getattr(run_project(ProjectInputs(**d)), metric)
        return None if v is None else v - target
    ga, gb = g(lo), g(hi)
    if ga is None or gb is None or ga * gb > 0:
        import sys as _s
        print(f"solve_for: target {metric}={target} not bracketed by {field_} in [{lo}, {hi}] "
              f"(f(lo)={ga}, f(hi)={gb}) — widen the range", file=_s.stderr)
        return None
    for _ in range(maxiter):
        mid = (lo + hi) / 2
        gm = g(mid)
        if gm is None:
            return None
        if abs(gm) < tol or (hi - lo) / 2 < tol * max(1, abs(mid)):
            return mid
        if ga * gm < 0:
            hi, gb = mid, gm
        else:
            lo, ga = mid, gm
    return (lo + hi) / 2


def scale_capex_solve(base: ProjectInputs, metric: str, target: float, lo: float = 0.3, hi: float = 2.0):
    """Capex multiplier (≈ entry price) at which metric hits target — 'maximum price we should pay'."""
    def g(m):
        d = asdict(base)
        d["capex"] = [c * m for c in d["capex"]]
        v = getattr(run_project(ProjectInputs(**d)), metric)
        return None if v is None else v - target
    a, b = lo, hi
    ga, gb = g(a), g(b)
    if ga is None or gb is None or ga * gb > 0:
        return None
    for _ in range(200):
        mid = (a + b) / 2
        gm = g(mid)
        if gm is None or abs(gm) < 1e-7:
            return mid
        if ga * gm < 0:
            b = mid
        else:
            a, ga = mid, gm
    return (a + b) / 2
