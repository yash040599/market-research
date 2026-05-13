"""
Sweep X = 10..20%, Y = 10..20% (integer grid, 11x11 = 121 combos).

For each combo: run the portfolio backtest across the cached NIFTY 50 data,
compute XIRR / CAGR / total-return / # trades / win-rate / avg holding /
max-drawdown / capital-deployed.

Outputs (all under research/results/):
    grid_metrics.csv           -- one row per (X,Y) with all metrics
    xirr_matrix.csv            -- pivoted X x Y matrix of XIRR%
    xirr_heatmap.png           -- seaborn heatmap of the matrix
    cagr_matrix.csv            -- same shape, CAGR%
    summary.md                 -- assumptions + top/worst lists + stats
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from research.backtest import (
    PortfolioRun,
    avg_holding_days,
    max_capital_deployed,
    max_drawdown,
    run_portfolio,
    win_rate,
)
from research.data_loader import fetch_all
from research.xirr import xirr

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

X_RANGE = list(range(10, 21))  # 10..20 inclusive
Y_RANGE = list(range(10, 21))  # 10..20 inclusive


def metrics_for(run: PortfolioRun, last_close_by_ticker: dict[str, float]) -> dict:
    cfs = run.cashflows
    inflow = sum(cf.amount for cf in cfs if cf.amount > 0)
    outflow = -sum(cf.amount for cf in cfs if cf.amount < 0)
    max_cap = max_capital_deployed(cfs)
    total_return_pct = ((inflow - outflow) / max_cap * 100.0) if max_cap > 0 else 0.0

    # XIRR
    amounts = [cf.amount for cf in cfs]
    dates = [cf.d for cf in cfs]
    irr = xirr(amounts, dates)
    xirr_pct = irr * 100.0 if irr is not None else float("nan")

    # CAGR over the actual deployment window (first buy -> last cashflow)
    if cfs:
        d_start = min(cf.d for cf in cfs if cf.amount < 0)
        d_end = max(cf.d for cf in cfs)
        years = max((d_end - d_start).days / 365.25, 1e-9)
    else:
        years = 1.0
    if max_cap > 0 and inflow > 0:
        cagr_pct = ((inflow / max_cap) ** (1.0 / years) - 1.0) * 100.0
    else:
        cagr_pct = float("nan")

    return {
        "X_pct": run.x_pct,
        "Y_pct": run.y_pct,
        "XIRR_pct": xirr_pct,
        "CAGR_pct": cagr_pct,
        "TotalReturn_pct": total_return_pct,
        "NumTrades": run.num_trades,
        "OpenPositions": len(run.open_trades),
        "WinRate_pct": win_rate(run.closed_trades, run.open_trades, last_close_by_ticker) * 100.0,
        "AvgHoldingDays": avg_holding_days(run.closed_trades),
        "MaxDrawdown_INR": max_drawdown(run.daily_equity),
        "MaxCapitalDeployed_INR": max_cap,
        "GrossInvested_INR": outflow,
        "GrossReturned_INR": inflow,
        "TerminalMTM_INR": run.terminal_mtm,
        "NetPnL_INR": inflow - outflow,
    }


def main() -> None:
    print("=" * 70)
    print(" NIFTY 50 ATH-Dip / Fixed-Gain-Exit  --  Grid Backtest")
    print("=" * 70)
    print("Loading data ...")
    per_stock = fetch_all(refresh=False)
    if not per_stock:
        print("No data loaded; aborting.", file=sys.stderr)
        sys.exit(1)

    last_close_by_ticker = {
        t: float(df["Close"].dropna().iloc[-1]) for t, df in per_stock.items()
    }
    earliest = min(df.index.min() for df in per_stock.values()).date()
    latest = max(df.index.max() for df in per_stock.values()).date()
    print(f"Universe: {len(per_stock)} tickers   Window: {earliest} -> {latest}")

    rows: list[dict] = []
    n = len(X_RANGE) * len(Y_RANGE)
    i = 0
    for x in X_RANGE:
        for y in Y_RANGE:
            i += 1
            run = run_portfolio(per_stock, float(x), float(y))
            row = metrics_for(run, last_close_by_ticker)
            rows.append(row)
            print(
                f"[{i:3d}/{n}] X={x:2d}%  Y={y:2d}%  "
                f"XIRR={row['XIRR_pct']:6.2f}%  CAGR={row['CAGR_pct']:6.2f}%  "
                f"Trades={row['NumTrades']:4d}  MaxCap=Rs.{row['MaxCapitalDeployed_INR']:>11,.0f}",
                flush=True,
            )

    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "grid_metrics.csv", index=False)

    # Pivot matrices
    xirr_mat = df.pivot(index="X_pct", columns="Y_pct", values="XIRR_pct").round(2)
    cagr_mat = df.pivot(index="X_pct", columns="Y_pct", values="CAGR_pct").round(2)
    xirr_mat.to_csv(RESULTS_DIR / "xirr_matrix.csv")
    cagr_mat.to_csv(RESULTS_DIR / "cagr_matrix.csv")

    # Heatmap
    plt.figure(figsize=(10, 8))
    sns.heatmap(
        xirr_mat,
        annot=True,
        fmt=".2f",
        cmap="RdYlGn",
        cbar_kws={"label": "XIRR (%)"},
        linewidths=0.4,
        linecolor="white",
    )
    plt.title("NIFTY 50 ATH-Dip Strategy  --  XIRR (%) by (X% dip-buy, Y% gain-sell)")
    plt.xlabel("Y  --  sell trigger: % gain from buy")
    plt.ylabel("X  --  buy trigger: % drop from ATH")
    plt.tight_layout()
    plt.savefig(RESULTS_DIR / "xirr_heatmap.png", dpi=140)
    plt.close()

    # Summary md
    top5 = df.sort_values("XIRR_pct", ascending=False).head(5)
    bot5 = df.sort_values("XIRR_pct", ascending=True).head(5)

    def _fmt_inr(v: float) -> str:
        return f"Rs.{v:,.0f}"

    md_lines: list[str] = []
    md_lines.append("# NIFTY 50 ATH-Dip Strategy -- Grid Backtest Results\n")
    md_lines.append(f"_Generated: {date.today().isoformat()}_\n")
    md_lines.append(f"**Universe:** {len(per_stock)} tickers (current NIFTY 50)  ")
    md_lines.append(f"**Window:** {earliest.isoformat()} -> {latest.isoformat()}  ")
    md_lines.append(f"**Trade size:** Rs.10,000 per buy  ")
    md_lines.append(f"**Grid:** X = {X_RANGE[0]}-{X_RANGE[-1]}%, Y = {Y_RANGE[0]}-{Y_RANGE[-1]}% (integer steps)\n")

    md_lines.append("## Assumptions\n")
    md_lines.append("- Yahoo Finance daily auto-adjusted closes (split + dividend adjusted).\n")
    md_lines.append("- Current NIFTY 50 membership held constant for the full 10y window (mild survivorship bias).\n")
    md_lines.append("- ATH = running max of close prices observed within the backtest window.\n")
    md_lines.append("- Fractional shares allowed; trade sized at Rs.10,000 notional at the buy-day close.\n")
    md_lines.append("- Sell on the first close >= buy * (1 + Y/100); re-arm immediately for next dip from ATH.\n")
    md_lines.append("- No brokerage / STT / slippage / liquidity caps.\n")
    md_lines.append("- Open positions at end-of-window are marked-to-market on the final close as a synthetic XIRR inflow.\n")
    md_lines.append("- Multiple stocks held simultaneously; no position-count cap; capital assumed available on demand.\n")
    md_lines.append("- CAGR computed as (GrossReturned / MaxCapitalDeployed) ^ (1/years) - 1, where years spans first buy to last cashflow.\n")
    md_lines.append("- Win-rate = % of trades (closed + open MTM) with positive P&L. Closed trades are 100% winners by construction.\n")

    md_lines.append("\n## XIRR Matrix (rows = X% dip, cols = Y% gain)\n")
    md_lines.append("```")
    md_lines.append(xirr_mat.to_string())
    md_lines.append("```\n")

    md_lines.append("## CAGR Matrix\n")
    md_lines.append("```")
    md_lines.append(cagr_mat.to_string())
    md_lines.append("```\n")

    md_lines.append("## Top 5 (X, Y) by XIRR\n")
    md_lines.append(top5.to_markdown(index=False, floatfmt=".2f"))
    md_lines.append("")

    md_lines.append("\n## Worst 5 (X, Y) by XIRR\n")
    md_lines.append(bot5.to_markdown(index=False, floatfmt=".2f"))
    md_lines.append("")

    # Trade summary across all combos
    md_lines.append("\n## Trade Summary Statistics (across full grid)\n")
    summary = df[
        ["NumTrades", "WinRate_pct", "AvgHoldingDays",
         "MaxDrawdown_INR", "MaxCapitalDeployed_INR", "NetPnL_INR"]
    ].describe().round(2)
    md_lines.append("```")
    md_lines.append(summary.to_string())
    md_lines.append("```\n")

    md_lines.append("\n## Files\n")
    md_lines.append("- `grid_metrics.csv` -- full per-combo metrics\n")
    md_lines.append("- `xirr_matrix.csv` / `cagr_matrix.csv` -- pivoted matrices\n")
    md_lines.append("- `xirr_heatmap.png` -- heatmap visualization\n")

    (RESULTS_DIR / "summary.md").write_text("\n".join(md_lines), encoding="utf-8")

    print()
    print(f"Wrote: {RESULTS_DIR / 'grid_metrics.csv'}")
    print(f"Wrote: {RESULTS_DIR / 'xirr_matrix.csv'}")
    print(f"Wrote: {RESULTS_DIR / 'cagr_matrix.csv'}")
    print(f"Wrote: {RESULTS_DIR / 'xirr_heatmap.png'}")
    print(f"Wrote: {RESULTS_DIR / 'summary.md'}")
    print("\nTop 5:")
    print(top5.to_string(index=False))


if __name__ == "__main__":
    main()
