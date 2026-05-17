"""Lightweight validation for V2 tax accounting."""

from __future__ import annotations

from math import isclose

import pandas as pd

from backtest_v2 import run_portfolio_v2
from run_grid_v2 import _benchmark_cagr, _load_nifty_index, fetch_all, metrics_for_v2


def _assert_close(name: str, actual: float, expected: float, *, abs_tol: float = 0.01) -> None:
    if not isclose(actual, expected, rel_tol=0.0, abs_tol=abs_tol):
        raise AssertionError(f"{name}: expected {expected:.2f}, got {actual:.2f}")


def validate_deterministic_tax_rows() -> None:
    prices = pd.DataFrame(
        {"Close": [100.0, 90.0, 100.0, 80.0, 90.0]},
        index=pd.to_datetime([
            "2016-01-01",
            "2016-01-02",
            "2016-01-10",
            "2017-01-01",
            "2018-01-02",
        ]),
    )
    run = run_portfolio_v2({"TEST.NS": prices}, x_pct=10.0, y_pct=10.0)

    stcg_by_year = {snapshot.year: snapshot.stcg_tax for snapshot in run.yearly_snapshots}
    ltcg_by_year = {snapshot.year: snapshot.ltcg_tax for snapshot in run.yearly_snapshots}

    _assert_close("2016 STCG tax", stcg_by_year[2016], 444.44)
    _assert_close("2017 STCG tax", stcg_by_year[2017], 0.0)
    _assert_close("2018 STCG tax", stcg_by_year[2018], 0.0)
    _assert_close("2016 LTCG tax", ltcg_by_year[2016], 0.0)
    _assert_close("2017 LTCG tax", ltcg_by_year[2017], 0.0)
    _assert_close("2018 LTCG tax", ltcg_by_year[2018], 312.5)

    annual_tax = sum(snapshot.total_tax for snapshot in run.yearly_snapshots)
    final_snapshot = run.yearly_snapshots[-1]
    _assert_close("sum of annual tax rows", annual_tax, 756.94)
    _assert_close("final cumulative tax", final_snapshot.cumulative_tax, annual_tax)
    _assert_close("final net after tax", final_snapshot.net_after_tax, run.final_value - annual_tax)


def validate_cached_best_combo() -> None:
    per_stock = fetch_all(refresh=False)
    last_close = {
        ticker: float(frame["Close"].dropna().iloc[-1])
        for ticker, frame in per_stock.items()
    }
    benchmark_cagr = _benchmark_cagr(_load_nifty_index(per_stock))

    run = run_portfolio_v2(per_stock, x_pct=10.0, y_pct=20.0)
    metrics = metrics_for_v2(run, last_close, benchmark_cagr)
    annual_tax = sum(snapshot.total_tax for snapshot in run.yearly_snapshots)

    _assert_close("X=10/Y=20 TotalTax_INR", metrics["TotalTax_INR"], annual_tax)
    _assert_close("X=10/Y=20 NetAfterTax_INR", metrics["NetAfterTax_INR"], run.final_value - annual_tax)


def main() -> None:
    validate_deterministic_tax_rows()
    validate_cached_best_combo()
    print("V2 tax validation passed.")


if __name__ == "__main__":
    main()