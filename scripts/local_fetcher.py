"""
local_fetcher.py — runs on Roger's always-on machine (WSL2 / server), NOT in the cloud workspace.
Pulls free macro + market data that has no connector and writes ONE snapshot file the agents read.

Sources (all free):
  FRED        rates, spreads, inflation           needs FRED_API_KEY (free, fred.stlouisfed.org)
  World Bank  GDP growth, inflation, FX reserves   no key (wbgapi)
  yfinance    prices / FX / ETFs                   no key — UNOFFICIAL scraper, fallback only
Output: market_snapshot.json  {as_of_utc, series: {id: {value, date, source, stale_days}}, errors: [...]}
Every value carries its own date and source; staleness is computed, never hidden.

Install:  pip install fredapi wbgapi yfinance pandas
Run:      python local_fetcher.py --watchlist watchlist.json --out market_snapshot.json
Schedule: Windows Task Scheduler (pwsh) or cron in WSL2, daily 06:00 Asia/Dubai.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

DEFAULT_WATCHLIST = {
    "fred": {
        "DGS10": "US 10Y Treasury yield",
        "DGS2": "US 2Y Treasury yield",
        "SOFR": "SOFR",
        "BAMLH0A0HYM2": "US HY OAS spread",
        "BAMLEMCBPIOAS": "EM corporate OAS spread",
        "CPIAUCSL": "US CPI index",
        "DCOILBRENTEU": "Brent crude",
    },
    "worldbank": {
        "indicators": {"NY.GDP.MKTP.KD.ZG": "GDP growth %", "FP.CPI.TOTL.ZG": "CPI inflation %",
                       "FI.RES.TOTL.MO": "Reserves, months of imports"},
        "countries": ["ARE", "SAU", "KWT", "NGA", "TUR", "MAR", "EGY", "LBN"],
    },
    "yfinance": {
        "USDNGN=X": "USD/NGN", "USDTRY=X": "USD/TRY", "USDKWD=X": "USD/KWD", "USDEGP=X": "USD/EGP",
        "^GSPC": "S&P 500", "ACWI": "MSCI ACWI ETF", "AGG": "US Agg bond ETF", "EMB": "EM USD bond ETF",
        "NVDA": "NVIDIA", "EQIX": "Equinix", "DLR": "Digital Realty",
    },
}


def _stale(date_str: str, now: dt.date) -> int | None:
    try:
        return (now - dt.date.fromisoformat(str(date_str)[:10])).days
    except Exception:
        return None


def fetch_fred(series: dict, now: dt.date, out: dict, errors: list) -> None:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        errors.append("FRED skipped: FRED_API_KEY not set")
        return
    from fredapi import Fred
    fred = Fred(api_key=key)
    for sid, label in series.items():
        try:
            s = fred.get_series(sid).dropna()
            d = s.index[-1].date().isoformat()
            out[f"fred:{sid}"] = {"label": label, "value": float(s.iloc[-1]), "date": d,
                                  "source": "FRED", "stale_days": _stale(d, now)}
        except Exception as e:  # keep going — one bad series must not kill the snapshot
            errors.append(f"FRED {sid}: {e}")


def fetch_worldbank(cfg: dict, now: dt.date, out: dict, errors: list) -> None:
    try:
        import wbgapi as wb
    except ImportError:
        errors.append("World Bank skipped: pip install wbgapi")
        return
    for ind, label in cfg["indicators"].items():
        for cc in cfg["countries"]:
            try:
                rows = [r for r in wb.data.fetch(ind, cc, mrv=3) if r.get("value") is not None]
                if not rows:
                    errors.append(f"WB {ind} {cc}: no recent value")
                    continue
                r = max(rows, key=lambda x: x["time"])
                year = int(str(r["time"]).replace("YR", ""))
                out[f"wb:{ind}:{cc}"] = {"label": f"{label} ({cc})", "value": float(r["value"]),
                                         "date": f"{year}-12-31", "source": "World Bank WDI",
                                         "stale_days": _stale(f"{year}-12-31", now)}
            except Exception as e:
                errors.append(f"WB {ind} {cc}: {e}")


def fetch_yf(tickers: dict, now: dt.date, out: dict, errors: list) -> None:
    try:
        import yfinance as yf
    except ImportError:
        errors.append("yfinance skipped: pip install yfinance")
        return
    for tk, label in tickers.items():
        try:
            h = yf.Ticker(tk).history(period="1y", auto_adjust=True)["Close"].dropna()
            if h.empty:
                errors.append(f"yfinance {tk}: empty")
                continue
            rets = h.pct_change().dropna()
            d = h.index[-1].date().isoformat()
            peak = h.cummax()
            out[f"yf:{tk}"] = {
                "label": label, "value": float(h.iloc[-1]), "date": d,
                "source": "yfinance (unofficial)", "stale_days": _stale(d, now),
                "ret_1y": float(h.iloc[-1] / h.iloc[0] - 1),
                "vol_1y_ann": float(rets.std() * 252 ** 0.5),
                "max_drawdown_1y": float((h / peak - 1).min()),
            }
        except Exception as e:
            errors.append(f"yfinance {tk}: {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watchlist", help="JSON with fred/worldbank/yfinance blocks (defaults built in)")
    ap.add_argument("--out", default="market_snapshot.json")
    a = ap.parse_args()
    wl = json.load(open(a.watchlist)) if a.watchlist else DEFAULT_WATCHLIST
    now = dt.datetime.now(dt.timezone.utc)
    series, errors = {}, []
    fetch_fred(wl.get("fred", {}), now.date(), series, errors)
    if "worldbank" in wl:
        fetch_worldbank(wl["worldbank"], now.date(), series, errors)
    fetch_yf(wl.get("yfinance", {}), now.date(), series, errors)
    snap = {"as_of_utc": now.isoformat(timespec="seconds"), "n_series": len(series),
            "series": series, "errors": errors}
    tmp = a.out + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(snap, fh, indent=1)
    os.replace(tmp, a.out)  # atomic: readers never see a half-written file
    print(f"{len(series)} series, {len(errors)} errors -> {a.out}")
    sys.exit(0 if series else 1)


if __name__ == "__main__":
    main()
