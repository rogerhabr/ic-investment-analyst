"""End-to-end: build formula workbooks, recalc in a real engine, run the suite.
The buggy fixture must reproduce the plugin's 2026-10-06 cap-90 numbers and be diagnosed."""
import shutil

import pytest

from pfreg import runner
from tests.make_fixtures import build

needs_engine = pytest.mark.skipif(
    not (shutil.which("soffice") or shutil.which("libreoffice")), reason="no LibreOffice")


@needs_engine
def test_correct_model_passes_every_flex(tmp_path):
    build(tmp_path / "ok.xlsx")
    rep = runner.run(str(tmp_path / "ok.xlsx"), "pf2", backend="libreoffice")
    assert rep["passed"], runner.render(rep)


@needs_engine
def test_buggy_model_fails_only_where_dscr_binds(tmp_path):
    build(tmp_path / "bad.xlsx", buggy=True)
    rep = runner.run(str(tmp_path / "bad.xlsx"), "pf2", backend="libreoffice")
    c = rep["cases"]
    assert c["base"]["passed"] and c["fee3"]["passed"] and c["cap60"]["passed"]
    assert not c["cap90"]["passed"] and not rep["passed"]
    got = {r["metric"]: r["got"] for r in c["cap90"]["rows"]}
    # identical to what the plugin's workbook showed on 2026-10-06
    assert abs(got["total_uses"] - 100.6814) < 1e-4
    assert abs(got["idc"] - 2.6342) < 1e-4
    assert abs(got["equity_irr"] - 0.331591) < 1e-6
    diag = " ".join(c["cap90"]["diagnosis"])
    for sig in ("8.0552", "lambda", "Total uses = D/cap", "IDC = r x cap"):
        assert sig in diag
