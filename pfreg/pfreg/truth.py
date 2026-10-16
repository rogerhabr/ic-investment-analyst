"""Ground-truth engine. Independent of any spreadsheet: every number a model must reproduce.

Conventions (shared by all PF cases):
  annual periods, flows at period END, t=1..n_con construction, then `life` operating years.
  Debt fully drawn by COD (end of last construction period), repaid over `tenor` years,
  sculpted to target DSCR; if a cap binds the sculpted profile is scaled down.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq


def irr(cfs) -> float:
    f = lambda x: sum(c / (1 + x) ** t for t, c in enumerate(cfs))
    return brentq(f, -0.99, 10.0, xtol=1e-14)


# ---------------------------------------------------------------- DCF
def dcf(fcf, wacc, g, midyear=True) -> dict:
    n = len(fcf)
    per = [t - 0.5 if midyear else t for t in range(1, n + 1)]
    dfs = [(1 + wacc) ** -p for p in per]
    pv_fcf = sum(c * d for c, d in zip(fcf, dfs))
    tv = fcf[-1] * (1 + g) / (wacc - g)
    pv_tv = tv * (1 + wacc) ** -per[-1]          # Gordon TV discounted at last flow's period
    ev = pv_fcf + pv_tv
    return dict(pv_fcf=pv_fcf, tv=tv, pv_tv=pv_tv, ev=ev, tv_share=pv_tv / ev,
                df_1=dfs[0], df_n=dfs[-1])


# ---------------------------------------------------------------- Project finance
@dataclass
class PF:
    capex: tuple = (40.0, 60.0)          # by construction period, t=1..n_con
    cfads1: float = 14.0
    growth: float = 0.02
    life: int = 15
    rate: float = 0.07
    tenor: int = 12
    dscr: float = 1.35
    cap: float = 0.70
    cap_basis: str = "capex"             # "capex" (v1) | "uses" (v2: capex+fee+IDC+DSRA)
    drawdown: str = "single"             # "single": one draw at COD | "prorata": each period at D/TU
    fee: float = 0.0                     # upfront fee, % of debt, paid t=1
    dsra: float = 0.0                    # DSRA = dsra x first-year DS, funded at COD, released at final repayment
    extra: dict = field(default_factory=dict)


def project_finance(p: PF) -> dict:
    if len(p.capex) != 2 and p.drawdown == "prorata":
        raise ValueError("prorata engine implemented for 2 construction periods")
    n_con = len(p.capex)
    cf = np.array([p.cfads1 * (1 + p.growth) ** k for k in range(p.life)])
    ds_t = cf[: p.tenor] / p.dscr
    disc = (1 + p.rate) ** -np.arange(1, p.tenor + 1)
    sc = float((ds_t * disc).sum())
    capex_tot = float(sum(p.capex))

    def state(D):
        """Given debt D, return all dependent quantities (uses, lambda, IDC...)."""
        scale = min(1.0, D / sc)
        ds1 = ds_t[0] * scale
        fee = p.fee * D
        dsra = p.dsra * ds1
        if p.drawdown == "single":
            idc, u1, u2 = 0.0, p.capex[0] + fee, p.capex[1] + dsra
            tu = u1 + u2
            lam = None
        else:
            # TU = u1 + C2 + DSRA + r*(D/TU)*u1  ->  TU^2 - A*TU - r*D*u1 = 0
            u1 = p.capex[0] + fee
            A = u1 + p.capex[1] + dsra
            tu = (A + math.sqrt(A * A + 4 * p.rate * D * u1)) / 2
            lam = D / tu
            idc = p.rate * lam * u1
            u2 = p.capex[1] + idc + dsra
        return dict(scale=scale, fee=fee, dsra=dsra, idc=idc, u1=u1, u2=u2, tu=tu, lam=lam)

    # Debt = min(DSCR capacity, cap x basis(D)); basis may depend on D -> monotone fixed point.
    def cap_amount(D):
        return p.cap * (capex_tot if p.cap_basis == "capex" else state(D)["tu"])

    if cap_amount(sc) >= sc:
        D = sc
    else:  # gearing binds: solve D = cap * basis(D)
        D = brentq(lambda d: d - cap_amount(d), 1e-9, sc, xtol=1e-13)
    s = state(D)
    binding = "DSCR" if abs(D - sc) < 1e-9 else "gearing"

    ds = ds_t * s["scale"]
    bal, sched = D, []
    for k in range(p.tenor):
        i = bal * p.rate
        pr = ds[k] - i
        sched.append(dict(open=bal, interest=i, principal=pr, ds=ds[k], dscr=cf[k] / ds[k]))
        bal -= pr

    T = n_con + p.life
    eq = np.zeros(T + 1)                       # index = t
    if p.drawdown == "single":
        draw1, draw2 = 0.0, D
        eq[1] = -s["u1"]
        eq[2] = -(s["u2"] - D)                 # can be positive (debt > t2 uses)
    else:
        draw1, draw2 = s["lam"] * s["u1"], s["lam"] * s["u2"]
        eq[1] = -(s["u1"] - draw1)
        eq[2] = -(s["u2"] - draw2)
    for k in range(p.life):
        eq[n_con + 1 + k] = cf[k] - (ds[k] if k < p.tenor else 0.0)
    eq[n_con + p.tenor] += s["dsra"]           # DSRA released with final repayment

    proj = np.zeros(T + 1)
    for t, c in enumerate(p.capex, start=1):
        proj[t] = -c
    proj[n_con + 1:] = cf

    return dict(
        sculpt_capacity=sc, debt=D, binding=binding,
        total_uses=s["tu"] if p.drawdown == "prorata" else capex_tot + s["fee"] + s["dsra"],
        fee=s["fee"], idc=s["idc"], dsra=s["dsra"],
        gearing=D / (s["tu"] if p.drawdown == "prorata" else capex_tot),
        draw_t1=draw1, draw_t2=draw2, equity_t1=-eq[1], equity_t2=-eq[2],
        ds_y1=sched[0]["ds"], int_y1=sched[0]["interest"], prin_y1=sched[0]["principal"],
        min_dscr=min(r["dscr"] for r in sched),
        llcr=float((cf[: p.tenor] * disc).sum() / D),
        end_balance=bal, project_irr=irr(np.r_[0, proj[1:]]),
        equity_irr=irr(np.r_[0, eq[1:]]),
        schedule=sched, equity_cf=eq[1:].tolist(),
    )
