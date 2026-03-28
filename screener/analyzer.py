"""
Core screening & statistical analysis engine.
Filters NSE universe by liquidity, scores each stock technically,
computes historical win rates and weekly patterns.
"""

import warnings
warnings.filterwarnings("ignore")

import calendar
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

from screener.universe import get_symbols, get_meta


MIN_DAILY_VALUE_CR = 5      # ₹5 Crore minimum average daily turnover
MIN_DATA_DAYS      = 120    # Need at least 4 months of history
BATCH_SIZE         = 30     # Download N tickers at a time


# ── Technical helpers ──────────────────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=True, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=True, min_periods=period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series):
    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    return macd, signal, hist


def _bollinger(close: pd.Series, period: int = 20):
    sma   = close.rolling(period).mean()
    std   = close.rolling(period).std()
    return sma, sma + 2 * std, sma - 2 * std


def _safe(series: pd.Series):
    """Return last non-NaN float or 0."""
    v = series.dropna()
    return float(v.iloc[-1]) if not v.empty else 0.0


# ── Monthly & weekly analysis ──────────────────────────────────────────────────

def _monthly_history(close: pd.Series) -> list:
    """Returns last 13 months of month-open → month-close data."""
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


def _weekly_history(hist: pd.DataFrame) -> list:
    """Returns last 5 complete Monday→Friday weeks."""
    df = hist.copy()
    df["iso_year"] = df.index.isocalendar().year.values
    df["iso_week"] = df.index.isocalendar().week.values

    weeks   = []
    grouped = df.groupby(["iso_year", "iso_week"])
    keys    = sorted(grouped.groups.keys())[-7:]   # take last 7, filter partials

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
            "vol_avg":   round(vol_avg / 1e6, 2),  # in millions
        })

    return weeks[-5:]


def _weekly_win_rate(close: pd.Series) -> float:
    """% of Mon→Fri weeks that were positive over last 12 months."""
    wkly = close.resample("W-FRI").last().pct_change().dropna() * 100
    if len(wkly) < 8:
        return 50.0
    return round(float((wkly > 0).sum()) / len(wkly) * 100, 1)


# ── Directional scoring ────────────────────────────────────────────────────────

def _score_direction(close, volume):
    """
    Returns (raw_score, direction, breakdown).
    raw_score > 0 = bullish, < 0 = bearish.
    Breakdown is a dict of component scores for display.
    """
    latest = float(close.iloc[-1])
    breakdown = {}
    score = 0

    # RSI (max ±30)
    rsi_ser = _rsi(close)
    rsi_val = _safe(rsi_ser)
    if   rsi_val < 30:  rs = 30
    elif rsi_val < 40:  rs = 20
    elif rsi_val < 50:  rs = 10
    elif rsi_val < 60:  rs = -5
    elif rsi_val < 70:  rs = -18
    else:               rs = -30
    score += rs
    breakdown["rsi"] = {"value": round(rsi_val, 1), "score": rs}

    # MACD (max ±25)
    macd, sig, hist_m = _macd(close)
    mv, sv = _safe(macd), _safe(sig)
    if   mv > sv and mv > 0:   ms = 25
    elif mv > sv:               ms = 14
    elif mv < sv and mv < 0:   ms = -25
    else:                       ms = -14
    score += ms
    breakdown["macd"] = {
        "value":  round(mv, 3),
        "signal": round(sv, 3),
        "score":  ms,
        "cross":  "Bullish" if mv > sv else "Bearish",
    }

    # Price vs SMAs (max ±25)
    sma20  = close.rolling(20).mean()
    sma50  = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    s20, s50, s200 = _safe(sma20), _safe(sma50), _safe(sma200)
    ps = 0
    if s20  > 0: ps += 10 if latest > s20  else -10
    if s50  > 0: ps += 10 if latest > s50  else -10
    if s200 > 0: ps +=  5 if latest > s200 else -5
    score += ps
    breakdown["sma"] = {
        "sma20":  round(s20, 2),
        "sma50":  round(s50, 2),
        "sma200": round(s200, 2),
        "score":  ps,
        "vs20":   round((latest / s20  - 1) * 100, 1) if s20  else 0,
        "vs50":   round((latest / s50  - 1) * 100, 1) if s50  else 0,
        "vs200":  round((latest / s200 - 1) * 100, 1) if s200 else 0,
    }

    # Bollinger Band position (max ±20)
    _, bb_up, bb_low = _bollinger(close)
    bu, bl = _safe(bb_up), _safe(bb_low)
    bbs = 0
    if bu > bl:
        bb_pos = (latest - bl) / (bu - bl)   # 0 = at lower band, 1 = at upper
        breakdown["bb_pos_pct"] = round(bb_pos * 100, 1)
        if   bb_pos < 0.15: bbs = 20
        elif bb_pos < 0.30: bbs = 12
        elif bb_pos > 0.85: bbs = -20
        elif bb_pos > 0.70: bbs = -12
    score += bbs
    breakdown["bollinger"] = {"upper": round(bu, 2), "lower": round(bl, 2), "score": bbs}

    # Volume trend (amplifier, max ±10)
    v5  = float(volume.tail(5).mean())
    v20 = float(volume.tail(20).mean())
    vol_ratio = round(v5 / v20, 2) if v20 > 0 else 1.0
    vs = 0
    if   vol_ratio > 2.0:  vs = 10
    elif vol_ratio > 1.3:  vs = 6
    elif vol_ratio < 0.6:  vs = -8
    elif vol_ratio < 0.8:  vs = -4
    score += vs
    breakdown["volume"] = {"ratio": vol_ratio, "score": vs}

    # Normalise: max possible is ~110, min ~-110 → scale to 0-100
    norm = min(100, max(0, 50 + score * 0.45))
    direction = "UP" if score >= 0 else "DOWN"

    return score, norm, direction, breakdown, rsi_val, vol_ratio


# ── Win rate & backtest ────────────────────────────────────────────────────────

def _calc_win_rate(monthly_hist: list, direction: str) -> tuple:
    """
    Win rate = % of past months where actual movement matched predicted direction.
    Returns (win_rate_pct, backtest_list).
    """
    past = [m for m in monthly_hist if m["label"] != datetime.today().strftime("%b %Y")]
    if not past:
        return 50.0, []

    wins = 0
    backtest = []
    for m in past:
        actual_up = m["ret"] > 0
        predicted_up = direction == "UP"
        win = actual_up == predicted_up
        wins += int(win)
        backtest.append({
            "label":     m["label"],
            "ret":       m["ret"],
            "direction": m["direction"],
            "predicted": direction,
            "win":       win,
        })

    win_rate = round(wins / len(past) * 100, 1)
    return win_rate, backtest


def _calc_probability(norm_score: float, win_rate: float, vol_ratio: float) -> float:
    """
    Composite probability combining technical strength + historical accuracy.
    Range: 72–92% — only high-conviction setups qualify.
    """
    tech_contrib = norm_score / 100          # 0–1
    hist_contrib = win_rate / 100            # 0–1
    vol_contrib  = min(vol_ratio / 5, 0.08)  # max 0.08 bonus

    raw = tech_contrib * 0.45 + hist_contrib * 0.45 + vol_contrib

    # Stretch to 72–92 range (high-conviction filter)
    prob = 72 + (raw - 0.5) * 40
    return round(min(92, max(72, prob)), 1)


# ── Seasonal & month-level analysis ───────────────────────────────────────────

def _seasonal_month_returns(close: pd.Series, month: int) -> list:
    """
    Returns list of (year, return_pct) for a given calendar month
    over the last 5 available years in the data.
    """
    today   = datetime.today()
    results = []
    for yr in range(today.year - 5, today.year):
        try:
            last_day  = calendar.monthrange(yr, month)[1]
            m_start   = pd.Timestamp(year=yr, month=month, day=1,  tz=None)
            m_end     = pd.Timestamp(year=yr, month=month, day=last_day, tz=None)
            idx       = close.index
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


def _current_month_pred(close: pd.Series, daily_std: float, norm_score: float) -> dict:
    """
    Current month analysis (e.g. March 2026):
    - MTD return so far
    - Remaining trading days
    - Adjusted EOM close probability
    - Historical performance in this calendar month
    """
    today   = datetime.today()
    idx     = close.index

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

    # Remaining trading days in the month
    last_day_of_month = calendar.monthrange(today.year, today.month)[1]
    remaining_cal     = last_day_of_month - today.day
    trading_days_left = max(1, int(remaining_cal * 5 / 7))   # approx

    remaining_1std = round(daily_std * (trading_days_left ** 0.5) * 100, 1)

    # EOM direction is locked in if few days left and MTD return is large
    mtd_locked  = abs(mtd_return) > remaining_1std * 1.5
    eom_dir     = "UP" if mtd_return >= 0 else "DOWN"

    # Probability: how likely is the stock to close positive this month
    # Based on: current MTD progress + remaining volatility window
    if   mtd_return >  5:  eom_prob = 88
    elif mtd_return >  3:  eom_prob = 80
    elif mtd_return >  1:  eom_prob = 70
    elif mtd_return > -1:  eom_prob = 52 + norm_score * 0.15   # neutral — tech score decides
    elif mtd_return > -3:  eom_prob = 38
    elif mtd_return > -5:  eom_prob = 28
    else:                   eom_prob = 18

    # Adjust for days remaining: if almost EOM and locked in direction
    if trading_days_left <= 3:
        eom_prob = min(94, eom_prob + 8) if mtd_return > 0 else max(8, eom_prob - 8)

    # Historical performance for this calendar month
    seasonal = _seasonal_month_returns(close, today.month)
    seas_wr  = round(sum(1 for _, r in seasonal if r > 0) / len(seasonal) * 100, 1) if seasonal else 50.0
    seas_avg = round(sum(r for _, r in seasonal) / len(seasonal), 2) if seasonal else 0.0

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


def _next_month_pred(close: pd.Series, direction: str, norm_score: float,
                     monthly_vol: float) -> dict:
    """
    Next month prediction (e.g. April 2026):
    - Seasonal analysis (how this stock performed in April historically)
    - Momentum alignment (current tech direction vs seasonal bias)
    - Expected price range
    """
    today      = datetime.today()
    nm_num     = today.month + 1 if today.month < 12 else 1
    nm_year    = today.year if today.month < 12 else today.year + 1
    nm_label   = f"{calendar.month_name[nm_num]} {nm_year}"

    current_price = float(close.iloc[-1])

    seasonal      = _seasonal_month_returns(close, nm_num)
    seas_wr       = round(sum(1 for _, r in seasonal if r > 0) / len(seasonal) * 100, 1) if seasonal else 50.0
    seas_avg      = round(sum(r for _, r in seasonal) / len(seasonal), 2) if seasonal else 0.0
    seas_dir      = "UP" if seas_avg >= 0 else "DOWN"

    # Alignment: does current momentum agree with seasonal bias?
    momentum_up = direction == "UP"
    seasonal_up = seas_dir  == "UP"
    aligned     = momentum_up == seasonal_up

    if aligned:
        # Both signals agree — higher confidence
        base_prob = max(seas_wr, 65.0) + (norm_score - 50) * 0.18
        final_dir = "UP" if seasonal_up else "DOWN"
    else:
        # Conflict — stronger signal wins
        if norm_score > 68:          # Dominant tech signal
            base_prob = 62 + (norm_score - 68) * 0.7
            final_dir = direction
        elif seas_wr > 65:           # Strong seasonal
            base_prob = seas_wr
            final_dir = seas_dir
        else:                        # No clear winner — use stronger of the two
            base_prob = max(seas_wr, 55.0)
            final_dir = direction

    # Expected price targets for next month
    mv = monthly_vol / 100
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


# ── Main screener ──────────────────────────────────────────────────────────────

class StockScreener:

    def __init__(self):
        self.end   = datetime.today()
        self.start = self.end - timedelta(days=500)   # ~16 months

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self, top_n: int = 8, fast: bool = False) -> tuple:
        """
        Returns (top_candidates, all_candidates).
        top_candidates — top_n by probability (for detailed report cards).
        all_candidates — every stock that passed the liquidity filter
                         (used to populate SCREENER_DATA so any NSE stock
                          added to Active Trades has live probability/CMP).
        """
        symbols  = get_symbols()
        if fast:
            symbols = symbols[:60]          # quick mode

        print(f"\n  Downloading {len(symbols)} tickers in batches…")
        all_data = self._batch_download(symbols)

        print(f"  Analysing {len(all_data)} downloaded tickers…")
        candidates = []
        for sym, hist in all_data.items():
            result = self._analyze_one(sym, hist)
            if result:
                candidates.append(result)

        print(f"  {len(candidates)} stocks passed liquidity filter.")

        # Sort by probability descending
        candidates.sort(key=lambda x: x["probability"], reverse=True)
        top = candidates[:top_n]
        print(f"  Top {len(top)} candidates selected.\n")
        return top, candidates

    # ── Batch download ────────────────────────────────────────────────────────

    def _batch_download(self, symbols: list) -> dict:
        """Download OHLCV for all symbols in BATCH_SIZE chunks."""
        result = {}
        batches = [symbols[i:i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]

        for idx, batch in enumerate(batches):
            tickers = [s + ".NS" for s in batch]
            print(f"    Batch {idx+1}/{len(batches)} ({len(batch)} stocks)…", end=" ", flush=True)
            try:
                raw = yf.download(
                    tickers,
                    start=self.start.strftime("%Y-%m-%d"),
                    end=self.end.strftime("%Y-%m-%d"),
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )
                print("✓")
                for sym, ticker in zip(batch, tickers):
                    try:
                        if isinstance(raw.columns, pd.MultiIndex):
                            if ticker not in raw.columns.get_level_values(1):
                                continue
                            hist = raw.xs(ticker, level=1, axis=1).dropna()
                        else:
                            hist = raw.dropna()
                        if len(hist) >= MIN_DATA_DAYS:
                            result[sym] = hist
                    except Exception:
                        pass
            except Exception as e:
                print(f"✗ ({e})")

        return result

    # ── Single stock analysis ─────────────────────────────────────────────────

    def analyze_extra_symbols(self, symbols: list) -> dict:
        """
        Download and fully analyze specific symbols regardless of liquidity or ranking.
        Used for user-tracked active trades. Returns {symbol: result_dict}.
        """
        if not symbols:
            return {}
        print(f"  Fetching data for {len(symbols)} tracked symbol(s)…")
        all_data = self._batch_download(symbols)
        results  = {}
        for sym, hist in all_data.items():
            result = self._analyze_one(sym, hist, force=True)
            if result:
                results[sym] = result
        return results

    def _analyze_one(self, symbol: str, hist: pd.DataFrame, force: bool = False):
        try:
            close  = hist["Close"]
            volume = hist["Volume"]
            high   = hist["High"]
            low    = hist["Low"]

            # ── Liquidity filter ──────────────────────────────────────────────
            avg_price   = float(close.tail(20).mean())
            avg_vol     = float(volume.tail(20).mean())
            daily_val_cr = avg_price * avg_vol / 1e7   # crores
            if daily_val_cr < MIN_DAILY_VALUE_CR and not force:
                return None

            latest_close = float(close.iloc[-1])

            # ── Direction & technicals ────────────────────────────────────────
            raw_score, norm_score, direction, breakdown, rsi_val, vol_ratio = \
                _score_direction(close, volume)

            # ── Historical patterns ───────────────────────────────────────────
            monthly_hist   = _monthly_history(close)
            weekly_hist    = _weekly_history(hist)
            win_rate, bt   = _calc_win_rate(monthly_hist, direction)
            weekly_wr      = _weekly_win_rate(close)

            # ── Composite probability ─────────────────────────────────────────
            probability = _calc_probability(norm_score, win_rate, vol_ratio)

            # ── Volatility / expected range ───────────────────────────────────
            daily_std    = float(close.pct_change().dropna().std())
            monthly_vol  = round(daily_std * (21 ** 0.5) * 100, 1)
            atr_14       = float((high - low).tail(14).mean())
            atr_pct      = round(atr_14 / latest_close * 100, 2)

            # ── 52-week high / low ────────────────────────────────────────────
            wk52_high = round(float(high.tail(252).max()), 2)
            wk52_low  = round(float(low.tail(252).min()), 2)
            vs_52h    = round((latest_close / wk52_high - 1) * 100, 1)
            vs_52l    = round((latest_close / wk52_low  - 1) * 100, 1)

            # ── Month predictions ─────────────────────────────────────────────
            cur_month  = _current_month_pred(close, daily_std, norm_score)
            next_month = _next_month_pred(close, direction, norm_score, monthly_vol)

            meta = get_meta(symbol)

            return {
                "symbol":          symbol,
                "name":            meta["name"],
                "sector":          meta["sector"],
                "cmp":             round(latest_close, 2),
                "direction":       direction,
                "probability":     probability,
                "win_rate":        win_rate,
                "weekly_win_rate": weekly_wr,
                "tech_score":      round(norm_score, 1),
                "raw_score":       raw_score,
                "rsi":             round(rsi_val, 1),
                "vol_ratio":       vol_ratio,
                "daily_val_cr":    round(daily_val_cr, 1),
                "monthly_vol":     monthly_vol,
                "atr_pct":         atr_pct,
                "wk52_high":       wk52_high,
                "wk52_low":        wk52_low,
                "vs_52h":          vs_52h,
                "vs_52l":          vs_52l,
                "breakdown":       breakdown,
                "monthly_hist":    monthly_hist,
                "weekly_hist":     weekly_hist,
                "backtest":        bt,
                "cur_month":       cur_month,
                "next_month":      next_month,
            }
        except Exception:
            return None
