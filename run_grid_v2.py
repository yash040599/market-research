"""
V2 Grid Sweep: finite-capital ATH-dip strategy vs NIFTY 50 benchmark.

New features vs run_grid.py:
  - Rs.1,00,000 starting capital, Rs.20,000 per lot
  - Profit proceeds recycled into next buys
  - NIFTY 50 index benchmark (Rs.1L lump-sum)
  - Alpha heatmap (strategy CAGR - benchmark CAGR)
  - 3-year rolling returns (both original infinite-money & V2)
  - Yearly breakdown: invested / cash / asset value
  - Taxation: STCG 20%, LTCG 12.5%

Outputs go to research/results_v2/ (original results/ untouched).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Make `from research.X import ...` work when run from within the folder
_this_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_this_dir))
sys.path.insert(0, str(_this_dir.parent))
# Register this directory as the 'research' package
import types as _types
_pkg = _types.ModuleType("research")
_pkg.__path__ = [str(_this_dir)]
sys.modules.setdefault("research", _pkg)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from research.backtest import run_portfolio as run_portfolio_v1, _build_daily_equity
from research.backtest import TRADE_NOTIONAL as V1_NOTIONAL
from research.backtest_v2 import (
    INITIAL_CAPITAL,
    TRADE_NOTIONAL,
    PortfolioRunV2,
    avg_holding_days,
    max_drawdown,
    rolling_returns,
    run_portfolio_v2,
    win_rate,
)
from research.data_loader import fetch_all
from research.xirr import xirr

RESULTS_DIR = Path(__file__).resolve().parent / "results_v2"
RESULTS_DIR.mkdir(exist_ok=True)

X_RANGE = list(range(10, 21))
Y_RANGE = list(range(10, 21))

# ---------------------------------------------------------------------------
# NIFTY 50 index benchmark
# ---------------------------------------------------------------------------

def _load_nifty_index(per_stock: dict[str, pd.DataFrame]) -> pd.Series:
    """
    Approximate NIFTY 50 index via equal-weight daily returns of the universe.
    We don't have the actual index data from Yahoo easily, so we synthesise it.
    Start at Rs.1,00,000 and compound daily.
    """
    # Collect daily returns
    rets = pd.DataFrame()
    for tkr, df in per_stock.items():
        c = df["Close"].dropna().pct_change()
        rets[tkr] = c
    avg_ret = rets.mean(axis=1).fillna(0.0)
    index_val = (1 + avg_ret).cumprod() * INITIAL_CAPITAL
    index_val.iloc[0] = INITIAL_CAPITAL
    return index_val


def _benchmark_cagr(index_series: pd.Series) -> float:
    if index_series.empty or len(index_series) < 2:
        return 0.0
    start_val = INITIAL_CAPITAL
    end_val = float(index_series.iloc[-1])
    days = (index_series.index[-1] - index_series.index[0]).days
    years = max(days / 365.25, 1e-9)
    return ((end_val / start_val) ** (1.0 / years) - 1.0) * 100.0

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def metrics_for_v2(run: PortfolioRunV2, last_close: dict[str, float],
                   benchmark_cagr: float) -> dict:
    final_val = run.final_value
    start_val = INITIAL_CAPITAL
    if run.daily_equity.empty:
        years = 1.0
    else:
        days = (run.daily_equity.index[-1] - run.daily_equity.index[0]).days
        years = max(days / 365.25, 1e-9)
    cagr = ((final_val / start_val) ** (1.0 / years) - 1.0) * 100.0
    total_return = ((final_val - start_val) / start_val) * 100.0
    alpha = cagr - benchmark_cagr

    # XIRR: initial outflow + final inflow
    xirr_amounts = [-INITIAL_CAPITAL, final_val]
    if run.daily_equity.empty:
        xirr_dates = [date.today(), date.today()]
        xirr_pct = float("nan")
    else:
        d0 = run.daily_equity.index[0].date()
        d1 = run.daily_equity.index[-1].date()
        xirr_dates = [d0, d1]
        irr = xirr(xirr_amounts, xirr_dates)
        xirr_pct = irr * 100.0 if irr is not None else float("nan")

    # Tax totals
    total_stcg = sum(s.stcg_tax for s in run.yearly_snapshots)
    total_ltcg = sum(s.ltcg_tax for s in run.yearly_snapshots)
    total_tax = total_stcg + total_ltcg
    if run.yearly_snapshots:
        final_snapshot = run.yearly_snapshots[-1]
        if not np.isclose(total_stcg, final_snapshot.cumulative_stcg_tax, rtol=0, atol=0.01):
            raise AssertionError("Annual STCG tax rows do not match final cumulative STCG tax")
        if not np.isclose(total_ltcg, final_snapshot.cumulative_ltcg_tax, rtol=0, atol=0.01):
            raise AssertionError("Annual LTCG tax rows do not match final cumulative LTCG tax")

    wr = win_rate(run.closed_trades, run.open_trades, last_close) * 100.0

    return {
        "X_pct": run.x_pct,
        "Y_pct": run.y_pct,
        "XIRR_pct": xirr_pct,
        "CAGR_pct": cagr,
        "Benchmark_CAGR_pct": benchmark_cagr,
        "Alpha_pct": alpha,
        "TotalReturn_pct": total_return,
        "FinalValue_INR": final_val,
        "NumTrades": run.num_trades,
        "OpenPositions": len(run.open_trades),
        "WinRate_pct": wr,
        "AvgHoldingDays": avg_holding_days(run.closed_trades),
        "MaxDrawdown_INR": max_drawdown(run.daily_equity),
        "TotalInvested_INR": run.total_invested,
        "TotalReturned_INR": run.total_returned,
        "FinalCash_INR": run.final_cash,
        "FinalAsset_INR": run.final_asset_value,
        "STCG_Tax_INR": total_stcg,
        "LTCG_Tax_INR": total_ltcg,
        "TotalTax_INR": total_tax,
        "NetAfterTax_INR": final_val - total_tax,
    }

# ---------------------------------------------------------------------------
# Rolling returns for V1 (original infinite-money)
# ---------------------------------------------------------------------------

def _v1_equity_series(per_stock: dict[str, pd.DataFrame],
                      x_pct: float, y_pct: float) -> pd.Series:
    """Build a normalised equity curve for V1 starting at 1L for comparability."""
    run = run_portfolio_v1(per_stock, x_pct, y_pct)
    # V1 daily_equity is a P&L curve (starts near 0). Convert to value curve.
    # We normalise: value = INITIAL_CAPITAL + pnl_curve
    eq = run.daily_equity + INITIAL_CAPITAL
    return eq


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print(" NIFTY 50 ATH-Dip Strategy V2 (Finite Capital) -- Grid Backtest")
    print("=" * 70)
    print("Loading data ...")
    per_stock = fetch_all(refresh=False)
    if not per_stock:
        print("No data loaded; aborting.", file=sys.stderr)
        sys.exit(1)

    last_close = {t: float(df["Close"].dropna().iloc[-1])
                  for t, df in per_stock.items()}
    earliest = min(df.index.min() for df in per_stock.values()).date()
    latest = max(df.index.max() for df in per_stock.values()).date()
    print(f"Universe: {len(per_stock)} tickers   Window: {earliest} -> {latest}")

    # Benchmark
    print("Computing NIFTY 50 equal-weight benchmark ...")
    nifty_index = _load_nifty_index(per_stock)
    bench_cagr = _benchmark_cagr(nifty_index)
    bench_final = float(nifty_index.iloc[-1])
    print(f"Benchmark: CAGR={bench_cagr:.2f}%  Final value of Rs.1L = Rs.{bench_final:,.0f}")

    # Save benchmark series
    nifty_index.to_csv(RESULTS_DIR / "benchmark_nifty50.csv", header=["Value"])

    # Grid sweep
    rows: list[dict] = []
    best_run: PortfolioRunV2 | None = None
    best_cagr = -999.0
    n = len(X_RANGE) * len(Y_RANGE)
    i = 0
    all_runs: dict[tuple[int, int], PortfolioRunV2] = {}

    for x in X_RANGE:
        for y in Y_RANGE:
            i += 1
            run = run_portfolio_v2(per_stock, float(x), float(y))
            row = metrics_for_v2(run, last_close, bench_cagr)
            rows.append(row)
            all_runs[(x, y)] = run
            if row["CAGR_pct"] > best_cagr:
                best_cagr = row["CAGR_pct"]
                best_run = run
            print(
                f"[{i:3d}/{n}] X={x:2d}%  Y={y:2d}%  "
                f"CAGR={row['CAGR_pct']:6.2f}%  Alpha={row['Alpha_pct']:+6.2f}%  "
                f"Final=Rs.{row['FinalValue_INR']:>10,.0f}  Tax=Rs.{row['TotalTax_INR']:>8,.0f}",
                flush=True,
            )

    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "grid_metrics_v2.csv", index=False)

    # Pivot matrices
    cagr_mat = df.pivot(index="X_pct", columns="Y_pct", values="CAGR_pct").round(2)
    alpha_mat = df.pivot(index="X_pct", columns="Y_pct", values="Alpha_pct").round(2)
    xirr_mat = df.pivot(index="X_pct", columns="Y_pct", values="XIRR_pct").round(2)
    tax_mat = df.pivot(index="X_pct", columns="Y_pct", values="TotalTax_INR").round(0)
    final_mat = df.pivot(index="X_pct", columns="Y_pct", values="FinalValue_INR").round(0)

    cagr_mat.to_csv(RESULTS_DIR / "cagr_matrix_v2.csv")
    alpha_mat.to_csv(RESULTS_DIR / "alpha_matrix.csv")
    xirr_mat.to_csv(RESULTS_DIR / "xirr_matrix_v2.csv")

    # -----------------------------------------------------------------------
    # Heatmaps
    # -----------------------------------------------------------------------
    print("\nGenerating heatmaps ...")

    # Alpha heatmap
    fig, ax = plt.subplots(figsize=(10, 8))
    sns.heatmap(alpha_mat, annot=True, fmt=".1f", cmap="RdYlGn",
                center=0, cbar_kws={"label": "Alpha (%) vs NIFTY 50"},
                linewidths=0.4, linecolor="white", ax=ax)
    ax.set_title("Alpha (Strategy CAGR - NIFTY 50 CAGR) by (X% dip, Y% gain)")
    ax.set_xlabel("Y -- sell trigger: % gain from buy")
    ax.set_ylabel("X -- buy trigger: % drop from ATH")
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "alpha_heatmap.png", dpi=140)
    plt.close(fig)

    # CAGR heatmap
    fig, ax = plt.subplots(figsize=(10, 8))
    sns.heatmap(cagr_mat, annot=True, fmt=".1f", cmap="RdYlGn",
                cbar_kws={"label": "CAGR (%)"},
                linewidths=0.4, linecolor="white", ax=ax)
    ax.set_title("V2 Strategy CAGR (%) -- Finite Capital Rs.1L")
    ax.set_xlabel("Y -- sell trigger: % gain from buy")
    ax.set_ylabel("X -- buy trigger: % drop from ATH")
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "cagr_heatmap_v2.png", dpi=140)
    plt.close(fig)

    # Final value heatmap
    fig, ax = plt.subplots(figsize=(10, 8))
    sns.heatmap(final_mat, annot=True, fmt=",.0f", cmap="RdYlGn",
                cbar_kws={"label": "Final Portfolio Value (Rs.)"},
                linewidths=0.4, linecolor="white", ax=ax)
    ax.set_title("Final Portfolio Value (Rs.) starting from Rs.1,00,000")
    ax.set_xlabel("Y -- sell trigger: % gain from buy")
    ax.set_ylabel("X -- buy trigger: % drop from ATH")
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "final_value_heatmap.png", dpi=140)
    plt.close(fig)

    # -----------------------------------------------------------------------
    # 3-year rolling returns — best combo (V2) + V1 best + benchmark
    # -----------------------------------------------------------------------
    print("Computing 3-year rolling returns ...")
    best_x, best_y = int(df.loc[df["CAGR_pct"].idxmax(), "X_pct"]), \
                      int(df.loc[df["CAGR_pct"].idxmax(), "Y_pct"])

    # V2 rolling
    best_eq_v2 = all_runs[(best_x, best_y)].daily_equity
    roll_v2 = rolling_returns(best_eq_v2, 3)

    # V1 rolling (original infinite-money, normalised to 1L start)
    v1_eq = _v1_equity_series(per_stock, float(best_x), float(best_y))
    roll_v1 = rolling_returns(v1_eq, 3)

    # Benchmark rolling
    roll_bench = rolling_returns(nifty_index, 3)

    # Plot
    fig, ax = plt.subplots(figsize=(14, 6))
    if not roll_v2.empty:
        ax.plot(roll_v2.index, roll_v2.values, label=f"V2 Finite-Cap (X={best_x},Y={best_y})",
                linewidth=1.2)
    if not roll_v1.empty:
        ax.plot(roll_v1.index, roll_v1.values, label=f"V1 Infinite-Cap (X={best_x},Y={best_y})",
                linewidth=1.2, linestyle="--")
    if not roll_bench.empty:
        ax.plot(roll_bench.index, roll_bench.values, label="NIFTY 50 Benchmark",
                linewidth=1.2, linestyle=":")
    ax.axhline(0, color="grey", linewidth=0.5)
    ax.set_title("3-Year Rolling Annualised Returns (%)")
    ax.set_ylabel("Return (%)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "rolling_3yr_returns.png", dpi=140)
    plt.close(fig)

    # Save rolling data
    roll_df = pd.DataFrame({
        f"V2_X{best_x}_Y{best_y}": roll_v2,
        f"V1_X{best_x}_Y{best_y}": roll_v1,
        "NIFTY50_Benchmark": roll_bench,
    })
    roll_df.to_csv(RESULTS_DIR / "rolling_3yr_returns.csv")

    # -----------------------------------------------------------------------
    # Yearly breakdown for best combo
    # -----------------------------------------------------------------------
    print("Writing yearly breakdown ...")
    if best_run and best_run.yearly_snapshots:
        yr_rows = []
        for s in best_run.yearly_snapshots:
            yr_rows.append({
                "Year": s.year,
                "Cumulative_Invested": s.amount_invested,
                "Cash_in_Hand": s.cash_in_hand,
                "Asset_Value": s.asset_value,
                "Total_Value": s.total_value,
                "Annual_Realised_PnL": s.realised_pnl,
                "Annual_STCG_Tax_20pct": s.stcg_tax,
                "Annual_LTCG_Tax_12.5pct": s.ltcg_tax,
                "Annual_Total_Tax": s.total_tax,
                "Cumulative_STCG_Tax": s.cumulative_stcg_tax,
                "Cumulative_LTCG_Tax": s.cumulative_ltcg_tax,
                "Cumulative_Tax": s.cumulative_tax,
                "Net_After_Tax": s.net_after_tax,
            })
        yr_df = pd.DataFrame(yr_rows)
        best_metric_tax = float(df.loc[
            (df["X_pct"] == float(best_x)) & (df["Y_pct"] == float(best_y)),
            "TotalTax_INR",
        ].iloc[0])
        annual_tax_sum = float(yr_df["Annual_Total_Tax"].sum())
        if not np.isclose(best_metric_tax, annual_tax_sum, rtol=0, atol=0.01):
            raise AssertionError(
                "Best-combo TotalTax_INR does not equal the sum of annual tax rows"
            )
        yr_df.to_csv(RESULTS_DIR / "yearly_breakdown.csv", index=False)

    # -----------------------------------------------------------------------
    # Summary markdown
    # -----------------------------------------------------------------------
    print("Writing summary ...")
    top5 = df.sort_values("CAGR_pct", ascending=False).head(5)
    bot5 = df.sort_values("CAGR_pct", ascending=True).head(5)

    md = []
    md.append("# V2: Finite-Capital ATH-Dip Strategy -- Grid Backtest Results\n")
    md.append(f"_Generated: {date.today().isoformat()}_\n")
    md.append(f"**Starting Capital:** Rs.{INITIAL_CAPITAL:,.0f}  ")
    md.append(f"**Trade Size:** Rs.{TRADE_NOTIONAL:,.0f} per buy  ")
    md.append(f"**Universe:** {len(per_stock)} tickers (current NIFTY 50)  ")
    md.append(f"**Window:** {earliest} -> {latest}  ")
    md.append(f"**Grid:** X = {X_RANGE[0]}-{X_RANGE[-1]}%, Y = {Y_RANGE[0]}-{Y_RANGE[-1]}%\n")

    md.append("## Key Changes from V1\n")
    md.append("1. **Finite capital**: Start with Rs.1,00,000 (not infinite money).\n")
    md.append("2. **Rs.20,000 per lot** (not Rs.10,000).\n")
    md.append("3. **Profit recycling**: Sale proceeds (principal + gain) recycled as available cash.\n")
    md.append("4. **Taxation**: Annual realised-gain taxes: STCG @ 20% (held < 1 year), LTCG @ 12.5% (held >= 1 year).\n")

    md.append(f"\n## Benchmark: NIFTY 50 Equal-Weight Index\n")
    md.append(f"- Rs.1,00,000 lump-sum at start\n")
    md.append(f"- **CAGR: {bench_cagr:.2f}%**\n")
    md.append(f"- **Final Value: Rs.{bench_final:,.0f}**\n")

    md.append("\n## Alpha Matrix (Strategy CAGR - Benchmark CAGR)\n")
    md.append("```")
    md.append(alpha_mat.to_string())
    md.append("```\n")

    md.append("\n## CAGR Matrix\n")
    md.append("```")
    md.append(cagr_mat.to_string())
    md.append("```\n")

    md.append("\n## XIRR Matrix\n")
    md.append("```")
    md.append(xirr_mat.to_string())
    md.append("```\n")

    md.append("\n## Top 5 (X, Y) by CAGR\n")
    md.append(top5.to_markdown(index=False, floatfmt=".2f"))
    md.append("")

    md.append("\n\n## Worst 5 (X, Y) by CAGR\n")
    md.append(bot5.to_markdown(index=False, floatfmt=".2f"))
    md.append("")

    # Yearly breakdown in MD
    if best_run and best_run.yearly_snapshots:
        md.append(f"\n\n## Yearly Breakdown (Best combo: X={best_x}%, Y={best_y}%)\n")
        md.append(f"Starting capital: Rs.{INITIAL_CAPITAL:,.0f}\n")
        md.append("| Year | Cum. Invested | Cash in Hand | Asset Value | Total Value | Annual Realised P&L | Annual STCG Tax | Annual LTCG Tax | Cum. Tax | Net After Tax |")
        md.append("|------|---------------|-------------|-------------|-------------|---------------------|-----------------|-----------------|----------|---------------|")
        for s in best_run.yearly_snapshots:
            md.append(
                f"| {s.year} | {s.amount_invested:>13,.0f} | {s.cash_in_hand:>11,.0f} | "
                f"{s.asset_value:>11,.0f} | {s.total_value:>11,.0f} | {s.realised_pnl:>11,.0f} | "
                f"{s.stcg_tax:>15,.0f} | {s.ltcg_tax:>15,.0f} | {s.cumulative_tax:>8,.0f} | "
                f"{s.net_after_tax:>13,.0f} |"
            )
        md.append("")

    # Tax summary across grid
    md.append("\n\n## Taxation Summary (across full grid)\n")
    tax_summary = df[["STCG_Tax_INR", "LTCG_Tax_INR", "TotalTax_INR", "NetAfterTax_INR"]].describe().round(0)
    md.append("```")
    md.append(tax_summary.to_string())
    md.append("```\n")

    md.append("\n## Files\n")
    md.append("- `grid_metrics_v2.csv` -- full per-combo metrics\n")
    md.append("- `alpha_matrix.csv` / `cagr_matrix_v2.csv` / `xirr_matrix_v2.csv` -- pivoted matrices\n")
    md.append("- `alpha_heatmap.png` / `cagr_heatmap_v2.png` / `final_value_heatmap.png` -- heatmaps\n")
    md.append("- `rolling_3yr_returns.png` / `rolling_3yr_returns.csv` -- 3-year rolling returns\n")
    md.append("- `yearly_breakdown.csv` -- year-by-year for best combo\n")
    md.append("- `benchmark_nifty50.csv` -- NIFTY 50 equal-weight index series\n")

    (RESULTS_DIR / "summary_v2.md").write_text("\n".join(md), encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"All outputs written to {RESULTS_DIR}/")
    print(f"Best combo: X={best_x}%, Y={best_y}%  CAGR={best_cagr:.2f}%  "
          f"Alpha={best_cagr - bench_cagr:+.2f}%")
    print("=" * 70)


if __name__ == "__main__":
    main()
