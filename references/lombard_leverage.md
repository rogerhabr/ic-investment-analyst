# Lombard / leveraged investment against a portfolio

## Core idea
Borrowing against a portfolio sells a down-and-out put on your own equity. Returns look like
carry × leverage in calm markets. When collateral touches the call LTV, the lender can force a sale
at the low, and the loss crystallises even if prices recover the next month. Risk is path-dependent,
so a DCF or average-return view understates it. Always run `simulate_lombard`.

## Inputs to collect (each needs a source)
| Input | Field | Notes |
|---|---|---|
| Portfolio value, composition | `portfolio_value` | the lender's *lending value* = Σ(position × advance rate), not market value |
| Loan amount | `loan` | include any undrawn facility the user intends to use |
| Expected return / vol | `mu`, `sigma` | from the weighted holdings; vol from ≥3y history, and stress with the 2008/2020/2022 vol |
| Income yield | `income_yield` | dividends/coupons actually paid in cash |
| Loan rate | `loan_rate` | base (SOFR/EIBOR) + margin; floating, so stress +200 bps |
| Margin-call LTV, liquidation LTV | `ltv_call`, `ltv_liquidate` | from the credit agreement. Ask for them; never assume |
| Jumps | `jump_lambda`, `jump_mean`, `jump_std` | use for single stocks, EM, crypto or concentrated books; λ≈0.5–1/yr |

Set `advance_rate` to the weighted advance rate, so the MC tests LTV on lending value the way the lender does.
Leave `ltv_call`/`ltv_liquidate` exactly as written in the credit agreement.

## Sizing (the answer the user actually needs)
Run `max_loan_for_gate` twice: base vol, and stressed vol (1.5× with `haircut_cut` 0.10,
`haircut_cut_prob` 0.5, `haircut_trigger_dd` 0.10). Recommend the smaller one, plus a cash reserve outside
the pledged account equal to the cure cash in the worst stress row of the workbook.
Set `interest_paid_externally=True` when the borrower pays interest from income outside the pledge; the
default capitalises any shortfall into the loan.

## Must-report metrics
- LTV0, drawdown to call, drawdown to liquidation (closed-form, intuitive for the user)
- P(margin call 12m), P(forced liquidation 12m), median days to first call
- carry_spread = proceeds yield − expected loss − loan rate (set `proceeds_yield`); negative carry = paying to take risk
- VaR95, CVaR95 of levered equity; P(equity loss)
- Cure capacity: days of income, or liquid assets outside the pledged account, available to post margin

## Scenarios (beyond MC)
1. Historical replay: apply the 2007–09 (S&P 500 ≈ −57% peak-to-trough), 2020 (≈ −34% in 23 trading days), and 2022
   (equities ≈ −25% with US aggregate bonds ≈ −17% *together*) paths to the current book. A 60/40 "balanced" book lost its
   diversification in 2022, and its LTV moved faster than vol-based models implied.
2. Lender reprices haircuts mid-crisis (advance rates cut 10–20 pts): recompute the effective call LTV.
3. Rates +200 bps on a floating loan.

## What kills this
1. **Lender discretion** to change advance rates without notice or a cure period. This is a kill risk if present and unmitigated.
2. **Concentrated or illiquid collateral** (single stock, private fund units with low advance rates).
3. **Negative carry with no capital-gain thesis.**
4. **Correlated collateral and use of proceeds.** Borrowing against AI equities to invest in AI infra doubles the same factor bet.
5. **Currency mismatch** between the loan and the collateral.

## Gate metrics produced
carry_spread, p_margin_call, p_forced_liquidation, ltv0, cvar_95
