"""
NIFTY 50 constituent universe used by this research project.

ASSUMPTION (documented in README): we use the *current* (May 2026) NIFTY 50
membership for the entire 10-year backtest window. We do NOT rebalance the
universe historically -- this introduces a mild survivorship bias because
names that were dropped from the index over the past decade are excluded.

Tickers are Yahoo-Finance symbols (NSE listings, ".NS" suffix).
"""

NIFTY_50: list[str] = [
    "RELIANCE.NS",
    "TCS.NS",
    "HDFCBANK.NS",
    "INFY.NS",
    "ICICIBANK.NS",
    "HINDUNILVR.NS",
    "ITC.NS",
    "SBIN.NS",
    "BHARTIARTL.NS",
    "KOTAKBANK.NS",
    "LT.NS",
    "AXISBANK.NS",
    "BAJFINANCE.NS",
    "ASIANPAINT.NS",
    "MARUTI.NS",
    "M&M.NS",
    "SUNPHARMA.NS",
    "TITAN.NS",
    "ULTRACEMCO.NS",
    "NTPC.NS",
    "NESTLEIND.NS",
    "POWERGRID.NS",
    "HCLTECH.NS",
    "WIPRO.NS",
    "TATAMOTORS.NS",
    "JSWSTEEL.NS",
    "TATASTEEL.NS",
    "ADANIENT.NS",
    "ADANIPORTS.NS",
    "ONGC.NS",
    "COALINDIA.NS",
    "GRASIM.NS",
    "HINDALCO.NS",
    "BAJAJFINSV.NS",
    "BAJAJ-AUTO.NS",
    "INDUSINDBK.NS",
    "EICHERMOT.NS",
    "BRITANNIA.NS",
    "HEROMOTOCO.NS",
    "CIPLA.NS",
    "DRREDDY.NS",
    "DIVISLAB.NS",
    "APOLLOHOSP.NS",
    "TECHM.NS",
    "BPCL.NS",
    "SBILIFE.NS",
    "HDFCLIFE.NS",
    "TATACONSUM.NS",
    "LTIM.NS",
    "SHRIRAMFIN.NS",
]

assert len(NIFTY_50) == 50, f"expected 50 tickers, got {len(NIFTY_50)}"
