# Energy & grid infrastructure (PPAs, solar/wind/BESS, substations, transmission)

## Map to engine
| Deal term | engine field |
|---|---|
| EPC + development + IDC | `capex` (by construction year) |
| Contracted energy (MWh) × tariff, or availability payment | `revenue_y1` |
| Capacity factor ramp / degradation | `ramp` (solar: ~0.5%/yr decline, encode as a falling ramp) |
| Tariff indexation | `revenue_escalation` (0 if flat; use the actual index clause) |
| O&M, land, insurance | `fixed_opex`, `opex_pct_revenue` |
| Debt sculpting | approximate with `debt_tenor` + `debt_grace_years`; flag that real sculpting targets a constant DSCR |

Regulated assets (substations, transmission) have an availability-based return on a regulated
asset base. Model revenue as the allowed return on the RAB plus depreciation recovery, and stress for
regulatory reset risk.

## Comps to collect
- Recent PPA tariffs in the country and technology (auction results, regulator orders)
- EPC $/MW and battery $/kWh trends
- Project-finance margins and tenors from recent financial closes in the region
- Offtaker credit rating (utility or sovereign) and payment track record

## What kills this
1. **Offtaker credit.** The state utility pays late or renegotiates. Scenario: 6–12 months of receivables delay → liquidity, DSCR breach.
2. **Curtailment / grid congestion.** Scenario: ramp × 0.85.
3. **Tariff in local currency without USD indexation.** Scenario: fx_haircut = the country's historical 3-year devaluation.
4. **Merchant tail after PPA expiry.** Scenario: revenue −40% after the contract term.
5. **Change in law / regulatory reset.** Check for a stabilisation clause.

## Gate metrics produced
equity_irr, min_dscr, llcr, p_equity_loss, p_irr_below_hurdle, p_dscr_breach
