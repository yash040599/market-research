"""
Pure-Python XIRR (annualised IRR for irregularly-spaced cashflows).

Implements the same convention as Excel's XIRR / numpy_financial.xirr:
    sum_i  cf_i / (1 + r) ** ((d_i - d_0) / 365.0)  ==  0
where d_0 is the earliest cashflow date.

We solve with Newton's method, fall back to bisection if Newton diverges.

Sign convention: outflows (buys) are NEGATIVE, inflows (sells / terminal
mark-to-market) are POSITIVE.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence


def _npv(rate: float, cashflows: Sequence[float], days: Sequence[float]) -> float:
    base = 1.0 + rate
    if base <= 0:
        return float("inf")
    total = 0.0
    for cf, d in zip(cashflows, days):
        total += cf / (base ** (d / 365.0))
    return total


def _dnpv(rate: float, cashflows: Sequence[float], days: Sequence[float]) -> float:
    base = 1.0 + rate
    if base <= 0:
        return float("inf")
    total = 0.0
    for cf, d in zip(cashflows, days):
        t = d / 365.0
        total += -t * cf / (base ** (t + 1.0))
    return total


def xirr(
    cashflows: Sequence[float],
    dates: Sequence[date],
    *,
    guess: float = 0.1,
    tol: float = 1e-7,
    max_iter: int = 200,
) -> float | None:
    """Return annualised IRR as a decimal (0.18 == 18%), or None if no solution."""
    if len(cashflows) != len(dates) or len(cashflows) < 2:
        return None
    if not (any(cf > 0 for cf in cashflows) and any(cf < 0 for cf in cashflows)):
        return None

    d0 = min(dates)
    days = [(d - d0).days for d in dates]

    # Newton-Raphson
    rate = guess
    for _ in range(max_iter):
        f = _npv(rate, cashflows, days)
        if abs(f) < tol:
            return rate
        df = _dnpv(rate, cashflows, days)
        if df == 0 or not (df == df):  # NaN guard
            break
        new_rate = rate - f / df
        if new_rate <= -0.999999:
            new_rate = (rate + -0.999) / 2.0
        if abs(new_rate - rate) < tol:
            return new_rate
        rate = new_rate

    # Fallback: bisection over a wide bracket
    lo, hi = -0.9999, 10.0
    f_lo = _npv(lo, cashflows, days)
    f_hi = _npv(hi, cashflows, days)
    if f_lo * f_hi > 0:
        return None
    for _ in range(500):
        mid = 0.5 * (lo + hi)
        f_mid = _npv(mid, cashflows, days)
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return 0.5 * (lo + hi)
