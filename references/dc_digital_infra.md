# Data centers, powered land, GPU/AI infrastructure

## Value chain: grid → chip → token
The value chain runs grid MW → critical IT MW (÷ PUE) → racks / GPUs → tokens or colo revenue. Price
each link and find the binding constraint. Today that's usually grid MW and lead time, not capital.

## Map the deal to engine inputs
| Deal term | engine field | Watch |
|---|---|---|
| Capex $/MW IT × MW, split by year | `capex` | separate shell/core vs MEP vs IT; GPU capex is short-lived |
| Contracted MW × $/kW/month × 12 | `revenue_y1` | gross vs net of power pass-through |
| Lease-up / energisation schedule | `ramp` | tie to grid energisation dates, not marketing |
| Power + O&M | `opex_pct_revenue`, `fixed_opex` | power pass-through vs margin; PUE assumption |
| Local-currency tariffs | `fx_haircut` | NGN/TRY/EGP revenue with USD debt = mismatch |
| Exit | `terminal_multiple_ebitda` | stabilised colo vs build-to-suit vs GPU cloud trade at very different multiples |
| Ramp protection | `debt_grace_years` | lenders give 1–3 years interest-only; check the term sheet |

Always run the power check before trusting opex: annual power cost = IT MW × PUE × 8,760 h × tariff × utilisation.
Compare it with revenue. If it's above ~25% of revenue, the lease must pass power through (metered, or at a
contracted PUE cap), so model revenue net of power and say so. A "$/kW/month all-in" rent in a
high-tariff grid can be EBITDA-negative.

Loan tenor: a lender's "12 years with 2 years grace" usually means 12 years in total. Set
`debt_tenor` = quoted − grace unless the term sheet says otherwise, and state the reading.
Use `capitalise_grace_interest` when interest during construction or ramp rolls up, and `dsra_months` (6 is
typical for DFIs) when a reserve is required.

GPU-as-a-service: model GPUs as a separate tranche with a 4–6 year life and a residual-value curve.
GPU hourly pricing has repriced sharply between generations, so stress revenue_y1 −40% in years
3–5. A 20-year building life must not inherit GPU-like revenue.

Powered land: value = optionality on scarce energised MW. Model as land + capex-light lease (ground
rent or $/MW premium), and check the PPA/grid connection's transferability to the buyer.

## Comps to collect (Market & Comps agent)
- $/MW IT capex for the region and tier (broker reports, recent financings)
- Wholesale colo $/kW/month, hyperscaler build-to-suit yields
- Listed DC multiples (EV/EBITDA: Equinix, Digital Realty, regional peers)
- Power tariff and grid-connection lead time in the country (regulator orders)
- GPU rental $/hr trend for the relevant generation

## What kills this (Bear's starting list)
1. **Grid energisation slip.** Revenue ramp moves, debt doesn't. Scenario: ramp shifted +12 to 18 months.
2. **Anchor tenant non-commitment, or termination-for-convenience in the lease.** Scenario: revenue_y1 −50% until re-let.
3. **FX mismatch.** Local-currency revenue against USD debt. Scenario: a 30–50% devaluation drawn from that country's own history.
4. **Technology obsolescence.** Liquid-cooling and rack-density shifts strand an air-cooled hall. Scenario: capex +15% retrofit in year 5.
5. **Capex overrun on long-lead MEP** (transformers, switchgear, chillers). Scenario: capex ×1.25, COD +6 months.
6. **Land title or permit not transferable.** This is a kill risk, not a scenario.

## Gate metrics produced
equity_irr, min_dscr, llcr (engine) · p_equity_loss, p_irr_below_hurdle, p_dscr_breach (simulate_project)
