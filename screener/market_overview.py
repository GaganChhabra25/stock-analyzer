"""
Always-present market overview instruments:
  - Nifty 50 (weekly levels for 5 weeks + current month F&O expiry)
  - MCX Crude Oil (weekly levels + ~20th monthly expiry)
  - MCX Natural Gas (weekly levels + last-day monthly expiry)

Data sources:
  - Nifty 50  : yfinance ^NSEI  (actual NSE index, INR)
  - Crude Oil : yfinance BZ=F   (Brent) × live USD/INR  ≈ MCX INR price
  - Nat Gas   : yfinance NG=F   (NYMEX) × live USD/INR  ≈ MCX INR price
"""

import calendar
import warnings
warnings.filterwarnings("ignore")

from datetime import date, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf


# ── Expiry date calculators ────────────────────────────────────────────────────

def _last_thursday(year: int, month: int) -> date:
    """Last Thursday of month — NSE Nifty F&O monthly expiry."""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != 3:   # 3 = Thursday
        d -= timedelta(days=1)
    return d


def _mcx_crude_expiry(year: int, month: int) -> date:
    """MCX Crude Oil: 20th of the month (or nearest prior business day)."""
    d = date(year, month, 20)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _mcx_natgas_expiry(year: int, month: int) -> date:
    """MCX Natural Gas: last business day of the month."""
    last = calendar.monthrange(year, month)[1]
    d = date(year, month, last)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _next_expiry(calc_fn, today: date):
    """Return (expiry_date, days_remaining) for the current/next active contract."""
    exp = calc_fn(today.year, today.month)
    if exp < today:
        first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        exp = calc_fn(first_next.year, first_next.month)
    return exp, (exp - today).days


# ── yfinance helpers ───────────────────────────────────────────────────────────

def _download(ticker: str, period: str = "60d", interval: str = "1d") -> Optional[pd.DataFrame]:
    try:
        raw = yf.download(ticker, period=period, interval=interval,
                          progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return None
        # Flatten MultiIndex columns (newer yfinance versions)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        return raw
    except Exception:
        return None


def _spot(ticker: str, fx_mult: float = 1.0) -> Optional[float]:
    df = _download(ticker, period="3d")
    if df is None:
        return None
    try:
        val = float(df["Close"].dropna().iloc[-1]) * fx_mult
        return round(val, 2)
    except Exception:
        return None


def _usdinr() -> float:
    """Live USD/INR rate; falls back to 84.0."""
    try:
        rate = _spot("USDINR=X")
        return rate if (rate and 70 < rate < 120) else 84.0
    except Exception:
        return 84.0


def _weekly_candles(ticker: str, n_weeks: int = 5, fx_mult: float = 1.0) -> list:
    """
    Return last n_weeks of weekly OHLC candles.
    Built from daily data grouped by ISO week — more reliable than weekly interval.
    """
    period_days = (n_weeks + 4) * 7
    df = _download(ticker, period=f"{period_days}d", interval="1d")
    if df is None:
        return []

    try:
        df["iso_year"] = df.index.isocalendar().year.values
        df["iso_week"] = df.index.isocalendar().week.values
    except Exception:
        return []

    weeks = []
    grouped = df.groupby(["iso_year", "iso_week"])
    for key in sorted(grouped.groups.keys()):
        grp = grouped.get_group(key)
        if len(grp) < 3:
            continue
        try:
            o = float(grp["Open"].iloc[0])   * fx_mult
            h = float(grp["High"].max())     * fx_mult
            l = float(grp["Low"].min())      * fx_mult
            c = float(grp["Close"].iloc[-1]) * fx_mult
        except Exception:
            continue
        pct = round((c - o) / o * 100, 2) if o else 0.0
        weeks.append({
            "week":  f"Wk {key[1]}/{str(key[0])[-2:]}",
            "open":  round(o, 2),
            "high":  round(h, 2),
            "low":   round(l, 2),
            "close": round(c, 2),
            "pct":   pct,
            "bull":  c >= o,
        })

    # Drop the last entry if it's an incomplete week (current week)
    if len(weeks) > 1:
        weeks = weeks[:-1]

    return weeks[-n_weeks:]


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_market_overview() -> dict:
    """
    Fetch always-present market data for the screener report.

    Returns:
        {
          "nifty":     {name, cmp, expiry, days_to_expiry, weekly, unit, note},
          "crude_oil": {...},
          "nat_gas":   {...},
          "usd_inr":   float,
        }
    Each 'weekly' is a list of dicts: {week, open, high, low, close, pct, bull}
    """
    today    = date.today()
    usd_inr  = _usdinr()

    # ── Nifty 50 ──────────────────────────────────────────────────────────────
    nifty_exp, nifty_dte = _next_expiry(_last_thursday, today)
    nifty = {
        "name":           "Nifty 50",
        "cmp":            _spot("^NSEI"),
        "expiry":         nifty_exp.strftime("%d %b %Y"),
        "days_to_expiry": nifty_dte,
        "weekly":         _weekly_candles("^NSEI", n_weeks=5),
        "unit":           "\u20b9",          # ₹
        "note":           "NSE \u2022 Monthly expiry: last Thursday",
    }

    # ── MCX Crude Oil  (Brent USD \u00d7 USD/INR \u2248 MCX INR) ────────────────────────────
    crude_exp, crude_dte = _next_expiry(_mcx_crude_expiry, today)
    crude = {
        "name":           "MCX Crude Oil",
        "cmp":            _spot("BZ=F", fx_mult=usd_inr),
        "expiry":         crude_exp.strftime("%d %b %Y"),
        "days_to_expiry": crude_dte,
        "weekly":         _weekly_candles("BZ=F", n_weeks=5, fx_mult=usd_inr),
        "unit":           "\u20b9",
        "note":           f"MCX \u2022 Expiry ~20th \u2022 Brent \u00d7 \u20b9{usd_inr:.1f}",
    }

    # ── MCX Natural Gas  (NYMEX USD \u00d7 USD/INR \u2248 MCX INR) ──────────────────────────
    gas_exp, gas_dte = _next_expiry(_mcx_natgas_expiry, today)
    nat_gas = {
        "name":           "MCX Natural Gas",
        "cmp":            _spot("NG=F", fx_mult=usd_inr),
        "expiry":         gas_exp.strftime("%d %b %Y"),
        "days_to_expiry": gas_dte,
        "weekly":         _weekly_candles("NG=F", n_weeks=5, fx_mult=usd_inr),
        "unit":           "\u20b9",
        "note":           f"MCX \u2022 Expiry last day \u2022 NYMEX \u00d7 \u20b9{usd_inr:.1f}",
    }

    return {
        "nifty":     nifty,
        "crude_oil": crude,
        "nat_gas":   nat_gas,
        "usd_inr":   usd_inr,
    }
