# ic-investment-analyst

Roger's personal investment-analysis agent. Personal use only; no GDH data here.

- `SKILL.md`, `agents/`, `references/`, `config/`: mirror of the installed ic-investment-analyst skill.
- `scripts/`: engine, Monte Carlo, risk gate, Excel builder (from the skill) plus `monitor.py` (daily re-score)
  and `watchlist_doc.py` (writes analysis results in the watchlist's document shape).
- `pfreg/`: independent regression suite for spreadsheet models (truth engine + flex cases).
- `docs/queue.md`: what "process my IC queue" does.
- Watchlist page and database: https://claude.ai/artifact/5igovkrXLThTbUTxMKin3Y

Rules
- Every number shown to Roger comes from a script run, never from prose.
- After changing `scripts/`: `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q scripts/test_engine.py tests/`.
  `tests/test_monitor.py` must reproduce the stored watchlist numbers exactly.
- Analysis and alerts only. Never place trades, move money or contact anyone.
