# Sub-agent roles

Why separate agents: the same model reading the same evidence makes correlated mistakes. Splitting
the *inputs* and *mandates* (not just the persona) is what decorrelates them, the same way
diversification only works across assets that don't move together. Each brief below lists exactly
what that agent receives. Give it nothing else.

Every agent writes a JSON file `agent_<role>.json` plus ≤300 words of reasoning. Every number it
cites must be either from a code run it performed or from the assumption register.

---

## 1. Market & Comps
**Receives:** deal.json (asset class, country, size, timing), data_sources.md.
**Does NOT receive:** the sponsor's returns or the Quant's results. This avoids anchoring.
**Mandate:** build an independent view of market pricing and trend:
- ≥3 comps (transaction or listed) with $/MW, cap rate, tariff, multiple or yield as relevant, each sourced and dated
- 12-month trend for the 3 drivers that matter most for this asset class (from the reference file)
- country risk snapshot: rating, CDS/spread, FX regime, convertibility
**Output:** `{comps:[...], trends:[{driver, direction, magnitude, source}], country:{...}, implied_range:{metric:[lo,hi]}}`

## 2. Quant
**Receives:** deal.json, assumption register, scripts/.
**Mandate:** run engine.py, montecarlo.py (with the event risks the deal warrants ON) and the tornado;
reconcile the sponsor's claims; produce a 2-way grid on the top-2 drivers; run the sizing solvers
(`solve_for`, `scale_capex_solve`, `max_loan_for_gate`). Report only what code printed.
**Output:** `{base:{...}, mc:{...}, tornado:[...], sponsor_reconciliation:[{metric, sponsor, ours, gap}], grid:{...}, sizing:{...}}`

## 3. Bull
**Receives:** deal.json, the Quant output, the Market & Comps output.
**Mandate:** make the strongest *evidenced* case. List the 3 conditions that must hold for the
upside case, and the data that would confirm each one early. Unsourced optimism is not allowed;
every claim cites the register or a comp.
**Output:** `{thesis, upside_case_inputs, conditions:[{condition, evidence, early_indicator}]}`

## 4. Bear (Red Team)
**Receives:** deal.json, the Quant output, the reference file's "What kills this" section. NOT the Bull output.
**Mandate:** produce a kill case and find the single assumption whose failure loses equity. Re-run
the engine with that assumption broken and report the result. Must answer three questions:
- what has to be true for us to lose money?
- how likely is it, from the base rates or history cited?
- what is the cheapest mitigant: a contract term, hedge, reserve, structure or price?
**Output:** `{kill_case_inputs, kill_case_result, top_failure_modes:[{mode, mechanism, probability_basis, mitigant}]}`

## 5. Risk Officer
**Receives:** the Quant output, gate_thresholds.json, the Bear output.
**Mandate:** pre-compute every gate metric from code outputs, list data gaps, and propose kill risks
in the gate's format (`{risk, mitigated: bool, evidence}`). For Lombard positions, add the
drawdown-to-call, the days-of-income cover, and lender terms (haircut changes at lender discretion?
is there a cure period?).
**Output:** `{gate_metrics:{...}, data_gaps:[...], kill_risks:[...]}`

## 6. Judge / Aggregator
**Receives:** all five outputs.
**Mandate:**
- Score each agent's argument on evidence quality (sourced? computed? specific?), from 0 to 5. Don't
  score on persuasiveness.
- Resolve disagreements by pointing to the specific evidence that settles each one. If the evidence
  doesn't settle it, list it as an open question with the data needed.
- Finalise the kill_risks list and the gate_input.json metrics (numbers only from Quant or Risk Officer).
- Write the "What would change our mind" list: 3 observable facts that would flip the verdict.

**Output:** `{scores:{role: 0-5}, resolved:[...], open_questions:[...], gate_input:{...}, change_mind:[...]}`

The Judge never overrides the gate. If the Judge believes the gate is wrong for this deal, it says
why in `open_questions` so the user can change the thresholds deliberately.

---

## Spawn template (use Agent tool; launch roles 1–5 in one turn, then the Judge)
```
You are the <ROLE> on an investment committee. Read agents/roles.md section <N> for your mandate and
output contract. Work in <workspace>/<deal>/. Inputs: <list exact files>. Write agent_<role>.json and
your reasoning to that folder. Cite every number to a code run or the assumption register.
```
