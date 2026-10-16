# Listed equities, ETFs, funds

No project waterfall. Compute directly in Python (write a short script; don't do arithmetic in prose):
- expected_return: state the method (earnings yield + growth, dividend yield + growth, or a published
  capital-market assumption with its source), not just a number
- Historical: 1y/3y/5y return, annualised vol, max drawdown, beta vs ACWI or the local index (from
  a connector or the snapshot)
- `sharpe` = (expected_return − risk-free) / vol, with risk-free from fred:DGS2 or the local equivalent
- `var_95` = 1.645 × vol over a 1-year horizon (parametric). Also report the historical 5% quantile, since fat tails matter.
- `max_drawdown_hist` from the longest price history available
- `single_name_weight` = the position as a share of the user's stated portfolio. If the portfolio is unknown, mark it as a data gap.

Funds and ETFs: look up the TER, tracking difference, AUM, liquidity (ADV), top-10 concentration,
domicile and withholding tax, and whether the fund is synthetic or physical.

## What kills this
1. **Valuation compression.** Scenario: multiple to its 10-year 25th percentile.
2. **Concentration.** Top-10 holdings over 50%, or a single theme (e.g. AI capex) correlated with the user's own business exposure. Roger's income is already DC/AI-linked, so flag correlated bets explicitly.
3. **Liquidity mismatch** in semi-liquid or evergreen funds: redemption gates.
4. **FX of the share class against the user's base currency.**

## Gate metrics produced
expected_return (with country uplift), sharpe, var_95, max_drawdown_hist, single_name_weight
