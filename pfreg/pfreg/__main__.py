"""pfreg - regression suite for spreadsheet financial models.

  python -m pfreg run MODEL.xlsx --suite pf2 [--backend auto|excel|libreoffice]
                                  [--map pins.json] [--json report.json] [--keep DIR]
  python -m pfreg truth --suite pf2 [--flex cap90]
  python -m pfreg suites
Exit code: 0 = all flex cases pass, 1 = any failure, 2 = usage/setup error.
"""
import argparse
import json
import sys

from . import runner


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pfreg")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("model")
    r.add_argument("--suite", required=True, choices=[k for k in runner.SUITES if k != "tol"])
    r.add_argument("--backend", default="auto", choices=["auto", "excel", "libreoffice"])
    r.add_argument("--map", help='JSON pinning metrics/inputs to cells, e.g. {"debt": "Model!B23"}')
    r.add_argument("--json")
    r.add_argument("--keep", help="keep the recalculated flex workbooks in this folder")
    t = sub.add_parser("truth")
    t.add_argument("--suite", required=True)
    t.add_argument("--flex")
    sub.add_parser("suites")
    a = ap.parse_args(argv)

    if a.cmd == "suites":
        for k, v in runner.SUITES.items():
            if k != "tol":
                print(f"{k:5s} flex={list(v['flex'])} inputs={v['inputs']}")
        return 0
    if a.cmd == "truth":
        for f in ([a.flex] if a.flex else runner.SUITES[a.suite]["flex"]):
            e = runner.expected(a.suite, f)
            print(f"\n[{a.suite}/{f}] inputs={runner.input_values(a.suite, f)}")
            for k in runner.SUITES[a.suite]["metrics"]:
                v = e[k]
                print(f"  {k:<16} {v:.6f}" if isinstance(v, float) else f"  {k:<16} {v}")
        return 0
    try:
        pins = json.load(open(a.map)) if a.map else None
        rep = runner.run(a.model, a.suite, a.backend, pins, a.keep)
    except (KeyError, RuntimeError, FileNotFoundError) as e:
        print(f"setup error: {e}", file=sys.stderr)
        return 2
    print(runner.render(rep))
    if a.json:
        runner.save_json(rep, a.json)
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
