"""
ATH-dip / fixed-gain-exit strategy backtest.

Strategy (per the brief):
  1. Track the all-time high (ATH) for each stock dynamically (running max
     over close prices observed so far in the window).
  2. BUY Rs.10,000 worth of the stock whenever its close falls X% below ATH.
  3. SELL the entire position when its close rises Y% from the buy price.
  4. After a sell, the stock is eligible for a fresh buy again.
  5. Multiple stocks held simultaneously; no cap on positions.
  6. Ignore costs / slippage / liquidity.
  7. Use adjusted closes (yfinance auto_adjust=True).

Per-position cooldown: NONE. The next buy can fire on the very next day a
fresh dip from a NEW ATH is observed (i.e. after a sell, ATH continues to
update; we re-buy when close <= ATH * (1 - X/100)).

Edge cases:
  * If a single close is BOTH a fresh ATH AND >= buy*(1+Y) (impossible since
    buy was below ATH), we treat the daily close as the trade price.
  * Fractional shares allowed (continuous model). Trade size locked at
    Rs.10,000 notional at the close on the buy day.
  * Open positions at end of window are marked-to-market on the final close
    and counted as a synthetic terminal inflow for XIRR / total-return.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

import pandas as pd

TRADE_NOTIONAL = 10_000.0  # rupees per buy


@dataclass
class Trade:
    ticker: str
    buy_date: date
    buy_price: float
    sell_date: date | None
    sell_price: float | None
    shares: float

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


@dataclass
class StockResult:
    ticker: str
    trades: list[Trade] = field(default_factory=list)
    open_position: Trade | None = None
    last_close: float | None = None
    last_date: date | None = None


def _to_pydate(ts) -> date:
    if isinstance(ts, pd.Timestamp):
        return ts.date()
    if isinstance(ts, date):
        return ts
    return pd.Timestamp(ts).date()


def backtest_one(ticker: str, prices: pd.DataFrame, x_pct: float, y_pct: float) -> StockResult:
    """Run the strategy on a single stock's daily close series."""
    closes = prices["Close"].dropna()
    res = StockResult(ticker=ticker)
    if closes.empty:
        return res

    ath = float("-inf")
    open_trade: Trade | None = None
    buy_threshold_factor = 1.0 - x_pct / 100.0
    sell_threshold_factor = 1.0 + y_pct / 100.0

    for ts, raw_px in closes.items():
        px = float(raw_px)
        d = _to_pydate(ts)

        # 1) update ATH on this close
        if px > ath:
            ath = px

        # 2) if holding, check sell first (a single bar can't both buy and sell
        #    because a buy fires only when price <= ATH*(1-X) which is below
        #    ATH, while sell needs price >= buy*(1+Y); these don't co-occur on
        #    the same bar for the SAME open trade).
        if open_trade is not None:
            if px >= open_trade.buy_price * sell_threshold_factor:
                open_trade.sell_date = d
                open_trade.sell_price = px
                res.trades.append(open_trade)
                open_trade = None
                # after closing, fall through so a fresh buy can fire same day
                # if the (now-flat) state happens to satisfy the dip rule
        # 3) if flat, check buy
        if open_trade is None and ath > 0:
            if px <= ath * buy_threshold_factor:
                shares = TRADE_NOTIONAL / px
                open_trade = Trade(
                    ticker=ticker,
                    buy_date=d,
                    buy_price=px,
                    sell_date=None,
                    sell_price=None,
                    shares=shares,
                )

    res.open_position = open_trade
    res.last_close = float(closes.iloc[-1])
    res.last_date = _to_pydate(closes.index[-1])
    return res


# ---------------------------------------------------------------------------
# Portfolio aggregation across the full universe
# ---------------------------------------------------------------------------


@dataclass
class CashFlow:
    d: date
    amount: float  # negative = buy, positive = sell or terminal MTM


@dataclass
class PortfolioRun:
    x_pct: float
    y_pct: float
    cashflows: list[CashFlow]
    closed_trades: list[Trade]
    open_trades: list[Trade]  # at end-of-window
    terminal_mtm: float  # value of open positions on last bar
    daily_equity: pd.Series  # cumulative net P&L curve (NOT cash-balance)

    @property
    def num_trades(self) -> int:
        return len(self.closed_trades)

    @property
    def gross_invested(self) -> float:
        return -sum(cf.amount for cf in self.cashflows if cf.amount < 0)

    @property
    def gross_returned(self) -> float:
        return sum(cf.amount for cf in self.cashflows if cf.amount > 0)

    @property
    def total_pnl(self) -> float:
        return self.gross_returned - self.gross_invested


def _build_daily_equity(per_stock: dict[str, pd.DataFrame],
                        results: dict[str, StockResult]) -> pd.Series:
    """
    Build a daily total-equity curve = sum across stocks of (realized cash
    accumulated to date) + (mark-to-market of currently open position).

    This drives the max-drawdown calc.
    """
    # Build per-stock daily contribution
    union_idx = sorted({d for df in per_stock.values() for d in df.index})
    if not union_idx:
        return pd.Series(dtype=float)
    contrib = pd.DataFrame(0.0, index=union_idx, columns=list(results.keys()))

    for tkr, sr in results.items():
        closes = per_stock[tkr]["Close"].dropna()
        if closes.empty:
            continue
        # Sort trades by buy_date for chronological replay
        trades_sorted = sorted(sr.trades, key=lambda t: t.buy_date)
        # Running realized cash for this ticker across time:
        # after each closed trade, realized += (sell-buy)*shares; before close
        # we hold an unrealised position.
        # Build event timeline:
        events = []  # (date, kind, trade, shares_or_pnl)
        for tr in trades_sorted:
            events.append((tr.buy_date, "buy", tr))
            events.append((tr.sell_date, "sell", tr))
        if sr.open_position is not None:
            events.append((sr.open_position.buy_date, "buy", sr.open_position))

        events.sort(key=lambda e: (e[0], 0 if e[1] == "buy" else 1))

        realized = 0.0
        open_tr: Trade | None = None
        ev_iter = iter(events)
        next_ev = next(ev_iter, None)

        # Iterate per trading day in this stock's index, but write into the
        # union index. We reindex at the end.
        per_day = pd.Series(0.0, index=closes.index, dtype=float)
        for ts, raw_px in closes.items():
            d = _to_pydate(ts)
            px = float(raw_px)
            # apply all events with date <= d
            while next_ev is not None and next_ev[0] <= d:
                _, kind, tr = next_ev
                if kind == "buy":
                    open_tr = tr
                else:  # sell
                    realized += (tr.sell_price - tr.buy_price) * tr.shares
                    open_tr = None
                next_ev = next(ev_iter, None)
            mtm = 0.0
            if open_tr is not None:
                mtm = (px - open_tr.buy_price) * open_tr.shares
            per_day.loc[ts] = realized + mtm
        contrib[tkr] = per_day.reindex(union_idx).ffill().fillna(0.0)

    eq = contrib.sum(axis=1)
    eq.index = pd.to_datetime(eq.index)
    return eq


def run_portfolio(
    per_stock: dict[str, pd.DataFrame],
    x_pct: float,
    y_pct: float,
) -> PortfolioRun:
    results: dict[str, StockResult] = {}
    for tkr, df in per_stock.items():
        results[tkr] = backtest_one(tkr, df, x_pct, y_pct)

    cashflows: list[CashFlow] = []
    closed: list[Trade] = []
    opens: list[Trade] = []
    terminal_mtm = 0.0
    last_dates: list[date] = []

    for sr in results.values():
        for tr in sr.trades:
            cashflows.append(CashFlow(tr.buy_date, -TRADE_NOTIONAL))
            cashflows.append(CashFlow(tr.sell_date, tr.sell_price * tr.shares))
            closed.append(tr)
        if sr.open_position is not None:
            tr = sr.open_position
            cashflows.append(CashFlow(tr.buy_date, -TRADE_NOTIONAL))
            opens.append(tr)
        if sr.last_date is not None:
            last_dates.append(sr.last_date)

    # Terminal mark-to-market for open positions = added on portfolio's last
    # observed date.
    if last_dates:
        portfolio_end = max(last_dates)
    else:
        portfolio_end = date.today()
    for sr in results.values():
        if sr.open_position is not None and sr.last_close is not None:
            mv = sr.open_position.shares * sr.last_close
            terminal_mtm += mv
            cashflows.append(CashFlow(portfolio_end, mv))

    cashflows.sort(key=lambda cf: cf.d)
    eq = _build_daily_equity(per_stock, results)

    return PortfolioRun(
        x_pct=x_pct,
        y_pct=y_pct,
        cashflows=cashflows,
        closed_trades=closed,
        open_trades=opens,
        terminal_mtm=terminal_mtm,
        daily_equity=eq,
    )


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------


def max_capital_deployed(cashflows: Iterable[CashFlow]) -> float:
    """Maximum simultaneous outstanding outflow (running net negative)."""
    running = 0.0
    peak = 0.0
    for cf in sorted(cashflows, key=lambda c: c.d):
        running -= cf.amount  # negate so buys add, sells subtract
        if running > peak:
            peak = running
    return peak


def max_drawdown(equity: pd.Series) -> float:
    """Max drawdown of a P&L curve (returned as a NEGATIVE rupee figure)."""
    if equity.empty:
        return 0.0
    running_peak = equity.cummax()
    dd = equity - running_peak
    return float(dd.min())


def avg_holding_days(closed: list[Trade]) -> float:
    if not closed:
        return 0.0
    return sum(t.holding_days for t in closed) / len(closed)


def win_rate(closed: list[Trade], opens: list[Trade], last_close_by_ticker: dict[str, float]) -> float:
    """% of trades (closed + open marked-to-last-close) with positive P&L."""
    wins = 0
    total = 0
    for t in closed:
        total += 1
        if (t.sell_price - t.buy_price) > 0:
            wins += 1
    for t in opens:
        last = last_close_by_ticker.get(t.ticker)
        if last is None:
            continue
        total += 1
        if (last - t.buy_price) > 0:
            wins += 1
    return wins / total if total else 0.0
