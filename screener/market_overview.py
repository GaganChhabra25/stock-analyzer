"""
Always-present market overview: Nifty 50, MCX Crude Oil, MCX Natural Gas.

Per-instrument output:
  Weekly schedule (current expiry month):
    - Week 1..N with date range, expiry date
    - Direction (UP/DOWN), probability %, expected price
    - Past weeks: actual result; current/future: prediction
  Monthly summary:
    - Direction, probability, bull/bear targets, days to expiry

Probability per week =
  historical_win_rate_for_that_week_position (55%)
  + time-decayed technical score — RSI/MACD/SMA (45%)
  Confidence decays for further-out weeks (more uncertainty)

Expected price target:
  Week N target  = cmp  ±  weekly_atr × √N   (square-root time scaling)
  Monthly target = cmp  ±  avg_monthly_move

Data:
  Nifty 50   → ^NSEI   (exact NSE index, INR)
  Crude Oil  → BZ=F    (Brent) × live USD/INR  ≈ MCX price
  Nat Gas    → NG=F    (NYMEX) × live USD/INR  ≈ MCX price
"""

import calendar
import math
import warnings
warnings.filterwarnings("ignore")

from datetime import date, timedelta
from typing import Optional, List

import numpy as np
import pandas as pd
import yfinance as yf


# ── Expiry helpers ─────────────────────────────────────────────────────────────

def _weekdays_in_month(year: int, month: int, weekday: int) -> List[date]:
    last = calendar.monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, last + 1)
            if date(year, month, d).weekday() == weekday]


def _last_thursday(year: int, month: int) -> date:
    return _weekdays_in_month(year, month, 3)[-1]


def _mcx_crude_expiry(year: int, month: int) -> date:
    d = date(year, month, 20)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _mcx_natgas_expiry(year: int, month: int) -> date:
    last = calendar.monthrange(year, month)[1]
    d = date(year, month, last)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _next_monthly_expiry(calc_fn, today: date) -> date:
    exp = calc_fn(today.year, today.month)
    if exp < today:
        first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        exp = calc_fn(first_next.year, first_next.month)
    return exp


# ── yfinance download ──────────────────────────────────────────────────────────

def _download(ticker: str, period: str = "3y") -> Optional[pd.DataFrame]:
    try:
        raw = yf.download(ticker, period=period, interval="1d",
                          progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return None
        # Flatten MultiIndex columns (newer yfinance)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        # Always tz-naive DatetimeIndex — avoids comparison errors
        raw.index = pd.to_datetime(raw.index).tz_localize(None)
        return raw.dropna(subset=["Close"])
    except Exception:
        return None


def _usdinr() -> float:
    try:
        raw = _download("USDINR=X", period="5d")
        if raw is None:
            return 84.0
        rate = float(raw["Close"].dropna().iloc[-1])
        return rate if 70 < rate < 120 else 84.0
    except Exception:
        return 84.0


# ── Technical score (-1..+1) ───────────────────────────────────────────────────

def _tech_score(df: pd.DataFrame) -> float:
    """
    Blended technical signal in -1..+1.
    Components: RSI (40%), MACD histogram (35%), SMA20 position (25%).
    """
    close = df["Close"].dropna()
    if len(close) < 30:
        return 0.0

    # RSI-14
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=True, min_periods=14).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=True, min_periods=14).mean()
    rsi   = float((100 - 100 / (1 + gain / loss.replace(0, np.nan))).dropna().iloc[-1])

    # MACD histogram normalised by recent volatility
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    hist  = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    mh    = float(hist.dropna().iloc[-1])
    vol   = float(close.pct_change().rolling(20).std().dropna().iloc[-1]) * float(close.iloc[-1])
    mh_n  = max(-1.0, min(1.0, mh / (vol * 0.5 + 1e-9)))

    # Price vs SMA20
    sma20 = float(close.rolling(20).mean().dropna().iloc[-1])
    sma_c = 0.5 if float(close.iloc[-1]) > sma20 else -0.5

    # Price vs SMA50 (extra signal)
    sma50 = close.rolling(50).mean().dropna()
    sma50_c = (0.3 if float(close.iloc[-1]) > float(sma50.iloc[-1]) else -0.3) if not sma50.empty else 0.0

    # RSI component
    if   rsi < 30: rsi_c = +1.0
    elif rsi < 40: rsi_c = +0.6
    elif rsi < 48: rsi_c = +0.2
    elif rsi < 55: rsi_c = -0.1
    elif rsi < 65: rsi_c = -0.5
    else:          rsi_c = -1.0

    score = rsi_c * 0.35 + mh_n * 0.30 + sma_c * 0.20 + sma50_c * 0.15
    return round(max(-1.0, min(1.0, score)), 4)


# ── Weekly ATR (expected 1-week move) ──────────────────────────────────────────

def _weekly_atr(df: pd.DataFrame) -> float:
    """
    Typical 1-week price range from True Range.
    Uses ATR(14) × √5 as the 1-week expected move (random-walk scaling).
    Falls back to avg absolute daily move × 2.5.
    """
    close = df["Close"]
    if "High" in df.columns and "Low" in df.columns:
        high, low = df["High"], df["Low"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean().dropna()
        if not atr14.empty:
            return float(atr14.iloc[-1]) * math.sqrt(5)
    # fallback
    diff = close.diff().abs().rolling(14).mean().dropna()
    return float(diff.iloc[-1]) * 2.5 if not diff.empty else float(close.iloc[-1]) * 0.02


# ── Historical win rates by week-of-month ──────────────────────────────────────

def _week_num_in_month(d: date, expiry_wd: int) -> int:
    """Which occurrence (1-based) of expiry_wd weekday is date d in its month?"""
    count = 0
    for day in range(1, d.day + 1):
        if date(d.year, d.month, day).weekday() == expiry_wd:
            count += 1
    return count


def _weekly_win_rates(df: pd.DataFrame, expiry_wd: int) -> dict:
    """
    For each week-of-month position 1..5:
      win rate = % of past expiry days where close > Monday-open of that week.

    Uses boolean-mask slicing (robust to any tz-naive DatetimeIndex).
    Returns {1: 64.3, 2: 55.0, 3: 60.7, 4: 71.4, 5: 50.0}
    """
    buckets:  dict = {1: [], 2: [], 3: [], 4: [], 5: []}
    today_ts = pd.Timestamp(date.today())

    # Pre-compute weekday column to avoid repeated .weekday() calls
    df2 = df.copy()
    df2["_wd"] = df2.index.weekday

    # Iterate only over expiry days (much faster)
    expiry_mask = (df2["_wd"] == expiry_wd) & (df2.index < today_ts)
    for ts in df2.index[expiry_mask]:
        d   = ts.date()
        mon = d - timedelta(days=d.weekday())    # Monday of that week
        if mon.month != d.month:
            mon = date(d.year, d.month, 1)       # clamp to month start

        mon_ts = pd.Timestamp(mon)
        # Slice the week using boolean mask — tz-safe
        week_df = df2[(df2.index >= mon_ts) & (df2.index <= ts)]
        if week_df.empty:
            continue

        try:
            mon_open     = float(week_df["Open"].iloc[0])
            expiry_close = float(week_df["Close"].iloc[-1])
        except Exception:
            continue

        if mon_open <= 0:
            continue

        wk = _week_num_in_month(d, expiry_wd)
        if wk in buckets:
            buckets[wk].append(1 if expiry_close > mon_open else 0)

    result = {}
    for wk, vals in buckets.items():
        n = len(vals)
        if n >= 8:
            result[wk] = round(sum(vals) / n * 100, 1)
        elif n >= 4:
            # Small sample — blend toward 50%
            raw = sum(vals) / n * 100
            result[wk] = round(raw * 0.7 + 50.0 * 0.3, 1)
        else:
            result[wk] = 52.0   # near-neutral when no data
    return result


def _monthly_win_rate(df: pd.DataFrame) -> float:
    """% of calendar months where last close > first open."""
    monthly = df["Close"].resample("ME").agg(["first", "last"]).dropna()
    if len(monthly) < 6:
        return 55.0
    return round(float((monthly["last"] > monthly["first"]).sum()) / len(monthly) * 100, 1)


# ── Probability blending ───────────────────────────────────────────────────────

def _blend_prob(hist_wr: float, tech: float, time_decay: float = 1.0):
    """
    Returns (direction, probability_pct).
    hist_wr  : 0..100  historical win rate
    tech     : -1..+1  technical score
    time_decay: 0..1   decay factor (1.0 = full confidence, 0.5 = half)

    Tech score is scaled by time_decay so further-out weeks are
    pulled toward 50% (higher uncertainty).
    """
    h   = hist_wr / 100                         # 0..1
    t   = ((tech * time_decay) + 1) / 2         # 0..1
    raw = h * 0.55 + t * 0.45                   # 0..1

    if raw >= 0.5:
        direction = "UP"
        prob      = 50 + (raw - 0.5) * 80       # 50..90
    else:
        direction = "DOWN"
        prob      = 50 + (0.5 - raw) * 80

    return direction, round(min(90.0, max(52.0, prob)), 1)


# ── Monthly expected move ──────────────────────────────────────────────────────

def _monthly_expected_move(df: pd.DataFrame) -> float:
    """Average absolute monthly open→close move from last 18 months."""
    monthly = df["Close"].resample("ME").agg(["first", "last"]).dropna()
    if len(monthly) < 3:
        return 0.0
    return float((monthly["last"] - monthly["first"]).abs().tail(18).mean())


# ── Recent weekly momentum ─────────────────────────────────────────────────────

def _recent_week_momentum(df: pd.DataFrame) -> float:
    """
    Returns +1 if last full week closed above its Monday open (bullish),
    -1 if below (bearish), 0 if flat (±0.3%).
    """
    today = date.today()
    # Find last completed Friday (or most recent expiry day)
    last_fri = today - timedelta(days=(today.weekday() - 4) % 7 + 7)
    last_mon = last_fri - timedelta(days=4)
    mon_ts, fri_ts = pd.Timestamp(last_mon), pd.Timestamp(last_fri)
    week_df = df[(df.index >= mon_ts) & (df.index <= fri_ts)]
    if week_df.empty or len(week_df) < 3:
        return 0.0
    try:
        o = float(week_df["Open"].iloc[0])
        c = float(week_df["Close"].iloc[-1])
        pct = (c - o) / o * 100 if o else 0.0
        if pct >  0.3: return +1.0
        if pct < -0.3: return -1.0
        return 0.0
    except Exception:
        return 0.0


# ── Weekly schedule ────────────────────────────────────────────────────────────

# How much confidence decays per week (1=full, 0=no confidence beyond hist)
_DECAY = {1: 1.00, 2: 0.88, 3: 0.76, 4: 0.64, 5: 0.52}


def _weekly_schedule(
    df: pd.DataFrame,
    cmp: float,
    tech: float,
    expiry_wd: int,
    monthly_exp: date,
    weekly_wr: dict,
    w_atr: float,
    recent_momentum: float,
) -> List[dict]:
    today       = date.today()
    year, month = monthly_exp.year, monthly_exp.month
    expiry_days = _weekdays_in_month(year, month, expiry_wd)
    result      = []

    for i, exp_date in enumerate(expiry_days):
        week_num  = i + 1
        mon       = exp_date - timedelta(days=exp_date.weekday())
        if mon.month != month:
            mon = date(year, month, 1)

        is_past    = exp_date < today
        is_current = (not is_past) and (mon <= today <= exp_date)
        is_monthly = (exp_date == monthly_exp)

        # Historical win rate for this week position
        wr = weekly_wr.get(week_num, 52.0)

        # Time decay: further weeks have tech score pulled toward neutral
        decay = _DECAY.get(week_num, 0.50)

        # Blend tech with recent momentum (momentum matters most in week 1)
        mom_weight = max(0.0, 1.0 - (week_num - 1) * 0.25)   # 1.0 → 0.25 → 0.0
        blended_tech = tech + recent_momentum * 0.15 * mom_weight
        blended_tech = max(-1.0, min(1.0, blended_tech))

        direction, probability = _blend_prob(wr, blended_tech, decay)

        # Expected price: cmp ± ATR × √week_num  (random-walk time scaling)
        time_scale = math.sqrt(week_num)
        if direction == "UP":
            target = round(cmp + w_atr * time_scale, 2)
        else:
            target = round(cmp - w_atr * time_scale, 2)

        # Expected range (±½ ATR around target)
        range_hi = round(target + w_atr * 0.5, 2)
        range_lo = round(target - w_atr * 0.5, 2)

        # Actual result for past weeks
        actual_close = actual_dir = actual_pct = None
        if is_past:
            mon_ts, exp_ts = pd.Timestamp(mon), pd.Timestamp(exp_date)
            week_df = df[(df.index >= mon_ts) & (df.index <= exp_ts)]
            if not week_df.empty:
                try:
                    w_open  = float(week_df["Open"].iloc[0])
                    w_close = float(week_df["Close"].iloc[-1])
                    actual_close = round(w_close, 2)
                    actual_dir   = "UP" if w_close >= w_open else "DOWN"
                    actual_pct   = round((w_close - w_open) / w_open * 100, 2) if w_open else 0.0
                except Exception:
                    pass

        result.append({
            "week_num":          week_num,
            "label":             f"Week {week_num}",
            "date_from":         mon.strftime("%d %b"),
            "date_to":           exp_date.strftime("%d %b"),
            "expiry_date":       exp_date.strftime("%d %b %Y"),
            "is_monthly_expiry": is_monthly,
            "is_past":           is_past,
            "is_current":        is_current,
            "direction":         direction,
            "probability":       probability,
            "target":            target,
            "range_hi":          range_hi,
            "range_lo":          range_lo,
            "hist_wr":           wr,
            "actual_close":      actual_close,
            "actual_dir":        actual_dir,
            "actual_pct":        actual_pct,
        })

    return result


# ── Monthly prediction ─────────────────────────────────────────────────────────

def _monthly_prediction(
    df: pd.DataFrame,
    cmp: float,
    tech: float,
    monthly_exp: date,
) -> dict:
    today     = date.today()
    m_wr      = _monthly_win_rate(df)
    direction, probability = _blend_prob(m_wr, tech, time_decay=0.80)
    m_move    = _monthly_expected_move(df)

    if direction == "UP":
        bull_target = round(cmp + m_move, 2)
        bear_target = round(cmp - m_move * 0.55, 2)
        expected    = bull_target
    else:
        bull_target = round(cmp + m_move * 0.55, 2)
        bear_target = round(cmp - m_move, 2)
        expected    = bear_target

    return {
        "label":          monthly_exp.strftime("%b %Y"),
        "expiry_date":    monthly_exp.strftime("%d %b %Y"),
        "days_to_expiry": (monthly_exp - today).days,
        "direction":      direction,
        "probability":    probability,
        "expected_price": expected,
        "bull_target":    bull_target,
        "bear_target":    bear_target,
        "hist_wr":        m_wr,
    }


# ── Per-instrument analysis ────────────────────────────────────────────────────

def _analyze_instrument(
    ticker: str,
    name: str,
    fx_mult: float,
    expiry_wd: int,
    monthly_expiry_fn,
    note: str,
) -> dict:
    today  = date.today()
    df_raw = _download(ticker, period="3y")

    if df_raw is None or len(df_raw) < 60:
        return {"name": name, "cmp": None, "note": note, "unit": "\u20b9",
                "weekly_schedule": [], "monthly": {}, "error": True}

    # Apply FX multiplier
    df = df_raw.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in df.columns:
            df[col] = df[col] * fx_mult

    cmp            = round(float(df["Close"].dropna().iloc[-1]), 2)
    tech           = _tech_score(df)
    recent_mom     = _recent_week_momentum(df)
    monthly_exp    = _next_monthly_expiry(monthly_expiry_fn, today)
    weekly_wr      = _weekly_win_rates(df, expiry_wd)
    w_atr          = _weekly_atr(df)

    schedule = _weekly_schedule(
        df, cmp, tech, expiry_wd, monthly_exp,
        weekly_wr, w_atr, recent_mom,
    )
    monthly = _monthly_prediction(df, cmp, tech, monthly_exp)

    return {
        "name":            name,
        "cmp":             cmp,
        "note":            note,
        "unit":            "\u20b9",
        "weekly_schedule": schedule,
        "monthly":         monthly,
        "error":           False,
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_market_overview() -> dict:
    usd_inr = _usdinr()

    nifty = _analyze_instrument(
        ticker="^NSEI", name="Nifty 50", fx_mult=1.0,
        expiry_wd=3,
        monthly_expiry_fn=_last_thursday,
        note="NSE \u2022 Weekly: every Thursday \u2022 Monthly: last Thursday",
    )
    crude = _analyze_instrument(
        ticker="BZ=F", name="MCX Crude Oil", fx_mult=usd_inr,
        expiry_wd=4,
        monthly_expiry_fn=_mcx_crude_expiry,
        note=f"MCX \u2022 Monthly expiry: ~20th \u2022 Brent \u00d7 \u20b9{usd_inr:.1f}",
    )
    nat_gas = _analyze_instrument(
        ticker="NG=F", name="MCX Natural Gas", fx_mult=usd_inr,
        expiry_wd=4,
        monthly_expiry_fn=_mcx_natgas_expiry,
        note=f"MCX \u2022 Monthly expiry: last day \u2022 NYMEX \u00d7 \u20b9{usd_inr:.1f}",
    )

    return {"nifty": nifty, "crude_oil": crude, "nat_gas": nat_gas, "usd_inr": usd_inr}
