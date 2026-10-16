# pfreg — regression suite for spreadsheet financial models

Judges a workbook against an independent Python truth engine, across **flex cases**
(input changes that flip which constraint binds). Built after the financial-analysis plugin's
V2 model passed its base case and silently overstated equity IRR by 11.6 pts when DSCR bound.

## Install (Windows, pwsh 7, non-elevated)
```powershell
cd pfreg
py -m pip install -r requirements.txt     # pywin32 drives Excel for recalculation
py -m pytest -q                           # engine + end-to-end tests (e2e needs LibreOffice, else skipped)
```

## Use
```powershell
py -m pfreg suites                                         # dcf | pf1 | pf2
py -m pfreg truth --suite pf2 --flex cap90                 # expected values only
py -m pfreg run "C:\Users\Roger\DCF_Model_2026-10-06.xlsx" --suite dcf
py -m pfreg run "C:\Users\Roger\PF_v2.xlsx" --suite pf2 --json report.json --keep .\flexruns
```
Exit code `0` pass · `1` any metric fails · `2` setup error (label not found, no recalc engine).

## How it works
1. **Locate** each metric by label regex (last match wins, so summary blocks beat working lines);
   pin anything ambiguous: `--map pins.json` → `{"debt": "Model!B23", "cap": "Inputs!C8"}`.
2. For every flex case: copy the workbook, overwrite input cells, **recalculate** (Excel COM →
   LibreOffice fallback), read values.
3. Compare to `truth.py` with tolerances: $0.001m · ratios 0.0001x · rates 0.0005 pct-pts.
4. **Diagnose** known failure signatures (λ hard-wired to cap, TU = D/cap, IDC on cap,
   sources ≠ uses, DSCR limit ignored, covenant breach, end-of-year vs mid-year).
5. Flags output cells that hold **numbers instead of formulas**.

## Suites
| Suite | Model | Flex cases |
|---|---|---|
| `dcf` | 5-yr FCF, Gordon TV, mid-year | base · WACC 9% · g 2% |
| `pf1` | sculpted debt, cap on capex, single draw | base · cap 95% (DSCR binds) · cap 50% |
| `pf2` | + pro-rata draws, IDC, fee, DSRA, cap on total uses (circular) | base · fee 3% · **cap 90%** · cap 60% |

## Proof (2026-10-06)
- `tests/test_truth.py`: engine vs hand values, closed form, and the plugin-confirmed figures.
- `tests/test_e2e.py`: a correct formula workbook passes all 4 pf2 flex cases; a workbook with the
  plugin's defect passes base/fee3/cap60 and fails cap90 with **the same numbers the plugin produced**
  (TU 100.6814, IDC 2.6342, equity IRR 33.1591%) and all four root causes named.

## Limits
- Your original file is never modified; flex copies are saved through openpyxl, which drops
  charts/VBA in the copy (values are unaffected).
- Truth engine: annual periods, 2 construction periods for `pf2`. Quarterly/tax/lock-up/cash sweep
  are not modelled yet — add a suite before trusting a model with those features.
- Excel COM backend is written but not executed in this build environment; first Windows run is its test.
