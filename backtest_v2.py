"""
Finite-capital ATH-dip / fixed-gain-exit strategy backtest (V2).

Changes vs V1 (backtest.py):
  - Start with Rs.1,00,000 cash (not infinite).
  - Invest Rs.20,000 per lot (not Rs.10,000).
  - On profit-booking, the FULL sale proceeds (principal + profit) become
    available cash for the next buy.
  - At most one open position per ticker at a time (same as V1).
  - If cash < 20,000 when a buy signal fires, the signal is skipped.

The portfolio-level simulation must run chronologically across ALL tickers
on a unified calendar so that cash is shared correctly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd

INITIAL_CAPITAL = 100_000.0
TRADE_NOTIONAL = 20_000.0


@dataclass
class Trade:
    ticker: str
    buy_date: date
    buy_price: float
    sell_date: date | None
    sell_price: float | None
    shares: float
    invested: float  # actual rupees spent (always TRADE_NOTIONAL when bought)

    @property
    def holding_days(self) -> int | None:
        if self.sell_date is None:
            return None
        return (self.sell_date - self.buy_date).days

    @property
    def realised_pnl(self) -> float | None:
        if self.sell_price is None:
            return None
        return (self.sell_price - self.buy_price) * self.shares

    @property
    def sale_proceeds(self) -> float | None:
        if self.sell_price is None:
            return None
        return self.sell_price * self.shares


@dataclass
class YearlySnapshot:
    year: int
    amount_invested: float  # cumulative outflows (buys) up to year-end
    cash_in_hand: float
    asset_value: float  # MTM of open positions
    total_value: float  # cash + asset
    realised_pnl: float
    stcg_tax: float  # 20% on gains from trades held < 365 days
    ltcg_tax: float  # 12.5% on gains from trades held >= 365 days
    net_after_tax: float


@dataclass
class PortfolioRunV2:
    x_pct: float
    y_pct: float
    closed_trades: list[Trade]
    open_trades: list[Trade]
    terminal_mtm: float
    daily_equity: pd.Series  # total portfolio value (cash + MTM) per day
    daily_cash: pd.Series
    yearly_snapshots: list[YearlySnapshot]
    total_invested: float  # cumulative buy outflows
    total_returned: float  # cumulative sell inflows
    final_cash: float
    final_asset_value: float

    @property
    def num_trades(self) -> int:
        return len(self.closed_trades)

    @property
    def final_value(self) -> float:
        return self.final_cash + self.final_asset_value


def _to_pydate(ts) -> date:
    if isinstance(ts, pd.Timestamp):
        return ts.date()
    if isinstance(ts, date):
        return ts
    return pd.Timestamp(ts).date()


def run_portfolio_v2(
    per_stock: dict[str, pd.DataFrame],
    x_pct: float,
    y_pct: float,
) -> PortfolioRunV2:
    """Run finite-capital portfolio simulation across all tickers."""

    buy_factor = 1.0 - x_pct / 100.0
    sell_factor = 1.0 + y_pct / 100.0

    # Build unified daily calendar
    all_dates: set[date] = set()
    ticker_closes: dict[str, dict[date, float]] = {}
    for tkr, df in per_stock.items():
        closes = df["Close"].dropna()
        d_map: dict[date, float] = {}
        for ts, px in closes.items():
            d = _to_pydate(ts)
            d_map[d] = float(px)
            all_dates.add(d)
        ticker_closes[tkr] = d_map

    sorted_dates = sorted(all_dates)
    if not sorted_dates:
        return _empty_run(x_pct, y_pct)

    # State
    cash = INITIAL_CAPITAL
    open_positions: dict[str, Trade] = {}  # ticker -> open Trade
    ath: dict[str, float] = {t: float("-inf") for t in per_stock}
    closed_trades: list[Trade] = []
    total_invested = 0.0
    total_returned = 0.0

    # For yearly snapshots
    yearly_invested: float = 0.0  # cumulative
    yearly_realised: float = 0.0
    yearly_stcg: float = 0.0
    yearly_ltcg: float = 0.0
    snapshots: list[YearlySnapshot] = []
    current_year: int | None = None

    # Daily tracking
    daily_values: list[tuple[date, float, float]] = []  # (date, total_value, cash)

    for d in sorted_dates:
        # Check year rollover — snapshot at end of previous year
        if current_year is not None and d.year != current_year:
            _take_yearly_snapshot(
                snapshots, current_year, yearly_invested, cash,
                open_positions, ticker_closes, sorted_dates,
                yearly_realised, yearly_stcg, yearly_ltcg,
            )
        current_year = d.year

        # Process each ticker for this day
        # First pass: sells (to free up cash for buys on same day)
        for tkr in list(open_positions.keys()):
            px = ticker_closes[tkr].get(d)
            if px is None:
                continue
            # Update ATH
            if px > ath[tkr]:
                ath[tkr] = px
            trade = open_positions[tkr]
            if px >= trade.buy_price * sell_factor:
                trade.sell_date = d
                trade.sell_price = px
                proceeds = trade.sale_proceeds
                cash += proceeds
                total_returned += proceeds
                # Tax accounting
                holding = (d - trade.buy_date).days
                gain = proceeds - trade.invested
                if gain > 0:
                    if holding < 365:
                        yearly_stcg += gain * 0.20
                    else:
                        yearly_ltcg += gain * 0.125
                yearly_realised += gain
                closed_trades.append(trade)
                del open_positions[tkr]

        # Second pass: update ATH for tickers not yet updated (no open pos)
        for tkr in per_stock:
            if tkr in open_positions:
                continue  # ATH already updated above if price existed
            px = ticker_closes[tkr].get(d)
            if px is not None and px > ath[tkr]:
                ath[tkr] = px

        # Third pass: buys
        for tkr in per_stock:
            if tkr in open_positions:
                continue
            px = ticker_closes[tkr].get(d)
            if px is None:
                continue
            if ath[tkr] > 0 and px <= ath[tkr] * buy_factor:
                if cash >= TRADE_NOTIONAL:
                    shares = TRADE_NOTIONAL / px
                    trade = Trade(
                        ticker=tkr,
                        buy_date=d,
                        buy_price=px,
                        sell_date=None,
                        sell_price=None,
                        shares=shares,
                        invested=TRADE_NOTIONAL,
                    )
                    open_positions[tkr] = trade
                    cash -= TRADE_NOTIONAL
                    total_invested += TRADE_NOTIONAL
                    yearly_invested = total_invested

        # Daily portfolio value
        asset_val = 0.0
        for tkr, trade in open_positions.items():
            px = ticker_closes[tkr].get(d)
            if px is not None:
                asset_val += trade.shares * px
            else:
                # Use last known
                asset_val += trade.shares * trade.buy_price
        daily_values.append((d, cash + asset_val, cash))

    # Final yearly snapshot
    if current_year is not None:
        _take_yearly_snapshot(
            snapshots, current_year, yearly_invested, cash,
            open_positions, ticker_closes, sorted_dates,
            yearly_realised, yearly_stcg, yearly_ltcg,
        )

    # Build open trades list & terminal MTM
    open_list = list(open_positions.values())
    terminal_mtm = 0.0
    for tkr, trade in open_positions.items():
        last_px = _last_price(tkr, ticker_closes, sorted_dates)
        if last_px is not None:
            terminal_mtm += trade.shares * last_px

    # Build daily series
    if daily_values:
        idx = pd.DatetimeIndex([dv[0] for dv in daily_values])
        eq = pd.Series([dv[1] for dv in daily_values], index=idx, dtype=float)
        cash_s = pd.Series([dv[2] for dv in daily_values], index=idx, dtype=float)
    else:
        eq = pd.Series(dtype=float)
        cash_s = pd.Series(dtype=float)

    return PortfolioRunV2(
        x_pct=x_pct,
        y_pct=y_pct,
        closed_trades=closed_trades,
        open_trades=open_list,
        terminal_mtm=terminal_mtm,
        daily_equity=eq,
        daily_cash=cash_s,
        yearly_snapshots=snapshots,
        total_invested=total_invested,
        total_returned=total_returned,
        final_cash=cash,
        final_asset_value=terminal_mtm,
    )


def _last_price(tkr: str, ticker_closes: dict[str, dict[date, float]],
                sorted_dates: list[date]) -> float | None:
    tc = ticker_closes.get(tkr, {})
    for d in reversed(sorted_dates):
        if d in tc:
            return tc[d]
    return None


def _take_yearly_snapshot(
    snapshots: list[YearlySnapshot],
    year: int,
    cum_invested: float,
    cash: float,
    open_positions: dict[str, Trade],
    ticker_closes: dict[str, dict[date, float]],
    sorted_dates: list[date],
    realised_pnl: float,
    stcg_tax: float,
    ltcg_tax: float,
) -> None:
    asset_val = 0.0
    for tkr, trade in open_positions.items():
        # Find last price on or before year-end
        last_px = None
        for d in reversed(sorted_dates):
            if d.year > year:
                continue
            if d in ticker_closes.get(tkr, {}):
                last_px = ticker_closes[tkr][d]
                break
        if last_px is not None:
            asset_val += trade.shares * last_px
        else:
            asset_val += trade.shares * trade.buy_price

    total = cash + asset_val
    snapshots.append(YearlySnapshot(
        year=year,
        amount_invested=cum_invested,
        cash_in_hand=cash,
        asset_value=asset_val,
        total_value=total,
        realised_pnl=realised_pnl,
        stcg_tax=stcg_tax,
        ltcg_tax=ltcg_tax,
        net_after_tax=total - stcg_tax - ltcg_tax,
    ))


def _empty_run(x_pct: float, y_pct: float) -> PortfolioRunV2:
    return PortfolioRunV2(
        x_pct=x_pct, y_pct=y_pct,
        closed_trades=[], open_trades=[], terminal_mtm=0.0,
        daily_equity=pd.Series(dtype=float),
        daily_cash=pd.Series(dtype=float),
        yearly_snapshots=[], total_invested=0.0, total_returned=0.0,
        final_cash=INITIAL_CAPITAL, final_asset_value=0.0,
    )


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_peak = equity.cummax()
    dd = equity - running_peak
    return float(dd.min())


def avg_holding_days(closed: list[Trade]) -> float:
    if not closed:
        return 0.0
    return sum(t.holding_days for t in closed) / len(closed)


def win_rate(closed: list[Trade], opens: list[Trade],
             last_close_by_ticker: dict[str, float]) -> float:
    wins = total = 0
    for t in closed:
        total += 1
        if t.realised_pnl and t.realised_pnl > 0:
            wins += 1
    for t in opens:
        last = last_close_by_ticker.get(t.ticker)
        if last is None:
            continue
        total += 1
        if (last - t.buy_price) > 0:
            wins += 1
    return wins / total if total else 0.0


def rolling_returns(equity: pd.Series, window_years: int = 3) -> pd.Series:
    """Compute rolling annualised returns over a window of N years."""
    if equity.empty:
        return pd.Series(dtype=float)
    window_days = int(window_years * 365.25)
    # Resample to business-daily to avoid gaps
    eq = equity.asfreq("B", method="ffill")
    if len(eq) < window_days:
        return pd.Series(dtype=float)
    start_vals = eq.shift(window_days)
    roll_ret = (eq / start_vals) ** (1.0 / window_years) - 1.0
    return roll_ret.dropna() * 100.0  # as percentage
