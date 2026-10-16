# Real estate (income property, off-plan, resale)

## Map to engine
| Term | engine field |
|---|---|
| Purchase price + fees (DLD 4%, agency, registration) + fit-out | `capex` |
| Off-plan payment plan | `capex` by installment year, handover payment in the last capex year (see timing note) |
| Net rent achievable | `revenue_y1` (gross rent × (1 − vacancy)) |
| Service charges, maintenance, management | `fixed_opex`, `opex_pct_revenue` |
| Mortgage | `debt_pct`, `debt_rate`, `debt_tenor`; drawn at handover with `debt_draw_year` |
| Exit | `terminal_multiple_ebitda` = 1 / exit cap rate (e.g. 6.5% cap ⇒ 15.4×) |

Off-plan timing: put each installment in `capex` at its year index, with the handover payment in the
LAST capex year. Set `debt_draw_year` to that same index, so the mortgage funds the handover payment. Rent
starts the following year (t = len(capex)). The engine repays any outstanding mortgage at exit (`balloon`),
and LLCR is then blank by design.
Handover-delay scenario: insert a 0 into `capex` before the handover year AND reduce `operating_years` by 1 when
the exit date is fixed, otherwise the delay just shifts the whole deal and barely moves IRR.
Max-price solve: when fees, premium or loan scale with price (DLD 4%, agency 2%, LTV), write a small function that
rebuilds `capex`/`debt_pct` from the price and bisect on it. `scale_capex_solve` scales everything together,
which is only right when every cost is proportional to price.

Exit multiple: derive it from comps, not taste. Use exit cap rate = today's net yield on comparable
completed stock (Market & Comps agent), plus or minus a stated drift, and set `exit_multiple_sd` ≈ 0.10–0.15 in
project MC. Report the break-even exit price with `solve_for(..., "terminal_multiple_ebitda", ...)`.
Benchmark: always report `excess_vs_benchmark` against the money-market / T-bill yield; that is the
user's real alternative.

Set `operating_years` to the intended hold period, so the terminal value is the exit sale.
Compute `net_yield` = year-1 NOI / total acquisition cost and pass it to the gate.
Compute `ltv` = debt / value.

## Comps to collect
- Transaction $/sqft for the same project or community (land department / DLD data, portals)
- Rental comps for the same unit type, with achieved rents rather than asking rents
- Supply pipeline: handovers in the community over the next 24 months
- Mortgage rates (EIBOR + margin in UAE) and the trend

## What kills this
1. **Handover delay on off-plan.** Capital is locked with no rent. Scenario: ramp +12 months.
2. **Supply wave at handover.** Scenario: rent −15%, exit cap +75 bps.
3. **Developer or escrow risk.** Check the escrow-account registration. Unregistered escrow is a kill risk.
4. **Rate reset on a floating mortgage.** Scenario: debt_rate +200 bps.
5. **Exit liquidity.** Transaction volume for the unit type is thin, so the exit has to be discounted.

## Gate metrics produced
equity_irr (hurdle), net_yield, p_equity_loss (simulate_project), ltv
