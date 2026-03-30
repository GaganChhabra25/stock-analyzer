"""
Month and week prediction functions — extracted from screener/analyzer.py.

These functions depend on the current date (via datetime.today()) so they
are kept separate from signals.py (which is fully pure/timeless) to make
time-dependent tests easy to patch:

    with unittest.mock.patch("screener.predictions.datetime") as mock_dt:
        mock_dt.today.return_value = datetime(2026, 3, 15)
        result = current_month_prediction(...)
"""

import calendar
from datetime import datetime, timedelta
from typing import List, Tuple

import pandas as pd

from screener.signals import safe_last


# ── Monthly history ───────────────────────────────────────────────────────────

def monthly_history(close: pd.Series) -> List[dict]:
    """
    Return the last 13 months of month-open → month-close data.

    Each entry: {label, open, close, ret, direction}
    direction: "UP" | "DOWN" | "FLAT" (within ±1.5%)
    """
    monthly = close.resample("ME").agg(["first", "last"]).dropna()
    monthly.columns = ["open", "close"]
    monthly["ret"]  = (monthly["close"] / monthly["open"] - 1) * 100

    records = []
    for dt, row in monthly.tail(13).iterrows():
        ret = float(row["ret"])
        records.append({
            "label":     dt.strftime("%b %Y"),
            "open":      round(float(row["open"]), 2),
            "close":     round(float(row["close"]), 2),
            "ret":       round(ret, 2),
            "direction": "UP" if ret > 1.5 else ("DOWN" if ret < -1.5 else "FLAT"),
        })
    return records


# ── Weekly history ────────────────────────────────────────────────────────────

def weekly_history(hist: pd.DataFrame) -> List[dict]:
    """Return the last 5 complete Monday→Friday weeks with OHLCV summary."""
    df = hist.copy()
    df["iso_year"] = df.index.isocalendar().year.values
    df["iso_week"] = df.index.isocalendar().week.values

    weeks   = []
    grouped = df.groupby(["iso_year", "iso_week"])
    keys    = sorted(grouped.groups.keys())[-7:]   # take last 7, filter partial weeks

    for key in keys:
        grp = grouped.get_group(key)
        if len(grp) < 3:
            continue
        mon_open  = float(grp["Open"].iloc[0])
        fri_close = float(grp["Close"].iloc[-1])
        high_wk   = float(grp["High"].max())
        low_wk    = float(grp["Low"].min())
        vol_avg   = float(grp["Volume"].mean())
        pct       = round((fri_close / mon_open - 1) * 100, 2) if mon_open > 0 else 0.0
        weeks.append({
            "label":     f"Wk {key[1]}/{str(key[0])[-2:]}",
            "mon_open":  round(mon_open, 2),
            "fri_close": round(fri_close, 2),
            "high":      round(high_wk, 2),
            "low":       round(low_wk, 2),
            "pct":       pct,
            "direction": "UP" if pct > 0 else "DOWN",
            "vol_avg":   round(vol_avg / 1e6, 2),  # millions
        })

    return weeks[-5:]


# ── Seasonal returns ──────────────────────────────────────────────────────────

def seasonal_month_returns(close: pd.Series, month: int) -> List[Tuple[int, float]]:
    """
    Return list of (year, return_pct) for a given calendar month
    over the last 5 available years of price data.
    """
    today   = datetime.today()
    results = []
    for yr in range(today.year - 5, today.year):
        try:
            last_day = calendar.monthrange(yr, month)[1]
            m_start  = pd.Timestamp(year=yr, month=month, day=1,        tz=None)
            m_end    = pd.Timestamp(year=yr, month=month, day=last_day, tz=None)
            idx      = close.index
            if idx.tz is not None:
                m_start = m_start.tz_localize(idx.tz)
                m_end   = m_end.tz_localize(idx.tz)
            chunk = close[(idx >= m_start) & (idx <= m_end)]
            if len(chunk) < 10:
                continue
            ret = round((float(chunk.iloc[-1]) / float(chunk.iloc[0]) - 1) * 100, 2)
            results.append((yr, ret))
        except Exception:
            pass
    return results


# ── Current month prediction ──────────────────────────────────────────────────

def current_month_prediction(close: pd.Series, daily_std: float,
                              norm_score: float) -> dict:
    """
    Analyse the in-progress calendar month (e.g. March 2026).

    Returns a dict with:
      - MTD return so far
      - Remaining trading days estimate
      - EOM close probability
      - Historical performance for this calendar month (last 5 years)
    """
    today = datetime.today()
    idx   = close.index

    # Find month-open price (first trading day of this month)
    try:
        m_start = pd.Timestamp(year=today.year, month=today.month, day=1, tz=None)
        if idx.tz is not None:
            m_start = m_start.tz_localize(idx.tz)
        this_month = close[idx >= m_start]
        month_open = float(this_month.iloc[0]) if not this_month.empty else float(close.iloc[-20])
    except Exception:
        month_open = float(close.iloc[-20])

    current_price = float(close.iloc[-1])
    mtd_return    = round((current_price / month_open - 1) * 100, 2) if month_open > 0 else 0.0

    # Remaining trading days estimate
    last_day_of_month  = calendar.monthrange(today.year, today.month)[1]
    remaining_cal      = last_day_of_month - today.day
    trading_days_left  = max(1, int(remaining_cal * 5 / 7))

    remaining_1std = round(daily_std * (trading_days_left ** 0.5) * 100, 1)

    # EOM direction locked in when MTD move >> remaining volatility
    mtd_locked = abs(mtd_return) > remaining_1std * 1.5
    eom_dir    = "UP" if mtd_return >= 0 else "DOWN"

    # EOM close probability based on current MTD progress
    if   mtd_return >  5: eom_prob = 88
    elif mtd_return >  3: eom_prob = 80
    elif mtd_return >  1: eom_prob = 70
    elif mtd_return > -1: eom_prob = 52 + norm_score * 0.15   # neutral — tech decides
    elif mtd_return > -3: eom_prob = 38
    elif mtd_return > -5: eom_prob = 28
    else:                  eom_prob = 18

    # Adjust for proximity to month-end
    if trading_days_left <= 3:
        eom_prob = min(94, eom_prob + 8) if mtd_return > 0 else max(8, eom_prob - 8)

    seasonal  = seasonal_month_returns(close, today.month)
    seas_wr   = round(sum(1 for _, r in seasonal if r > 0) / len(seasonal) * 100, 1) if seasonal else 50.0
    seas_avg  = round(sum(r for _, r in seasonal) / len(seasonal), 2) if seasonal else 0.0

    eom_up_target   = round(current_price * (1 + remaining_1std / 100), 2)
    eom_down_target = round(current_price * (1 - remaining_1std / 100), 2)

    return {
        "label":              today.strftime("%B %Y"),
        "month_open":         round(month_open, 2),
        "current_price":      round(current_price, 2),
        "mtd_return":         mtd_return,
        "trading_days_left":  trading_days_left,
        "remaining_move_1sd": remaining_1std,
        "eom_direction":      eom_dir,
        "eom_prob":           round(min(94, max(10, eom_prob)), 1),
        "eom_up_target":      eom_up_target,
        "eom_down_target":    eom_down_target,
        "locked_in":          mtd_locked,
        "seasonal_wr":        seas_wr,
        "seasonal_avg":       seas_avg,
        "seasonal_history":   seasonal,
    }


# ── Next month prediction ─────────────────────────────────────────────────────

def next_month_prediction(close: pd.Series, direction: str, norm_score: float,
                          monthly_vol: float) -> dict:
    """
    Forecast for the next calendar month (e.g. April 2026).

    Combines seasonal bias (how this stock performed in that month historically)
    with current momentum direction to produce a final directional call and
    expected price range.
    """
    today         = datetime.today()
    nm_num        = today.month + 1 if today.month < 12 else 1
    nm_year       = today.year if today.month < 12 else today.year + 1
    nm_label      = f"{calendar.month_name[nm_num]} {nm_year}"
    current_price = float(close.iloc[-1])

    seasonal  = seasonal_month_returns(close, nm_num)
    seas_wr   = round(sum(1 for _, r in seasonal if r > 0) / len(seasonal) * 100, 1) if seasonal else 50.0
    seas_avg  = round(sum(r for _, r in seasonal) / len(seasonal), 2) if seasonal else 0.0
    seas_dir  = "UP" if seas_avg >= 0 else "DOWN"

    momentum_up = direction == "UP"
    seasonal_up = seas_dir  == "UP"
    aligned     = momentum_up == seasonal_up

    if aligned:
        base_prob = max(seas_wr, 65.0) + (norm_score - 50) * 0.18
        final_dir = "UP" if seasonal_up else "DOWN"
    else:
        if norm_score > 68:           # Dominant technical signal
            base_prob = 62 + (norm_score - 68) * 0.7
            final_dir = direction
        elif seas_wr > 65:            # Strong seasonal bias
            base_prob = seas_wr
            final_dir = seas_dir
        else:                          # No clear winner — use stronger signal
            base_prob = max(seas_wr, 55.0)
            final_dir = direction

    # Price targets for next month
    mv          = monthly_vol / 100
    bull_target = round(current_price * (1 + mv * 1.5), 2)
    base_target = round(current_price * (1 + mv * (1 if final_dir == "UP" else -1)), 2)
    bear_target = round(current_price * (1 - mv * 1.5), 2)
    range_high  = round(current_price * (1 + mv), 2)
    range_low   = round(current_price * (1 - mv), 2)

    return {
        "label":            nm_label,
        "direction":        final_dir,
        "probability":      round(min(90, max(65, base_prob)), 1),
        "aligned":          aligned,
        "seasonal_wr":      seas_wr,
        "seasonal_avg":     seas_avg,
        "seasonal_dir":     seas_dir,
        "momentum_dir":     direction,
        "seasonal_history": seasonal,
        "bull_target":      bull_target,
        "base_target":      base_target,
        "bear_target":      bear_target,
        "range_high":       range_high,
        "range_low":        range_low,
    }
