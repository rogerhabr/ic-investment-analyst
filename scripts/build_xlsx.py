"""
build_xlsx.py — Code-first Excel: Python writes a LIVE-formula workbook that mirrors engine.py,
recalculates it in LibreOffice, then reconciles every headline output against the Python engine.
A workbook that does not reconcile is not shipped (exit code 1).

Sheets:
  Inputs   blue = hardcoded input / scenario lever (edit these)
  Model    annual project-finance waterfall, all formulas (black), cross-sheet links green
  Results  headline metrics as formulas + Python reconciliation column
  Risk     Monte Carlo + gate outputs (Python-simulated, pasted as values, labelled as such)
  Sources  assumption register: every input -> source, date, confidence

CLI: python build_xlsx.py deal.json out.xlsx [--risk risk.json] [--gate gate.json]
deal.json = {"project": {...ProjectInputs...}, "sources": {"field": {"source": "...", "date": "...", "confidence": "H|M|L"}}}
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter as COL

from engine import ProjectInputs, run_project
from montecarlo import LombardInputs, simulate_lombard
from dataclasses import asdict

BLUE = Font(color="0000FF")
GREEN = Font(color="008000")
BOLD = Font(bold=True)
HDR = PatternFill("solid", fgColor="DDE4EE")
INPUT_FILL = PatternFill("solid", fgColor="FFF8DC")
THIN = Border(bottom=Side(style="thin", color="999999"))
RECALC = "/mnt/skills/public/xlsx/scripts/recalc.py"
TOL = 1e-4

SCALARS = [  # (field, label, number format) — {cur} is replaced by the deal currency
    ("revenue_y1", "Revenue, first operating year @100% ({cur})", "#,##0"),
    ("revenue_escalation", "Revenue escalation p.a.", "0.0%"),
    ("opex_pct_revenue", "Variable opex, % revenue", "0.0%"),
    ("fixed_opex", "Fixed opex, year 1 ({cur})", "#,##0"),
    ("opex_escalation", "Fixed opex escalation p.a.", "0.0%"),
    ("tax_rate", "Corporate tax rate", "0.0%"),
    ("depreciation_years", "Depreciation life (years)", "0"),
    ("operating_years", "Operating years (= hold period if exit)", "0"),
    ("debt_pct", "Debt, % of capex", "0.0%"),
    ("debt_rate", "Debt all-in rate", "0.00%"),
    ("debt_tenor", "Debt amortisation tenor (years)", "0"),
    ("debt_grace_years", "Grace after COD (years)", "0"),
    ("capitalise_grace_interest", "Capitalise grace interest? (1=yes)", "0"),
    ("debt_draw_year", "Debt drawn in full at year t (-1 = pro-rata with capex)", "0"),
    ("dsra_months", "DSRA, months of debt service (equity-funded)", "0.0"),
    ("terminal_multiple_ebitda", "Exit multiple on final-year EBITDA (x)", "0.0x"),
    ("fx_haircut", "FX haircut on revenue", "0.0%"),
    ("discount_rate", "Equity discount rate (hurdle)", "0.0%"),
    ("equity_share", "Investor share of equity", "0.0%"),
]


def _val(p, fld):
    v = getattr(p, fld)
    if fld == "debt_draw_year":
        return -1 if v is None else v
    if isinstance(v, bool):
        return int(v)
    return v


def build(p: ProjectInputs, sources: dict, out: str, risk: dict | None, gate: dict | None,
          scenarios: dict | None = None) -> None:
    cur = p.currency
    wb = Workbook()
    # ------------------------------------------------------------- Inputs
    ws = wb.active
    ws.title = "Inputs"
    ws["A1"], ws["A1"].font = f"Inputs — {p.name} ({cur})", Font(bold=True, size=14)
    ws["A2"] = f"Blue cells are inputs. Change them; every other sheet recalculates. All money in {cur}."
    name = {}
    r = 4
    for fld, label, fmt in SCALARS:
        ws.cell(r, 1, label.format(cur=cur))
        c = ws.cell(r, 2, _val(p, fld))
        c.font, c.fill, c.number_format = BLUE, INPUT_FILL, fmt
        name[fld] = f"Inputs!$B${r}"
        r += 1
    r += 1
    ws.cell(r, 1, f"Capex schedule by year from financial close ({cur})").font = BOLD
    for i, v in enumerate(p.capex):
        c = ws.cell(r, 2 + i, v)
        c.font, c.fill, c.number_format = BLUE, INPUT_FILL, "#,##0"
    capex_rng = f"Inputs!$B${r}:${COL(1 + len(p.capex))}${r}"
    build_yrs = len(p.capex)
    r += 1
    ws.cell(r, 1, "Utilisation ramp by operating year (last value held)").font = BOLD
    for i, v in enumerate(p.ramp):
        c = ws.cell(r, 2 + i, v)
        c.font, c.fill, c.number_format = BLUE, INPUT_FILL, "0%"
    ramp_rng = f"Inputs!$B${r}:${COL(1 + len(p.ramp))}${r}"
    nramp = len(p.ramp)
    r += 2
    ws.cell(r, 1, "Derived (formulas)").font = BOLD
    derived = [
        ("build", "Build years", f"=COUNT({capex_rng})", "0"),
        ("total_capex", "Total capex", f"=SUM({capex_rng})", "#,##0"),
        ("debt", "Debt amount", None, "#,##0"),
        ("loan_life", "Loan life after COD (grace + tenor)", None, "0"),
        ("pmt", "Annuity payment (sized on balance at end of grace)", None, "#,##0"),
    ]
    for i, (key, label, fx, fmt) in enumerate(derived):
        name[key] = f"Inputs!$B${r + 1 + i}"
    formulas = {
        "debt": f"={name['total_capex']}*{name['debt_pct']}",
        "loan_life": f"={name['debt_grace_years']}+{name['debt_tenor']}",
        "pmt": (f"=IF({name['debt']}>0,-PMT({name['debt_rate']},{name['debt_tenor']},{name['debt']}"
                f"*(1+{name['debt_rate']})^({name['debt_grace_years']}*{name['capitalise_grace_interest']})),0)"),
    }
    for i, (key, label, fx, fmt) in enumerate(derived):
        ws.cell(r + 1 + i, 1, label)
        ws.cell(r + 1 + i, 2, fx or formulas[key]).number_format = fmt
    ws.column_dimensions["A"].width = 56
    ws.column_dimensions["B"].width = 16

    # ------------------------------------------------------------- Model
    m = wb.create_sheet("Model")
    horizon = build_yrs + p.operating_years
    rows = ["Year (t)", "Operating flag", "Operating year index (k)", "Loan-life flag", "Amortising flag",
            "Capex", "Utilisation", "Revenue", "Opex", "EBITDA", "Depreciation",
            "Debt drawdown", "Debt opening balance", "Interest", "Debt service", "Debt closing balance",
            "Taxable income", "Tax", "CFADS", "Terminal value", "DSRA flow", "Balloon repayment",
            "Project cash flow", "Equity cash flow", "DSCR", "DSCR (amortising years)"]
    R = {lbl: i + 2 for i, lbl in enumerate(rows)}
    m["A1"], m["A1"].font = f"Annual project-finance waterfall ({cur}, all formulas)", BOLD
    for lbl, row in R.items():
        m.cell(row, 1, lbl).font = BOLD if lbl in ("EBITDA", "CFADS", "Project cash flow", "Equity cash flow") else Font()
    m.column_dimensions["A"].width = 28
    N = name
    for t in range(horizon):
        c = COL(2 + t)
        prev = COL(1 + t)
        f = lambda lbl: f"{c}{R[lbl]}"
        k, op = f("Operating year index (k)"), f("Operating flag")
        cells = {
            "Year (t)": t,
            "Operating flag": f"=IF({f('Year (t)')}>={N['build']},1,0)",
            "Operating year index (k)": f"=IF({op}=1,{f('Year (t)')}-{N['build']},-1)",
            "Loan-life flag": f"=IF(AND({op}=1,{k}<{N['loan_life']}),1,0)",
            "Amortising flag": f"=IF(AND({op}=1,{k}>={N['debt_grace_years']},{k}<{N['loan_life']}),1,0)",
            "Capex": f"=IF({f('Year (t)')}<{N['build']},INDEX({capex_rng},1,{f('Year (t)')}+1),0)",
            "Utilisation": f"=IF({op}=1,INDEX({ramp_rng},1,MIN({k}+1,{nramp})),0)",
            "Revenue": (f"=IF({op}=1,{N['revenue_y1']}*{f('Utilisation')}*(1+{N['revenue_escalation']})^{k}"
                        f"*(1-{N['fx_haircut']}),0)"),
            "Opex": (f"=IF({op}=1,{f('Revenue')}*{N['opex_pct_revenue']}+{N['fixed_opex']}"
                     f"*(1+{N['opex_escalation']})^{k},0)"),
            "EBITDA": f"={f('Revenue')}-{f('Opex')}",
            "Depreciation": (f"=IF(AND({op}=1,{N['depreciation_years']}>0),IF({k}<{N['depreciation_years']},"
                             f"{N['total_capex']}/{N['depreciation_years']},0),0)"),
            "Debt drawdown": (f"=IF({N['debt_draw_year']}<0,{f('Capex')}*{N['debt_pct']},"
                              f"IF(MIN({N['debt_draw_year']},{horizon - 1})={f('Year (t)')},{N['debt']},0))"),
            "Debt opening balance": f"={N['debt']}" if t == 0 else f"={prev}{R['Debt closing balance']}",
            "Interest": f"=IF({f('Loan-life flag')}=1,{f('Debt opening balance')}*{N['debt_rate']},0)",
            "Debt service": (f"=IF({f('Loan-life flag')}=1,IF({f('Amortising flag')}=1,{N['pmt']},"
                             f"IF({N['capitalise_grace_interest']}=1,0,{f('Interest')})),0)"),
            "Debt closing balance": f"={f('Debt opening balance')}+{f('Interest')}-{f('Debt service')}",
            "Taxable income": f"={f('EBITDA')}-{f('Depreciation')}-{f('Interest')}",
            "Tax": f"=MAX({f('Taxable income')},0)*{N['tax_rate']}",
            "CFADS": f"={f('EBITDA')}-{f('Tax')}",
            "Terminal value": f"=IF({f('Year (t)')}={horizon - 1},{f('EBITDA')}*{N['terminal_multiple_ebitda']},0)",
            "DSRA flow": (f"=IF(AND({N['dsra_months']}>0,{N['debt']}>0),"
                          f"IF({f('Year (t)')}={N['build']},-{N['pmt']}*{N['dsra_months']}/12,0)"
                          f"+IF({f('Year (t)')}=MIN({N['build']}+{N['loan_life']},{horizon})-1,{N['pmt']}*{N['dsra_months']}/12,0),0)"),
            "Balloon repayment": (f"=IF(AND({f('Year (t)')}={horizon - 1},{N['build']}+{N['loan_life']}>{horizon}),"
                                  f"MAX({f('Debt closing balance')},0),0)"),
            "Project cash flow": f"=-{f('Capex')}+{f('CFADS')}+{f('Terminal value')}",
            "Equity cash flow": (f"=-{f('Capex')}+{f('Debt drawdown')}+{f('CFADS')}-{f('Debt service')}"
                                 f"+{f('Terminal value')}+{f('DSRA flow')}-{f('Balloon repayment')}"),
            "DSCR": f"=IF({f('Debt service')}>0,{f('CFADS')}/{f('Debt service')},\"\")",
            "DSCR (amortising years)": (f"=IF(AND({f('Amortising flag')}=1,{f('Debt service')}>0),"
                                        f"{f('CFADS')}/{f('Debt service')},\"\")"),
        }
        for lbl, v in cells.items():
            m[f(lbl)] = v
            cell = m[f(lbl)]
            cell.number_format = ("0.00x" if lbl.startswith("DSCR") else "0%" if lbl == "Utilisation"
                                  else "0" if "flag" in lbl or lbl.startswith(("Year", "Operating year")) else "#,##0;(#,##0)")
        m[f("Year (t)")].font, m[f("Year (t)")].fill = BOLD, HDR
        m.column_dimensions[c].width = 13
    last = COL(1 + horizon)
    rowrng = lambda lbl: f"Model!$B${R[lbl]}:${last}${R[lbl]}"
    m.freeze_panes = "B3"

    # ------------------------------------------------------------- Results
    rs = wb.create_sheet("Results")
    rs["A1"], rs["A1"].font = f"Results — {p.name} ({cur})", Font(bold=True, size=14)
    for j, h in enumerate(["Metric", "Excel (live)", "Python engine", "|Δ|", "Reconciles?"]):
        cell = rs.cell(3, 1 + j, h)
        cell.font, cell.fill = BOLD, HDR
    eng = run_project(p)
    eq, pj, cf_ = rowrng("Equity cash flow"), rowrng("Project cash flow"), rowrng("CFADS")
    first_eq = f"Model!$B${R['Equity cash flow']}"
    rest_eq = f"Model!$C${R['Equity cash flow']}:${last}${R['Equity cash flow']}"
    balloon_total = f"SUM({rowrng('Balloon repayment')})"
    loan_pv = (f"SUMPRODUCT({rowrng('Loan-life flag')}*{cf_}/(1+{N['debt_rate']})"
               f"^(Model!$B${R['Year (t)']}:${last}${R['Year (t)']}-{N['build']}+1))")
    metrics = [
        ("Total capex", f"={N['total_capex']}", eng.total_capex, "#,##0"),
        ("Debt amount", f"={N['debt']}", eng.debt_amount, "#,##0"),
        ("Project IRR", f"=IRR({pj},0.1)", eng.project_irr, "0.00%"),
        ("Equity IRR", f"=IRR({eq},0.1)", eng.equity_irr, "0.00%"),
        ("Equity NPV @ hurdle", f"={first_eq}+NPV({N['discount_rate']},{rest_eq})", eng.equity_npv, "#,##0;(#,##0)"),
        ("Min DSCR (all debt-service years)", f"=IF(COUNT({rowrng('DSCR')})>0,MIN({rowrng('DSCR')}),\"\")", eng.min_dscr, "0.00x"),
        ("Min DSCR (amortising years)", f"=IF(COUNT({rowrng('DSCR (amortising years)')})>0,MIN({rowrng('DSCR (amortising years)')}),\"\")",
         eng.min_dscr_amort, "0.00x"),
        ("Avg DSCR", f"=IF(COUNT({rowrng('DSCR')})>0,AVERAGE({rowrng('DSCR')}),\"\")", eng.avg_dscr, "0.00x"),
        ("LLCR (blank if balloon)", f"=IF(AND({N['debt']}>0,{balloon_total}=0),{loan_pv}/{N['debt']},\"\")", eng.llcr, "0.00x"),
        ("MOIC (equity)", f"=SUMIF({eq},\">0\")/-SUMIF({eq},\"<0\")", eng.moic, "0.00x"),
        ("Balloon at exit", f"={balloon_total}", eng.balloon, "#,##0"),
        ("Equity invested (100%)", f"=-SUMIF({eq},\"<0\")", eng.equity_invested, "#,##0"),
        ("Investor ticket", f"=-SUMIF({eq},\"<0\")*{N['equity_share']}", eng.ticket, "#,##0"),
    ]
    _write_results(rs, metrics)

    # ------------------------------------------------------------- Scenarios
    if scenarios:
        sc = wb.create_sheet("Scenarios")
        sc["A1"], sc["A1"].font = "Scenarios — Python engine (static values; rerun build_xlsx.py to refresh)", BOLD
        hdr = ["Scenario", "Overrides", "Equity IRR", "Min DSCR (amort)", "Equity NPV", "MOIC", "Ticket"]
        for j, h in enumerate(hdr):
            sc.cell(3, 1 + j, h).font = BOLD
        base_d = asdict(p)
        for i, (sname, ov) in enumerate(scenarios.items()):
            d = dict(base_d); d.update(ov)
            res = run_project(ProjectInputs(**d))
            vals = [sname, json.dumps(ov), res.equity_irr, res.min_dscr_amort, res.equity_npv, res.moic, res.ticket]
            fmts = [None, None, "0.00%", "0.00x", "#,##0;(#,##0)", "0.00x", "#,##0"]
            for j, (v, fm) in enumerate(zip(vals, fmts)):
                cell = sc.cell(4 + i, 1 + j, v)
                if fm:
                    cell.number_format = fm
        sc.column_dimensions["A"].width = 22
        sc.column_dimensions["B"].width = 60

    _write_risk(wb, risk, gate)

    # ------------------------------------------------------------- Sources
    so = wb.create_sheet("Sources")
    for j, h in enumerate(["Input", "Value", "Source", "As-of date", "Confidence"]):
        so.cell(1, 1 + j, h).font = BOLD
    for i, (fld, _, _) in enumerate(SCALARS + [("capex", "", ""), ("ramp", "", "")]):
        s = sources.get(fld, {})
        v = _val(p, fld)
        so.cell(2 + i, 1, fld)
        so.cell(2 + i, 2, json.dumps(v) if isinstance(v, list) else v)
        so.cell(2 + i, 3, s.get("source", "UNSOURCED — assumption, flag in memo"))
        so.cell(2 + i, 4, s.get("date", ""))
        so.cell(2 + i, 5, s.get("confidence", "L"))
    so.column_dimensions["A"].width = 24
    so.column_dimensions["C"].width = 60

    wb.save(out)


def _write_results(rs, metrics) -> None:
    """metrics: [(label, excel_formula, python_value, number_format)]. Python None -> blank;
    a blank Python value against a numeric Excel value is a mismatch ('NO'), never silently 'n/a'."""
    for i, (lbl, fx, py, fmt) in enumerate(metrics):
        row = 4 + i
        rs.cell(row, 1, lbl)
        rs.cell(row, 2, fx).number_format = fmt
        rs.cell(row, 3, py).number_format = fmt
        rs.cell(row, 4, f"=IF(AND(ISNUMBER(B{row}),ISNUMBER(C{row})),ABS(B{row}-C{row}),\"\")").number_format = "0.000000"
        rel = f"MAX(1,ABS(C{row}))" if fmt.startswith("#") else "1"
        rs.cell(row, 5, f"=IF(ISNUMBER(B{row})<>ISNUMBER(C{row}),\"NO\",IF(D{row}=\"\",\"n/a\","
                         f"IF(D{row}/{rel}<={TOL},\"YES\",\"NO\")))")
    rs.column_dimensions["A"].width = 34
    for col in "BCDE":
        rs.column_dimensions[col].width = 18


def _write_risk(wb, risk: dict | None, gate: dict | None) -> None:
    rk = wb.create_sheet("Risk")
    rk["A1"], rk["A1"].font = "Risk — Python-simulated (static values; rerun montecarlo.py to refresh)", BOLD
    row = 3
    if gate:
        rk.cell(row, 1, "GATE VERDICT").font = BOLD
        rk.cell(row, 2, gate["verdict"]).font = Font(bold=True, color={"PROCEED": "008000", "CONDITIONAL": "B8860B",
                                                                      "DECLINE": "C00000"}[gate["verdict"]])
        rk.cell(row + 1, 1, "Score (0–100)"); rk.cell(row + 1, 2, gate["score"])
        row += 3
        for j, h in enumerate(["Metric", "Rule", "Value", "Status"]):
            rk.cell(row, 1 + j, h).font = BOLD
        for ch in gate["checks"]:
            row += 1
            rk.cell(row, 1, ch["metric"]); rk.cell(row, 2, ch["rule"])
            rk.cell(row, 3, ch["value"]); rk.cell(row, 4, ch["status"])
        row += 2
    if risk:
        for block, vals in risk.items():
            if not isinstance(vals, dict):
                continue
            rk.cell(row, 1, block).font = BOLD
            row += 1
            for kk, v in vals.items():
                if isinstance(v, (int, float)) or v is None:
                    rk.cell(row, 1, kk); rk.cell(row, 2, v)
                    row += 1
            row += 1
    rk.column_dimensions["A"].width = 34
    rk.column_dimensions["B"].width = 18


LOMBARD_INPUTS = [
    ("portfolio_value", "Portfolio market value ({cur})", "#,##0"),
    ("loan", "Loan ({cur})", "#,##0"),
    ("advance_rate", "Weighted advance rate (lending value / market value)", "0.0%"),
    ("ltv_call", "Margin-call LTV (on lending value)", "0.0%"),
    ("ltv_liquidate", "Forced-sale LTV (on lending value)", "0.0%"),
    ("loan_rate", "Loan all-in rate", "0.00%"),
    ("mu", "Collateral expected total return", "0.0%"),
    ("sigma", "Collateral volatility", "0.0%"),
    ("proceeds_yield", "Yield on use of proceeds (blank = none)", "0.00%"),
    ("proceeds_credit_loss", "Expected annual loss on proceeds asset", "0.00%"),
    ("loan_fx_drift", "Loan-currency forward cost (expected appreciation vs collateral ccy, /yr; 0 = same ccy)", "0.00%"),
    ("loan_fx_sigma", "Loan-currency volatility vs collateral ccy (0 = same ccy)", "0.0%"),
]


def build_lombard(lp: LombardInputs, stresses: list[dict], out: str, risk: dict | None, gate: dict | None,
                  currency: str = "USD", name_: str = "Lombard") -> None:
    """Formulas for the deterministic Lombard mechanics (LTVs, distances, carry, stress cures);
    Monte Carlo results go on Risk as labelled static values."""
    cur = currency
    wb = Workbook()
    ws = wb.active
    ws.title = "Inputs"
    ws["A1"], ws["A1"].font = f"Lombard inputs — {name_} ({cur})", Font(bold=True, size=14)
    N = {}
    for i, (fld, label, fmt) in enumerate(LOMBARD_INPUTS):
        r = 4 + i
        ws.cell(r, 1, label.format(cur=cur))
        c = ws.cell(r, 2, getattr(lp, fld))
        c.font, c.fill, c.number_format = BLUE, INPUT_FILL, fmt
        N[fld] = f"Inputs!$B${r}"
    ws.column_dimensions["A"].width = 52
    ws.column_dimensions["B"].width = 16

    calc = wb.create_sheet("Mechanics")
    calc["A1"], calc["A1"].font = "Lombard mechanics (all formulas)", BOLD
    defs = [
        ("lending_value", "Lending value", f"={N['portfolio_value']}*{N['advance_rate']}", "#,##0"),
        ("ltv_mkt", "LTV on market value", f"={N['loan']}/{N['portfolio_value']}", "0.0%"),
        ("ltv_lend", "LTV on lending value (lender's test)", f"={N['loan']}/({N['portfolio_value']}*{N['advance_rate']})", "0.0%"),
        ("dd_call", "Portfolio fall to margin call", f"=1-({N['loan']}/({N['ltv_call']}*{N['advance_rate']}))/{N['portfolio_value']}", "0.0%"),
        ("dd_liq", "Portfolio fall to forced sale", f"=1-({N['loan']}/({N['ltv_liquidate']}*{N['advance_rate']}))/{N['portfolio_value']}", "0.0%"),
        ("fx_call", "Loan-currency rise to margin call (collateral flat)", f"={N['ltv_call']}*{N['portfolio_value']}*{N['advance_rate']}/{N['loan']}-1", "0.0%"),
        ("carry", "Carry spread (proceeds yield - loss - loan rate - loan-ccy forward cost)",
         f"=IF(ISNUMBER({N['proceeds_yield']}),{N['proceeds_yield']}-{N['proceeds_credit_loss']},{N['mu']})-{N['loan_rate']}-{N['loan_fx_drift']}", "0.00%"),
        ("carry_ccy", f"Annual net carry ({cur})", None, "#,##0;(#,##0)"),
        ("max_loan_call", f"Max loan with zero headroom to call ({cur})", f"={N['ltv_call']}*{N['portfolio_value']}*{N['advance_rate']}", "#,##0"),
    ]
    C = {}
    for i, (key, label, fx, fmt) in enumerate(defs):
        C[key] = f"Mechanics!$B${3 + i}"
    for i, (key, label, fx, fmt) in enumerate(defs):
        calc.cell(3 + i, 1, label)
        calc.cell(3 + i, 2, fx or f"={C['carry']}*{N['loan']}").number_format = fmt
    calc.column_dimensions["A"].width = 70
    calc.column_dimensions["B"].width = 16

    st = wb.create_sheet("Stress")
    st["A1"], st["A1"].font = "Deterministic stresses — shocks are inputs (blue); outcome is formula", BOLD
    hdr = ["Scenario", "Portfolio shock", "Advance-rate cut", "Stressed value", "Stressed LTV (lending)", "Status",
           f"Cash to cure to call LTV ({cur})", "Loan-currency move"]
    for j, h in enumerate(hdr):
        st.cell(3, 1 + j, h).font = BOLD
    for i, s_ in enumerate(stresses):
        r = 4 + i
        st.cell(r, 1, s_["name"])
        for col, key in ((2, "shock"), (3, "advance_cut"), (8, "fx_shock")):
            c = st.cell(r, col, s_.get(key, 0.0)); c.font, c.fill, c.number_format = BLUE, INPUT_FILL, "0.0%"
        st.cell(r, 4, f"={N['portfolio_value']}*(1+B{r})").number_format = "#,##0"
        st.cell(r, 5, f"={N['loan']}*(1+H{r})/(D{r}*MAX({N['advance_rate']}-C{r},0.01))").number_format = "0.0%"
        st.cell(r, 6, f"=IF(E{r}>={N['ltv_liquidate']},\"FORCED SALE\",IF(E{r}>={N['ltv_call']},\"MARGIN CALL\",\"OK\"))")
        st.cell(r, 7, f"=MAX(0,{N['loan']}*(1+H{r})-{N['ltv_call']}*D{r}*MAX({N['advance_rate']}-C{r},0.01))").number_format = "#,##0"
    st.column_dimensions["A"].width = 30
    for col in "BCDEFGH":
        st.column_dimensions[col].width = 18

    rs = wb.create_sheet("Results")
    rs["A1"], rs["A1"].font = f"Results — {name_}", Font(bold=True, size=14)
    for j, h in enumerate(["Metric", "Excel (live)", "Python engine", "|Δ|", "Reconciles?"]):
        rs.cell(3, 1 + j, h).font = BOLD
    py = simulate_lombard(LombardInputs(**{**asdict(lp), "n_paths": 2}))  # closed-form fields only
    _write_results(rs, [
        ("LTV on market value", f"={C['ltv_mkt']}", py["ltv0"], "0.0000%"),
        ("LTV on lending value", f"={C['ltv_lend']}", py["ltv0_lending"], "0.0000%"),
        ("Fall to margin call", f"={C['dd_call']}", py["drawdown_to_call"], "0.0000%"),
        ("Fall to forced sale", f"={C['dd_liq']}", py["drawdown_to_liquidation"], "0.0000%"),
        ("Carry spread", f"={C['carry']}", py["carry_spread"], "0.0000%"),
        ("Loan-currency rise to call", f"={C['fx_call']}", py["fx_rise_to_call"], "0.0000%"),
    ])
    _write_risk(wb, risk, gate)
    wb.save(out)


def recalc_and_reconcile(out: str) -> dict:
    res = subprocess.run([sys.executable, RECALC, out, "90"], capture_output=True, text=True)
    try:
        rc = json.loads(res.stdout[res.stdout.index("{"):])
    except Exception:
        rc = {"error": res.stdout + res.stderr}
    wb = load_workbook(out, data_only=True)
    rs = wb["Results"]
    rows = []
    ok = "error" not in rc and rc.get("total_errors", 0) == 0
    r = 4
    while rs.cell(r, 1).value:
        rows.append({"metric": rs.cell(r, 1).value, "excel": rs.cell(r, 2).value,
                     "python": rs.cell(r, 3).value, "reconciles": rs.cell(r, 5).value})
        if rs.cell(r, 5).value == "NO":
            ok = False
        r += 1
    return {"recalc": rc, "reconciliation": rows, "ok": ok}


DEFAULT_STRESSES = [
    {"name": "Equities -20%, bonds flat (60/40 ~ -12%)", "shock": -0.12},
    {"name": "2022: equities -25%, bonds -17% (60/40 ~ -22%)", "shock": -0.218},
    {"name": "2007-09 peak-to-trough (60/40 ~ -35%)", "shock": -0.35},
    {"name": "Lender cuts advance rate 10 pts, flat market", "shock": 0.0, "advance_cut": 0.10},
    {"name": "-12% market + 10 pt advance cut", "shock": -0.12, "advance_cut": 0.10},
]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("deal")
    ap.add_argument("out")
    ap.add_argument("--risk")
    ap.add_argument("--gate")
    a = ap.parse_args()
    spec = json.load(open(a.deal))
    risk = json.load(open(a.risk)) if a.risk else None
    gate = json.load(open(a.gate)) if a.gate else None
    if "project" in spec:
        p = ProjectInputs(**spec["project"])
        build(p, spec.get("sources", {}), a.out, risk, gate, spec.get("scenarios"))
    elif "lombard" in spec:
        build_lombard(LombardInputs(**spec["lombard"]), spec.get("stresses", DEFAULT_STRESSES), a.out, risk, gate,
                      spec.get("currency", "USD"), spec.get("name", "Lombard"))
    else:
        sys.exit("deal.json needs a 'project' or 'lombard' block")
    rep = recalc_and_reconcile(a.out)
    json.dump(rep, sys.stdout, indent=2, default=str)
    sys.exit(0 if rep["ok"] else 1)
