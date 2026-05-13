"""
Download daily OHLC + adjusted close for the NIFTY 50 universe and cache to
parquet/CSV under research/data/.

Why yfinance?
  * Free, no auth needed.
  * Provides ".NS" (NSE) symbols with split/dividend-adjusted closes via
    auto_adjust=True -- which is exactly what the strategy spec asks for
    ("use adjusted closing prices if available").

Usage:
    python -m research.data_loader            # fetches all 50 tickers
    python -m research.data_loader --refresh  # ignore cache, re-download
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from research.nifty50 import NIFTY_50

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# 10-year window ending today.
END_DATE = datetime.utcnow().date()
START_DATE = END_DATE - timedelta(days=365 * 10 + 5)


def _cache_path(ticker: str) -> Path:
    safe = ticker.replace("&", "AND").replace("/", "_")
    return DATA_DIR / f"{safe}.csv"


def fetch_one(ticker: str, *, refresh: bool = False) -> pd.DataFrame | None:
    path = _cache_path(ticker)
    if path.exists() and not refresh:
        df = pd.read_csv(path, parse_dates=["Date"])
        df = df.set_index("Date").sort_index()
        return df

    try:
        df = yf.download(
            ticker,
            start=START_DATE.isoformat(),
            end=(END_DATE + timedelta(days=1)).isoformat(),
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception as exc:  # noqa: BLE001 -- network/3rdparty
        print(f"  ! {ticker}: download failed: {exc}", file=sys.stderr)
        return None

    if df is None or df.empty:
        print(f"  ! {ticker}: empty frame", file=sys.stderr)
        return None

    # yfinance >= 0.2.40 returns multi-index columns even for single tickers
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index.name = "Date"
    df.to_csv(path)
    return df


def fetch_all(*, refresh: bool = False) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for i, t in enumerate(NIFTY_50, 1):
        print(f"[{i:2d}/{len(NIFTY_50)}] {t}", flush=True)
        df = fetch_one(t, refresh=refresh)
        if df is not None and not df.empty:
            out[t] = df
        # be polite to Yahoo when refreshing
        if refresh:
            time.sleep(0.4)
    print(f"\nLoaded {len(out)}/{len(NIFTY_50)} tickers.")
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true", help="ignore cache")
    args = p.parse_args()
    fetch_all(refresh=args.refresh)


if __name__ == "__main__":
    main()
