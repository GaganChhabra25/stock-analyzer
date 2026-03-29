"""
Always-present market overview: Nifty 50, MCX Crude Oil, MCX Natural Gas.

For each instrument shows:
  - Weekly schedule for the current expiry month (Week 1..N):
      date range · expiry date · direction (UP/DOWN) · probability % · expected price
      Past weeks show actual close; current/future weeks show prediction.
  - Monthly summary:
      direction · probability · bull/bear targets · days to expiry

Probability = blended historical weekly/monthly win-rate (55%) + technical score (45%).

Data sources:
  Nifty 50   →  yfinance ^NSEI  (exact NSE index, INR)
  Crude Oil  →  yfinance BZ=F (Brent) × live USD/INR  ≈ MCX INR price
  Nat Gas    →  yfinance NG=F (NYMEX) × live USD/INR  ≈ MCX INR price
"""

import calendar
import warnings
warnings.filterwarnings("ignore")

from datetime import date, timedelta
from typing import Optional, List

import pandas as pd
import yfinance as yf


# ── Expiry utilities ───────────────────────────────────────────────────────────

def _weekdays_in_month(year: int, month: int, weekday: int) -> List[date]:
    """All dates with given weekday (0=Mon…6=Sun) within the calendar month."""
    last = calendar.monthrange(year, month)[1]
    return [
        date(year, month, d)
        for d in range(1, last + 1)
        if date(year, month, d).weekday() == weekday
    ]


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
    """Current or next contract month's expiry date."""
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
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw.index = pd.to_datetime(raw.index)
        return raw.dropna(subset=["Close"])
    except Exception:
        return None


def _usdinr() -> float:
    try:
        raw = _download("USDINR=X", period="3d")
        if raw is None:
            return 84.0
        rate = float(raw["Close"].dropna().iloc[-1])
        return rate if 70 < rate < 120 else 84.0
    except Exception:
        return 84.0


# ── Technical score  (-1..+1, positive = bullish) ─────────────────────────────

def _tech_score(close: pd.Series) -> float:
    # RSI
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=True, min_periods=14).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=True, min_periods=14).mean()
    rs    = gain / loss
    rsi_s = (100 - 100 / (1 + rs)).dropna()
    rsi   = float(rsi_s.iloc[-1]) if not rsi_s.empty else 50.0

    # MACD histogram
    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    macd_h = ((ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()).dropna()
    mh     = float(macd_h.iloc[-1]) if not macd_h.empty else 0.0
    mh_std = float(close.diff().std()) or 1.0
    mh_n   = max(-1.0, min(1.0, mh / (mh_std * 0.05)))

    # SMA20 position
    sma20 = close.rolling(20).mean().dropna()
    sma_c = 0.5 if (not sma20.empty and float(close.iloc[-1]) > float(sma20.iloc[-1])) else -0.5

    # RSI component
    if   rsi < 30: rsi_c = +1.0
    elif rsi < 40: rsi_c = +0.6
    elif rsi < 50: rsi_c = +0.2
    elif rsi < 60: rsi_c = -0.2
    elif rsi < 70: rsi_c = -0.6
    else:          rsi_c = -1.0

    return max(-1.0, min(1.0, rsi_c * 0.40 + mh_n * 0.35 + sma_c * 0.25))


# ── Historical win-rate analysis ───────────────────────────────────────────────

def _week_num_in_month(d: date, expiry_wd: int) -> int:
    """Which occurrence of expiry_wd weekday is date d in its month? (1-based)"""
    count = 0
    for day in range(1, d.day + 1):
        if date(d.year, d.month, day).weekday() == expiry_wd:
            count += 1
    return count


def _weekly_win_rates(df: pd.DataFrame, expiry_wd: int) -> dict:
    """
    For each week-of-month position 1..5, compute:
      % of historical periods where close on expiry_wd > open of Monday that week.
    Returns {1: 65.0, 2: 58.3, ...}  with 55.0 default when data is sparse.
    """
    buckets = {1: [], 2: [], 3: [], 4: [], 5: []}
    today   = date.today()

    for ts in df.index:
        d = ts.date() if hasattr(ts, "date") else ts
        if d.weekday() != expiry_wd or d >= today:
            continue

        # Monday of this week (clamp to month start if needed)
        mon = d - timedelta(days=d.weekday())
        if mon.month != d.month:
            mon = date(d.year, d.month, 1)

        # Find open on first available day on/after Monday in this df
        mon_ts = pd.Timestamp(mon)
        candidates = [t for t in df.index if mon_ts <= t <= pd.Timestamp(d)]
        if not candidates:
            continue

        try:
            mon_open    = float(df.loc[candidates[0], "Open"])
            expiry_close = float(df.loc[pd.Timestamp(d), "Close"])
        except Exception:
            continue

        wk = _week_num_in_month(d, expiry_wd)
        if wk in buckets and mon_open > 0:
            buckets[wk].append(1 if expiry_close > mon_open else 0)

    return {
        wk: (round(sum(v) / len(v) * 100, 1) if len(v) >= 5 else 55.0)
        for wk, v in buckets.items()
    }


def _monthly_win_rate(df: pd.DataFrame) -> float:
    """% of calendar months where month-close > month-open."""
    monthly = df["Close"].resample("ME").agg(["first", "last"]).dropna()
    if len(monthly) < 6:
        return 55.0
    return round(float((monthly["last"] > monthly["first"]).sum()) / len(monthly) * 100, 1)


# ── Probability & target helpers ───────────────────────────────────────────────

def _blend_prob(hist_wr: float, tech: float):
    """
    Returns (direction, probability_pct).
    hist_wr: 0..100 historical win rate
    tech:    -1..+1 technical score
    """
    h   = hist_wr / 100            # 0..1
    t   = (tech + 1) / 2          # 0..1  (0.5 = neutral)
    raw = h * 0.55 + t * 0.45     # 0..1

    if raw >= 0.5:
        direction = "UP"
        prob      = 50 + (raw - 0.5) * 80   # 50..90
    else:
        direction = "DOWN"
        prob      = 50 + (0.5 - raw) * 80   # 50..90

    return direction, round(min(90.0, max(52.0, prob)), 1)


def _weekly_expected_move(df: pd.DataFrame) -> float:
    """Typical weekly price range (open-to-close) from last 8 weeks."""
    close = df["Close"]
    diff  = close.diff().abs().rolling(5).mean().dropna()
    return float(diff.iloc[-1]) * 2.5 if not diff.empty else 0.0


def _monthly_expected_move(df: pd.DataFrame) -> float:
    """Average absolute monthly move (open-to-close) from last 12 months."""
    monthly = df["Close"].resample("ME").agg(["first", "last"]).dropna()
    if len(monthly) < 3:
        return 0.0
    moves = (monthly["last"] - monthly["first"]).abs()
    return float(moves.tail(12).mean())


# ── Weekly schedule builder ────────────────────────────────────────────────────

def _weekly_schedule(
    df: pd.DataFrame,
    cmp: float,
    tech: float,
    expiry_wd: int,
    monthly_exp: date,
    weekly_wr: dict,
    weekly_move: float,
) -> List[dict]:
    """
    Build week-by-week entries for the month of `monthly_exp`.
    Each entry:
      week_num, label, date_from, date_to, expiry_date,
      is_monthly_expiry, is_past, is_current,
      direction, probability, target,
      actual_close, actual_dir, actual_pct  (filled when is_past)
    """
    today  = date.today()
    year   = monthly_exp.year
    month  = monthly_exp.month
    result = []

    expiry_days = _weekdays_in_month(year, month, expiry_wd)

    for i, exp_date in enumerate(expiry_days):
        week_num = i + 1

        # Week start = Monday of this week, clamped to month start
        mon = exp_date - timedelta(days=exp_date.weekday())
        if mon.month != month:
            mon = date(year, month, 1)

        is_past    = exp_date < today
        is_current = (not is_past) and (mon <= today <= exp_date)
        is_monthly = (exp_date == monthly_exp)

        wr        = weekly_wr.get(week_num, 55.0)
        direction, probability = _blend_prob(wr, tech)

        if direction == "UP":
            target = round(cmp + weekly_move, 2)
        else:
            target = round(cmp - weekly_move, 2)

        # Actual outcome for past weeks
        actual_close = actual_dir = actual_pct = None
        if is_past:
            week_data = [
                t for t in df.index
                if pd.Timestamp(mon) <= t <= pd.Timestamp(exp_date)
            ]
            if week_data:
                try:
                    w_open  = float(df.loc[week_data[0],  "Open"])
                    w_close = float(df.loc[week_data[-1], "Close"])
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
    direction, probability = _blend_prob(m_wr, tech)
    m_move    = _monthly_expected_move(df)

    if direction == "UP":
        bull_target = round(cmp + m_move, 2)
        bear_target = round(cmp - m_move * 0.6, 2)
        expected    = bull_target
    else:
        bull_target = round(cmp + m_move * 0.6, 2)
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


# ── Per-instrument full analysis ───────────────────────────────────────────────

def _analyze_instrument(
    ticker: str,
    name: str,
    fx_mult: float,
    expiry_wd: int,
    monthly_expiry_fn,
    note: str,
) -> dict:
    today = date.today()

    df_raw = _download(ticker, period="3y")
    if df_raw is None or len(df_raw) < 60:
        return {"name": name, "cmp": None, "note": note, "unit": "₹",
                "weekly_schedule": [], "monthly": {}, "error": True}

    # Apply FX multiplier to all price columns
    df = df_raw.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in df.columns:
            df[col] = df[col] * fx_mult

    cmp  = round(float(df["Close"].dropna().iloc[-1]), 2)
    tech = _tech_score(df["Close"])

    # Determine contract month (skip if monthly expiry already passed)
    monthly_exp  = _next_monthly_expiry(monthly_expiry_fn, today)
    year, month  = monthly_exp.year, monthly_exp.month

    # Historical win rates and expected moves
    weekly_wr    = _weekly_win_rates(df, expiry_wd)
    weekly_move  = _weekly_expected_move(df)

    schedule  = _weekly_schedule(df, cmp, tech, expiry_wd, monthly_exp, weekly_wr, weekly_move)
    monthly   = _monthly_prediction(df, cmp, tech, monthly_exp)

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
    """
    Fetch always-present market data for the screener report.
    Returns {nifty, crude_oil, nat_gas, usd_inr}.
    """
    usd_inr = _usdinr()

    nifty = _analyze_instrument(
        ticker="^NSEI", name="Nifty 50", fx_mult=1.0,
        expiry_wd=3,                        # Thursday weekly expiry
        monthly_expiry_fn=_last_thursday,
        note="NSE \u2022 Weekly expiry: every Thursday \u2022 Monthly: last Thursday",
    )

    crude = _analyze_instrument(
        ticker="BZ=F", name="MCX Crude Oil", fx_mult=usd_inr,
        expiry_wd=4,                        # Friday week-end reference
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
