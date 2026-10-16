"""Run a suite against a workbook: for each flex case set inputs -> recalc -> read -> compare.

Results are judged against pfreg.truth, never against the workbook's own checks."""
from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import fields
from pathlib import Path

import yaml

from . import xlio
from .truth import PF, dcf, project_finance

SUITES = yaml.safe_load((Path(__file__).parent / "suites.yaml").read_text())
RATE = {"gearing", "equity_irr", "project_irr"}
RATIO = {"min_dscr", "llcr"}


def case_params(suite: str, flex: str) -> dict:
    s = SUITES[suite]
    return {**s["base"], **s["flex"][flex]}


def expected(suite: str, flex: str) -> dict:
    s, prm = SUITES[suite], case_params(suite, flex)
    if s["kind"] == "dcf":
        return dcf(prm["fcf"], prm["wacc"], prm["g"], prm.get("midyear", True)) | {"_params": prm}
    pf = PF(**{k: v for k, v in prm.items() if k in {f.name for f in fields(PF)}})
    return project_finance(pf) | {"_params": prm, "_pf": pf}


def input_values(suite: str, flex: str) -> dict:
    s, prm = SUITES[suite], case_params(suite, flex)
    if s["kind"] == "dcf":
        return {k: prm[k] for k in s["inputs"]}
    pf = expected(suite, flex)["_pf"]
    return {k: getattr(pf, k) for k in s["inputs"]}


def _binding(v):
    t = str(v).lower()
    if "dscr" in t or "sculpt" in t:
        return "DSCR"
    if any(w in t for w in ("ltv", "cap", "gearing", "leverage")):
        return "gearing"
    return str(v)


def _cmp(k, got, exp, tol):
    if k == "binding":
        return _binding(got) == exp, _binding(got)
    if not isinstance(got, (int, float)):
        return False, got
    g = float(got)
    if k in RATE and abs(exp) < 1 and abs(g) > 1:      # model shows 17.52 for 17.52%
        g /= 100
    t = tol["rate"] if k in RATE else tol["ratio"] if k in RATIO else tol["money"]
    return abs(g - exp) <= t, g


def diagnose(suite, exp, got):
    """Known failure signatures -> human-readable root cause."""
    d, p = [], exp.get("_pf")
    num = lambda k: got.get(k) if isinstance(got.get(k), (int, float)) else None
    if SUITES[suite]["kind"] == "dcf":
        prm = exp["_params"]
        eoy = dcf(prm["fcf"], prm["wacc"], prm["g"], midyear=False)["ev"]
        if num("ev") is not None and abs(num("ev") - eoy) < 1e-3:
            d.append("EV equals END-OF-YEAR result: mid-year convention not applied")
        return d
    D, dr1, dr2 = num("debt"), num("draw_t1"), num("draw_t2")
    if None not in (D, dr1, dr2) and abs(dr1 + dr2 - D) > 1e-3:
        d.append(f"Draws {dr1 + dr2:.4f} != final debt {D:.4f}: {dr1 + dr2 - D:+.4f} of debt drawn "
                 "that the schedule never repays (sources/uses broken)")
    if p is None:
        return d
    lam = num("gearing")
    if exp["binding"] == "DSCR" and lam is not None and abs(lam - p.cap) < 1e-6:
        d.append("lambda equals the cap while DSCR binds: lambda hard-wired to LTV instead of D/TU")
    tu = num("total_uses")
    if exp["binding"] == "DSCR" and None not in (tu, D) and abs(tu - D / p.cap) < 1e-3:
        d.append("Total uses = D/cap: only valid when the cap binds; must be the SUM of uses")
    idc = num("idc")
    if idc is not None and p.drawdown == "prorata":
        u1 = p.capex[0] + p.fee * exp["debt"]
        if exp["binding"] == "DSCR" and abs(idc - p.rate * p.cap * u1) < 1e-3:
            d.append("IDC = r x cap x uses_t1: uses the cap instead of lambda")
        if abs(idc - p.rate * exp["debt"]) < 1e-2:
            d.append("IDC charged on the full facility instead of the t=1 draw")
    if D is not None and D > exp["sculpt_capacity"] + 1e-3:
        d.append("Debt exceeds DSCR sculpting capacity: DSCR constraint ignored")
    md = num("min_dscr")
    if md is not None and md < p.dscr - 1e-4:
        d.append(f"Min DSCR {md:.4f} below target {p.dscr}: covenant breach")
    return d


def run(model: str, suite: str, backend="auto", pins=None, keep: str | None = None) -> dict:
    s, tol = SUITES[suite], SUITES["tol"]
    work = Path(keep or tempfile.mkdtemp(prefix="pfreg_"))
    work.mkdir(parents=True, exist_ok=True)
    locs = xlio.locate(model, s["metrics"], xlio.LABELS, pins)
    live = xlio.formulas_present(model, locs)
    report = dict(model=str(model), suite=suite, cases={}, located=locs,
                  hardcoded=[k for k, v in live.items() if not v])
    for flex in s["flex"]:
        exp = expected(suite, flex)
        f = work / f"{Path(model).stem}__{flex}.xlsx"
        xlio.write_inputs(model, f, input_values(suite, flex), pins)
        used = xlio.recalc(f, backend)
        got = xlio.read(f, locs)
        rows = []
        for k in s["metrics"]:
            e = exp[k]
            if locs.get(k) is None:
                rows.append(dict(metric=k, expected=e, got=None, ok=False, note="label not found"))
                continue
            ok, g = _cmp(k, got[k], e, tol)
            rows.append(dict(metric=k, expected=e, got=g, ok=ok))
        report["cases"][flex] = dict(backend=used, inputs=input_values(suite, flex), rows=rows,
                                     passed=all(r["ok"] for r in rows),
                                     diagnosis=diagnose(suite, exp, got))
    report["passed"] = all(c["passed"] for c in report["cases"].values())
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    return report


def render(rep: dict) -> str:
    fmt = lambda v: f"{v:.6f}" if isinstance(v, float) else str(v)
    out = [f"MODEL {rep['model']}  |  SUITE {rep['suite']}  |  "
           f"{'PASS' if rep['passed'] else 'FAIL'}"]
    if rep["hardcoded"]:
        out.append(f"  ! hardcoded (no formula) output cells: {', '.join(rep['hardcoded'])}")
    for name, c in rep["cases"].items():
        fails = [r for r in c["rows"] if not r["ok"]]
        out.append(f"\n[{'PASS' if c['passed'] else 'FAIL'}] {name}  inputs={c['inputs']}  "
                   f"({len(c['rows']) - len(fails)}/{len(c['rows'])} metrics, recalc={c['backend']})")
        for r in fails:
            out.append(f"    x {r['metric']:<14} expected {fmt(r['expected']):>14}   got {fmt(r['got']):>14}"
                       + (f"   ({r['note']})" if r.get("note") else ""))
        for dmsg in c["diagnosis"]:
            out.append(f"    > {dmsg}")
    return "\n".join(out)


def save_json(rep, path):
    clean = json.loads(json.dumps(rep, default=str))
    Path(path).write_text(json.dumps(clean, indent=2))
