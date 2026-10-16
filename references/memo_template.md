# IC memo template

Use these sections in this order. Keep the memo to two pages; the appendix is the Excel model.

```
# <Deal name> — IC Memo            <date> · <asset class> · <country> · <ticket size> · debate: parallel|sequential (degraded)

## Verdict: PROCEED | CONDITIONAL | DECLINE   (gate score NN/100)
One sentence: the number that drives the verdict and the rule it was tested against.

## Sizing answer
Max entry price / max loan / required rent-tariff that passes the gate (solver output), and cash reserve.

## Versus the alternative
Equity IRR − benchmark (money-market / T-bill, sourced and dated) = excess_vs_benchmark.

## Thesis (falsifiable)
"This works if <driver> stays <above/below> <level>. We will know by <date/milestone>."

## Returns (base · sponsor · downside)       ← table, from engine/MC only
Equity IRR | Project IRR | NPV @ hurdle | Min DSCR | LLCR | MOIC | P(IRR<hurdle) | P(loss)
(Lombard: LTV0 | drawdown to call | P(call 12m) | P(forced sale) | carry spread | CVaR95)

## Sponsor reconciliation
Their headline vs our rebuild, and the gap and its cause.

## Market & comps
Comps table (sourced, dated) · trend signals · where this deal sits in the range.

## Top risks (ranked by tornado swing × likelihood)
| # | Risk | Mechanism | Impact on IRR | Likelihood basis | Mitigant | Mitigated? |

## Bull vs Bear: what the evidence settled, what is still open

## Conditions (if CONDITIONAL)
Concrete and checkable: "DSRA of 6 months' debt service funded at close".

## What would change our mind
3 observable facts.

## Data gaps and confidence
Inputs with confidence L, stale data, connectors not available.

Sources: [Title](URL) list
```
