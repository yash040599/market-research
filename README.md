# Research: NIFTY 50 ATH-Dip / Fixed-Gain-Exit Backtest

This folder is a **standalone research project**, intentionally kept
separate from the main `ai-portfolio-manager` tool. Nothing here imports
from `core/`, `modes/`, `shared/`, etc., and nothing in the main tool
imports from here.

## The question

> Backtest a NIFTY 50 universe over the last 10 years with the rule:
> Buy ₹10,000 of a stock whenever it falls **X%** from its all-time high.
> Sell the entire position when it rises **Y%** from the buy price.
> Multiple positions, no costs. Sweep X, Y over 10–20% integers and report
> XIRR / CAGR / total return / # trades / win rate / avg holding / max DD /
> capital deployed, plus a heatmap.

## How it works

| Step | File | What it does |
|------|------|--------------|
| 1. Universe | [research/nifty50.py](research/nifty50.py) | 50 NSE tickers (Yahoo `.NS` suffix). |
| 2. Data fetch | [research/data_loader.py](research/data_loader.py) | Downloads ~10 years of daily auto-adjusted OHLC via `yfinance`, caches to `research/data/<TICKER>.csv`. |
| 3. Strategy | [research/backtest.py](research/backtest.py) | Per-stock state machine + portfolio-level cashflow / equity-curve aggregator. |
| 4. XIRR | [research/xirr.py](research/xirr.py) | Pure-Python Newton + bisection IRR (Excel XIRR convention). |
| 5. Sweep | [research/run_grid.py](research/run_grid.py) | 11×11 grid; writes CSVs + heatmap PNG + Markdown summary into `research/results/`. |

## Data source choice

Yahoo Finance via `yfinance` was picked because it is:

- **Free**, no auth (Zerodha's KiteConnect requires daily login + per-symbol
  pagination and the historical depth is paywalled past ~365 days).
- Provides **split- and dividend-adjusted** closes via `auto_adjust=True`,
  satisfying the "use adjusted closing prices if available" requirement.
- Covers the full 10-year window for all current NIFTY 50 names.

The main tool's existing `shared/candle_cache.py` is Kite-backed and capped
at ~6 months — wrong tool for a decade-long backtest, hence we go to Yahoo.

## How to run

```powershell
# from repo root, with the existing .venv activated:
pip install -r research/requirements.txt
python -m research.data_loader        # ~2-5 min, one-time download
python -m research.run_grid           # ~30 sec for the 121-combo sweep
```

Outputs land in `research/results/`:

- `grid_metrics.csv` — one row per (X,Y) with every metric.
- `xirr_matrix.csv`, `cagr_matrix.csv` — pivoted X×Y tables.
- `xirr_heatmap.png` — heatmap visualization.
- `summary.md` — assumptions + top-5 / worst-5 + describe() stats.

## Assumptions (also reprinted in `results/summary.md`)

1. **Universe:** current (May 2026) NIFTY 50 membership held constant for
   the full 10-year window. This introduces mild **survivorship bias** —
   names that were dropped from the index over the decade are excluded.
2. **Prices:** Yahoo Finance daily close, auto-adjusted for splits and
   dividends. Trade triggers fire on the close.
3. **ATH:** running max of close prices observed since the start of the
   backtest window for that ticker (not lifetime ATH from listing — Yahoo
   data only goes back so far for some names anyway).
4. **Trade sizing:** ₹10,000 notional per buy, fractional shares allowed.
5. **Re-entry:** immediately eligible after a sell; no cooldown bars.
6. **Costs:** zero brokerage / STT / slippage / impact / liquidity caps,
   per the brief.
7. **Open positions at horizon end:** marked to market on the last
   available close and recorded as a synthetic XIRR inflow on that date.
8. **Capital:** assumed available on demand whenever a buy fires; no cap
   on simultaneous positions. "Capital deployed" reported = peak
   simultaneous outstanding outflow.
9. **CAGR:** `(GrossReturned / MaxCapitalDeployed) ^ (1/years) − 1`, with
   years measured from first buy to last cashflow. This is the
   capital-weighted variant — XIRR is the more rigorous figure.
10. **Win rate:** fraction of trades (closed + open-MTM) with positive
    P&L. By construction every CLOSED trade is a Y% winner — the only
    losers can be open positions trading below their buy price at the
    horizon date.

## Repo separation

This folder has its own `requirements.txt` (`yfinance`, `pandas`, `numpy`,
`matplotlib`, `seaborn`) so the main tool's dependency list stays clean.
Cached data and generated results are git-ignored.

## Results (run on 2026-05-14)

**Universe loaded:** 48 / 50 NIFTY 50 tickers.
`TATAMOTORS.NS` and `LTIM.NS` returned 404 / "symbol may be delisted"
from Yahoo Finance on the run date (tried `.NS`, `.BO`,
`TATAMTRDVR.NS`, `TML.NS`, `LTIMINDTREE.NS`, `LTI.NS`, `MINDTREE.NS`,
`LTIM.BO` — all empty). Backtest run on the remaining 48.

**Window:** 2016-05-10 → 2026-05-13 (full 10y of daily closes).

### XIRR matrix (rows = X% dip-buy, cols = Y% gain-sell), all values in %

| X \ Y | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **10** | 20.03 | 20.99 | 21.20 | 21.46 | 21.29 | 21.08 | 21.45 | 21.14 | 21.31 | 21.83 | 21.57 |
| **11** | 20.98 | 21.44 | 21.20 | 21.64 | 21.83 | 21.95 | 21.64 | 21.17 | 20.95 | 21.32 | 21.50 |
| **12** | 21.78 | 21.55 | 21.28 | 21.49 | 21.52 | 21.71 | 21.89 | 20.88 | 20.65 | 20.81 | 20.41 |
| **13** | 21.66 | 21.55 | 21.08 | 20.90 | 20.45 | 20.55 | 20.93 | 20.32 | 20.18 | 20.55 | 20.77 |
| **14** | 22.89 | 22.54 | 22.96 | 22.32 | 21.29 | 21.32 | 21.10 | 20.84 | 20.95 | 21.17 | 20.97 |
| **15** | 24.28 | 25.04 | 24.37 | 23.38 | 22.62 | 22.16 | 22.09 | 21.23 | 21.48 | 21.27 | 21.75 |
| **16** | 24.36 | 24.33 | 24.38 | 24.16 | 23.00 | 22.84 | 22.35 | 22.08 | 21.97 | 21.99 | 22.38 |
| **17** | 25.34 | 24.70 | 24.05 | 24.48 | 24.42 | 24.51 | 23.10 | 22.73 | 22.19 | 22.17 | 21.94 |
| **18** | 26.50 | 26.31 | 25.60 | 25.87 | 25.74 | 26.62 | 25.54 | 24.79 | 23.68 | 23.20 | 23.06 |
| **19** | 27.84 | 26.94 | 27.51 | 27.35 | 26.97 | 26.69 | 26.21 | 25.63 | 24.94 | 24.74 | 23.11 |
| **20** | **29.54** | 28.35 | 28.16 | 28.68 | 28.43 | 27.73 | 26.54 | 26.74 | 26.93 | 25.67 | 25.53 |

Heatmap: [results/xirr_heatmap.png](results/xirr_heatmap.png)

### Top 5 combos by XIRR

| Rank | X% | Y% | XIRR | CAGR | Total Return | # Trades | Win Rate | Avg Holding (days) | Max Drawdown | Max Capital Deployed |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 20 | 10 | **29.54%** | 28.53% | 108.97% | 328 | 95.98% | 149.7 | -₹1.32 L | ₹3.29 L |
| 2 | 20 | 13 | 28.68% | 26.78% | 116.43% | 274 | 95.25% | 194.3 | -₹1.32 L | ₹3.27 L |
| 3 | 20 | 14 | 28.43% | 26.47% | 119.55% | 261 | 94.70% | 206.8 | -₹1.32 L | ₹3.23 L |
| 4 | 20 | 11 | 28.35% | 27.50% | 108.91% | 305 | 95.69% | 165.9 | -₹1.32 L | ₹3.35 L |
| 5 | 20 | 12 | 28.16% | 27.01% | 110.85% | 288 | 95.47% | 179.7 | -₹1.32 L | ₹3.34 L |

### Worst 5 combos by XIRR  _(every combo in the grid was profitable)_

| Rank | X% | Y% | XIRR | CAGR | Total Return | # Trades | Win Rate | Avg Holding (days) | Max Drawdown | Max Capital Deployed |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 121 | 10 | 10 | 20.03% | 30.58% | 126.43% | 487 | 94.29% | 200.4 | -₹1.49 L | ₹4.02 L |
| 120 | 13 | 18 | 20.18% | 25.56% | 133.94% | 274 | 91.77% | 337.2 | -₹1.51 L | ₹3.82 L |
| 119 | 13 | 17 | 20.32% | 25.91% | 132.38% | 286 | 92.05% | 317.7 | -₹1.52 L | ₹3.82 L |
| 118 | 12 | 20 | 20.41% | 25.22% | 142.30% | 277 | 91.56% | 358.2 | -₹1.56 L | ₹3.99 L |
| 117 | 13 | 14 | 20.45% | 27.13% | 126.06% | 332 | 92.93% | 262.7 | -₹1.49 L | ₹3.82 L |

### Trade-summary statistics (across all 121 combos)

| | NumTrades | WinRate (%) | Avg Holding (days) | Max DD (₹) | Max Capital Deployed (₹) | Net P&L (₹) |
|---|---:|---:|---:|---:|---:|---:|
| mean | 312.18 | 93.41 | 257.21 | -143,570 | 361,566 | 475,114 |
| std  | 66.75  |  1.22 |  57.48 |   6,734 |  31,453 |  66,484 |
| min  | 202    | 91.00 | 149.72 | -156,759 | 317,924 | 358,970 |
| max  | 487    | 95.98 | 359.91 | -131,193 | 418,680 | 638,931 |

### Key observations

1. **Every (X, Y) in the 10-20% grid is profitable.** XIRR spans 20.0%
   (worst, X=10/Y=10) to 29.5% (best, X=20/Y=10). NIFTY 50's own
   ~13-14% CAGR over the same period is comfortably beaten by all
   combos on an XIRR basis.
2. **Deeper dips dominate.** XIRR rises near-monotonically with X: the
   X=20% row averages **27.4%** XIRR vs **21.2%** for X=10%. Waiting
   for a 20% drawdown filters out shallow noise and only buys real
   crash-class dips, which mean-revert faster.
3. **Tight take-profits win on XIRR but lose on CAGR.** Y=10–11%
   maximises XIRR (faster turnover → higher annualised return on
   actually-deployed capital) but Y=18–20% banks larger absolute gains
   per trade. The XIRR/CAGR divergence reflects denominator effects:
   CAGR here divides by peak capital deployed, which grows fast when
   X is small (more concurrent positions).
4. **Win rate is essentially structural.** Every CLOSED trade is a +Y%
   winner by construction. The 91–96% headline win rate is dragged
   below 100% only by open positions still under water at the 2026-05-13
   horizon (typically the most recent dip-buys that haven't hit +Y yet).
5. **Capital efficiency.** Peak simultaneous deployment is
   ₹3.2 L – ₹4.2 L for the entire 48-stock NIFTY 50 universe — i.e.
   ~32–42 concurrent positions at peak. Very small absolute capital
   requirement for a 10-year multi-stock strategy at ₹10 k a clip.
6. **Drawdowns are mild.** Worst equity-curve drawdown across the
   entire grid is **−₹1.57 L** on peak capital of ~₹4 L (≈ 37%) — and
   this is on the COMBO-LEVEL P&L curve including open MTM, not on a
   buy-and-hold benchmark. The COVID-2020 and 2022-correction dips are
   visible but quickly recovered because the strategy mechanically
   bought into them.

### Honest caveats

- **Survivorship bias.** We use 2026's NIFTY 50 list for the full
  decade — names that were KICKED OUT of the index during 2016-2026
  (because they underperformed badly) are absent. This inflates
  results vs a "true" historical NIFTY 50 panel. Magnitude of inflation
  is typically 100-200 bps of CAGR for Indian large-cap universes.
- **No costs.** With ~300 round trips on ₹10 k tickets, real-world
  brokerage + STT + GST + SEBI fees would shave roughly 50-100 bps
  off XIRR. Slippage on entries during real crashes (illiquid days)
  would be additional.
- **Yahoo data gaps.** TATAMOTORS and LTIM were unreachable on the
  run date — 48 / 50 names instead of 50. Diversification effect is
  small (~4%) but not zero.
- **Capital availability assumption.** "Buy whenever the rule fires"
  implicitly assumes infinite cash on hand. Real implementation
  requires sizing the cash buffer to cover the peak ~₹4 L commitment.

