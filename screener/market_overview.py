"""
Market Overview Module v2 — Multi-Signal Probability Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Instruments: Nifty 50, MCX Crude Oil, MCX Natural Gas

Signals used per instrument:
  ┌────────────────────────┬────────┬────────┬──────────┐
  │ Signal                 │ Nifty  │ Crude  │ Nat Gas  │
  ├────────────────────────┼────────┼────────┼──────────┤
  │ Historical win rate    │  15 %  │  20 %  │  15 %    │
  │ Technical (RSI/MACD/   │  20 %  │  35 %  │  35 %    │
  │   Bollinger/SMA/ADX)   │        │        │          │
  │ India VIX level+trend  │  22 %  │  10 %  │  10 %    │
  │ NSE PCR (options)      │  22 %  │   —    │   —      │
  │ Max Pain pull          │  12 %  │   —    │   —      │
  │ Global (S&P 500/Nikkei)│   6 %  │  25 %  │  25 %    │
  │ FII net flow           │   3 %  │   —    │   —      │
  │ Seasonal demand bias   │   —    │  10 %  │  15 %    │
  └────────────────────────┴────────┴────────┴──────────┘

Probability range: 50 %–78 %
  No honest short-term model exceeds ~78 % directional accuracy.
  A 65 %+ aligned signal is worth considering for a trade.

Target prices:
  Nifty weekly — options ATM IV × √(DTE/365) when available, else ATR
  Crude/NatGas  — ATR × √week_num (time-scaled)
"""

import calendar
import logging
import math
import time
import warnings
warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from screener.levels import calc_key_levels

try:
    import requests as _req
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False


# ── Signal weights ─────────────────────────────────────────────────────────────

_NIFTY_W: Dict[str, float] = {
    "pcr_trend": 0.20,   # DB: PCR level + 3-day trend from market_snapshot
    "oi_change": 0.20,   # DB: net OI change direction from option_chain
    "max_pain":  0.18,   # DB/API: max pain strike gravitational pull
    "tech":      0.15,   # RSI + MACD + Bollinger + SMA + ADX from price
    "vix":       0.13,   # DB: India VIX from market_snapshot
    "hist_wr":   0.08,   # historical week-of-month win rate from price
    "iv_pct":    0.06,   # DB: IV percentile from option_chain.iv
}

_CRUDE_W: Dict[str, float] = {
    "oi_conf":  0.35,   # DB: price + OI confluence from mcx_ohlc
    "tech":     0.30,   # DB: RSI + MACD + BB + SMA from mcx_ohlc
    "volume":   0.15,   # DB: volume vs 20-day avg from mcx_ohlc
    "seasonal": 0.12,   # DB: empirical monthly return from mcx_ohlc history
    "hist_wr":  0.08,   # DB: historical monthly win rate from mcx_ohlc
}

_NATGAS_W: Dict[str, float] = {
    "oi_conf":  0.33,   # DB: price + OI confluence from mcx_ohlc
    "tech":     0.28,   # DB: RSI + MACD + BB + SMA from mcx_ohlc
    "seasonal": 0.20,   # DB: NatGas has very strong seasonal pattern
    "volume":   0.12,   # DB: volume confirmation
    "hist_wr":  0.07,   # DB: historical monthly win rate
}

# Week-level confidence decay: further weeks → probability pulled toward 50 %
_DECAY = {1: 1.00, 2: 0.85, 3: 0.70, 4: 0.55, 5: 0.40}

# Probability swing: score ±1 maps to 50 ± _PROB_SWING → range 22 %..78 %
_PROB_SWING = 28.0


# ── Download cache (cleared each run) ─────────────────────────────────────────

_DL_CACHE: Dict[str, pd.DataFrame] = {}


def _download(ticker: str, period: str = "3y") -> Optional[pd.DataFrame]:
    key = f"{ticker}:{period}"
    if key in _DL_CACHE:
        return _DL_CACHE[key]
    try:
        raw = yf.download(ticker, period=period, interval="1d",
                          progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw.index = pd.to_datetime(raw.index).tz_localize(None)
        df = raw.dropna(subset=["Close"])
        _DL_CACHE[key] = df
        return df
    except Exception:
        return None


def _usdinr() -> float:
    raw = _download("USDINR=X", period="5d")
    if raw is None:
        return 84.0
    try:
        rate = float(raw["Close"].dropna().iloc[-1])
        return rate if 70 < rate < 120 else 84.0
    except Exception:
        return 84.0


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
        nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        exp = calc_fn(nxt.year, nxt.month)
    return exp


# ── NSE India API ──────────────────────────────────────────────────────────────

_NSE_SESSION = None
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Connection":      "keep-alive",
}


def _nse_session():
    global _NSE_SESSION
    if _NSE_SESSION is not None:
        return _NSE_SESSION
    if not _REQUESTS_OK:
        return None
    try:
        s = _req.Session()
        s.headers.update(_NSE_HEADERS)
        s.get("https://www.nseindia.com", timeout=8)
        time.sleep(0.4)
        s.get("https://www.nseindia.com/option-chain", timeout=8)
        _NSE_SESSION = s
        return s
    except Exception:
        return None


def _nse_get(path: str) -> Optional[dict]:
    global _NSE_SESSION
    s = _nse_session()
    if s is None:
        return None
    try:
        r = s.get(f"https://www.nseindia.com{path}", timeout=15)
        if r.status_code in (401, 403):
            _NSE_SESSION = None
            s = _nse_session()
            if s is None:
                return None
            r = s.get(f"https://www.nseindia.com{path}", timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _nse_options_analysis(nifty_cmp: float) -> dict:
    """
    Fetch Nifty option chain from NSE and compute:
      - PCR (Put-Call OI Ratio) for nearest expiry
      - Max Pain strike
      - ATM Implied Volatility
      - Largest CE/PE OI strikes (gamma walls = key S/R levels)
    Returns {} on any failure — all callers must handle missing keys.
    """
    result: dict = {}
    if nifty_cmp <= 0:
        return result
    try:
        data = _nse_get("/api/option-chain-indices?symbol=NIFTY")
        if not data or "records" not in data:
            return result

        all_records  = data["records"].get("data", [])
        expiry_dates = data["records"].get("expiryDates", [])
        if not all_records or not expiry_dates:
            return result

        # Use nearest weekly expiry (first in sorted list)
        target_expiry = expiry_dates[0]
        records = [r for r in all_records
                   if r.get("expiryDate", "").strip() == target_expiry.strip()]
        if not records:
            return result

        ce_oi: Dict[float, float] = {}
        pe_oi: Dict[float, float] = {}
        ce_iv: Dict[float, float] = {}
        pe_iv: Dict[float, float] = {}
        total_ce_oi = total_pe_oi = 0.0

        for rec in records:
            strike = float(rec.get("strikePrice", 0))
            if "CE" in rec and rec["CE"]:
                oi = float(rec["CE"].get("openInterest", 0) or 0)
                ce_oi[strike] = oi
                ce_iv[strike] = float(rec["CE"].get("impliedVolatility", 0) or 0)
                total_ce_oi  += oi
            if "PE" in rec and rec["PE"]:
                oi = float(rec["PE"].get("openInterest", 0) or 0)
                pe_oi[strike] = oi
                pe_iv[strike] = float(rec["PE"].get("impliedVolatility", 0) or 0)
                total_pe_oi  += oi

        if total_ce_oi > 0:
            result["pcr"] = round(total_pe_oi / total_ce_oi, 3)

        # Max Pain: strike that minimises total intrinsic value paid out to buyers
        all_strikes = sorted(set(ce_oi) | set(pe_oi))
        if all_strikes:
            losses: Dict[float, float] = {}
            for s in all_strikes:
                ce_loss = sum(max(0.0, s - k) * v for k, v in ce_oi.items())
                pe_loss = sum(max(0.0, k - s) * v for k, v in pe_oi.items())
                losses[s] = ce_loss + pe_loss
            result["max_pain"] = float(min(losses, key=losses.get))

        # ATM IV: average of CE and PE IV at nearest strike to CMP
        if ce_iv and pe_iv:
            atm = min(all_strikes, key=lambda x: abs(x - nifty_cmp))
            c_iv, p_iv = ce_iv.get(atm, 0.0), pe_iv.get(atm, 0.0)
            if c_iv > 0 and p_iv > 0:
                result["atm_iv"] = round((c_iv + p_iv) / 2.0, 2)

        # Gamma walls — highest OI strikes (strong S/R)
        if ce_oi:
            result["resistance"] = float(max(ce_oi, key=ce_oi.get))
        if pe_oi:
            result["support"] = float(max(pe_oi, key=pe_oi.get))

    except Exception:
        pass
    return result


def _fii_net_signal() -> float:
    """
    FII net cash buying/selling (last 3 trading days) → signal -1..+1.
    Positive = net buyers → bullish.
    Returns 0.0 on failure.
    """
    try:
        data = _nse_get("/api/fiidiiTradeReact")
        if not data or not isinstance(data, list):
            return 0.0

        net_cr = 0.0
        count  = 0
        for day in data[:3]:
            if not isinstance(day, dict):
                continue
            for buy_key, sell_key in [
                ("fiiBuy",   "fiiSell"),
                ("fii_buy",  "fii_sell"),
                ("buyValue", "sellValue"),
            ]:
                buy  = float(day.get(buy_key,  0) or 0)
                sell = float(day.get(sell_key, 0) or 0)
                if buy > 0 or sell > 0:
                    net_cr += buy - sell
                    count  += 1
                    break

        if count == 0:
            return 0.0
        # ±9 000 Cr total (3 days × ±3 000 Cr/day) maps to ±1.0
        return round(max(-1.0, min(1.0, net_cr / 9_000.0)), 3)
    except Exception:
        return 0.0


# ── Signal generators ──────────────────────────────────────────────────────────

def _hist_wr_to_signal(win_rate: float) -> float:
    """Win rate 0..100 → signal -1..+1. 50 % → 0.0."""
    return max(-1.0, min(1.0, (win_rate - 50.0) / 50.0))


def _enhanced_tech_score(df: pd.DataFrame) -> float:
    """
    Technical signal -1..+1 from:
      RSI-14            30 %
      MACD histogram    25 %
      Bollinger Bands   20 %
      SMA trend (20/50) 15 %
      ADX modifier      10 %  (amplifies/dampens in trending vs. ranging markets)
    """
    close = df["Close"].dropna()
    if len(close) < 50:
        return _simple_tech_score(df)

    # ── RSI-14 ──────────────────────────────────────────────────────────────
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=True, min_periods=14).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=True, min_periods=14).mean()
    rsi_s = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).dropna()
    rsi   = float(rsi_s.iloc[-1]) if not rsi_s.empty else 50.0

    if   rsi < 25: rsi_c = +1.0
    elif rsi < 35: rsi_c = +0.7
    elif rsi < 45: rsi_c = +0.3
    elif rsi < 55: rsi_c = -0.1
    elif rsi < 65: rsi_c = -0.5
    elif rsi < 75: rsi_c = -0.8
    else:          rsi_c = -1.0

    # ── MACD histogram (normalised) ─────────────────────────────────────────
    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    mh     = float(hist.dropna().iloc[-1])
    mh_p   = float(hist.dropna().iloc[-2]) if len(hist.dropna()) >= 2 else mh
    vol    = (float(close.pct_change().rolling(20).std().dropna().iloc[-1])
              * float(close.iloc[-1]))
    mh_n   = max(-1.0, min(1.0, mh / (vol * 0.5 + 1e-9)))
    if abs(mh) > abs(mh_p):          # histogram expanding = trend strengthening
        mh_n = max(-1.0, min(1.0, mh_n * 1.15))

    # ── Bollinger Bands (20, 2σ) ─────────────────────────────────────────────
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    upper = (sma20 + 2 * std20).dropna()
    lower = (sma20 - 2 * std20).dropna()
    curr  = float(close.iloc[-1])
    u, l  = float(upper.iloc[-1]), float(lower.iloc[-1])
    bw    = u - l
    if bw > 0:
        pos = (curr - l) / bw          # 0 = at lower band, 1 = at upper band
        if   pos < 0.10: bb_c = +1.0
        elif pos < 0.25: bb_c = +0.6
        elif pos < 0.40: bb_c = +0.2
        elif pos < 0.60: bb_c = -0.1
        elif pos < 0.75: bb_c = -0.4
        elif pos < 0.90: bb_c = -0.7
        else:            bb_c = -1.0
    else:
        bb_c = 0.0

    # ── SMA trend (golden/death cross zone) ─────────────────────────────────
    s20   = float(sma20.dropna().iloc[-1])
    s50_s = close.rolling(50).mean().dropna()
    s50   = float(s50_s.iloc[-1]) if not s50_s.empty else s20
    if   curr > s20 > s50: sma_c = +0.8    # strong uptrend
    elif curr > s20:       sma_c = +0.4
    elif curr > s50:       sma_c =  0.0    # between MAs
    elif s20 > s50:        sma_c = -0.3    # below ST but LT uptrend
    else:                  sma_c = -0.8    # full downtrend

    # ── ADX — trend strength modifier ───────────────────────────────────────
    adx_mod = 1.0
    di_adj  = 0.0
    try:
        if "High" in df.columns and "Low" in df.columns:
            hi, lo  = df["High"].dropna(), df["Low"].dropna()
            pc      = close.shift(1)
            tr      = pd.concat([hi - lo, (hi - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
            hd, ld  = hi.diff(), -lo.diff()
            pdm     = hd.where((hd > ld) & (hd > 0), 0.0)
            mdm     = ld.where((ld > hd) & (ld > 0), 0.0)
            atr14   = tr.ewm(span=14, adjust=False).mean()
            pdi     = 100 * pdm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan)
            mdi     = 100 * mdm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan)
            dx      = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
            adx_val = float(dx.ewm(span=14, adjust=False).mean().dropna().iloc[-1])
            pdi_v   = float(pdi.dropna().iloc[-1])
            mdi_v   = float(mdi.dropna().iloc[-1])
            if   adx_val > 30: adx_mod = 1.25   # strong trend — trust directional signals
            elif adx_val > 25: adx_mod = 1.10
            elif adx_val < 15: adx_mod = 0.75   # ranging — mean reversion dominates
            if adx_val > 25:
                di_adj = 0.25 if pdi_v > mdi_v else -0.25
    except Exception:
        pass

    raw = rsi_c * 0.30 + mh_n * 0.25 + bb_c * 0.20 + sma_c * 0.15 + di_adj * 0.10
    return round(max(-1.0, min(1.0, raw * adx_mod)), 4)


def _simple_tech_score(df: pd.DataFrame) -> float:
    """Fallback for short data series (< 50 bars)."""
    close = df["Close"].dropna()
    if len(close) < 30:
        return 0.0
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=True, min_periods=14).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=True, min_periods=14).mean()
    rsi_s = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).dropna()
    rsi   = float(rsi_s.iloc[-1]) if not rsi_s.empty else 50.0
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    hist  = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    mh    = float(hist.dropna().iloc[-1])
    vol   = (float(close.pct_change().rolling(20).std().dropna().iloc[-1])
             * float(close.iloc[-1]))
    mh_n  = max(-1.0, min(1.0, mh / (vol * 0.5 + 1e-9)))
    sma20 = float(close.rolling(20).mean().dropna().iloc[-1])
    sma_c = 0.5 if float(close.iloc[-1]) > sma20 else -0.5
    if   rsi < 30: rsi_c = +1.0
    elif rsi < 45: rsi_c = +0.5
    elif rsi < 55: rsi_c = -0.1
    elif rsi < 65: rsi_c = -0.5
    else:          rsi_c = -1.0
    return round(max(-1.0, min(1.0, rsi_c * 0.40 + mh_n * 0.35 + sma_c * 0.25)), 4)


def _india_vix_signal() -> float:
    """
    India VIX level + trend → signal -1..+1.
    High / rising VIX = fear = bearish near-term for Nifty.
    Low  / falling VIX = complacency = bullish near-term.
    """
    df = _download("^INDIAVIX", period="3mo")
    if df is None or df.empty:
        return 0.0
    try:
        close = df["Close"].dropna()
        vix   = float(close.iloc[-1])
        sma10 = float(close.rolling(10).mean().dropna().iloc[-1])
        vix5d = float(close.iloc[-6]) if len(close) >= 6 else vix

        # Level signal
        if   vix < 11: lvl = +0.70
        elif vix < 14: lvl = +0.40
        elif vix < 17: lvl = +0.10
        elif vix < 20: lvl = -0.30
        elif vix < 25: lvl = -0.60
        else:          lvl = -0.90

        # Trend vs 10-day SMA
        trend = +0.40 if vix < sma10 else -0.40

        # 5-day rate of change
        roc = (vix - vix5d) / (vix5d + 1e-9) * 100
        if   roc >  15: roc_s = -0.60
        elif roc >   8: roc_s = -0.30
        elif roc < -15: roc_s = +0.60
        elif roc <  -8: roc_s = +0.30
        else:           roc_s =  0.00

        sig = lvl * 0.50 + trend * 0.30 + roc_s * 0.20
        return round(max(-1.0, min(1.0, sig)), 3)
    except Exception:
        return 0.0


def _global_sentiment_signal() -> float:
    """
    S&P 500 (70 %) + Nikkei 225 (30 %) recent performance.
    Nifty has ~0.65 rolling correlation with S&P 500 on weekly timeframe.
    """
    try:
        spx  = _download("^GSPC", period="1mo")
        n225 = _download("^N225", period="1mo")

        spx_sig = 0.0
        if spx is not None and len(spx) >= 6:
            c   = spx["Close"].dropna()
            r5  = (float(c.iloc[-1]) - float(c.iloc[-6])) / float(c.iloc[-6]) * 100
            r1  = (float(c.iloc[-1]) - float(c.iloc[-2])) / float(c.iloc[-2]) * 100
            spx_sig = (max(-1.0, min(1.0, r5 / 5.0)) * 0.60 +
                       max(-1.0, min(1.0, r1 / 2.0)) * 0.40)

        nky_sig = 0.0
        if n225 is not None and len(n225) >= 6:
            c   = n225["Close"].dropna()
            r5  = (float(c.iloc[-1]) - float(c.iloc[-6])) / float(c.iloc[-6]) * 100
            nky_sig = max(-1.0, min(1.0, r5 / 5.0))

        return round(max(-1.0, min(1.0, spx_sig * 0.70 + nky_sig * 0.30)), 3)
    except Exception:
        return 0.0


def _pcr_to_signal(pcr: float) -> float:
    """
    PCR (Put-Call OI Ratio) → directional signal -1..+1.
    Contrarian reading: high PCR = extreme put buying = oversold = bullish.
    """
    if   pcr > 1.80: return +0.80
    elif pcr > 1.40: return +0.50
    elif pcr > 1.10: return +0.20
    elif pcr > 0.90: return  0.00
    elif pcr > 0.70: return -0.30
    elif pcr > 0.50: return -0.60
    else:            return -0.90


def _max_pain_to_signal(max_pain: float, cmp: float) -> float:
    """
    Max pain pull: price gravitates toward max pain near expiry.
    Positive = max pain above CMP → bullish pull.
    """
    if cmp <= 0:
        return 0.0
    diff_pct = (max_pain - cmp) / cmp * 100
    if   diff_pct >  3.0: return +0.90
    elif diff_pct >  1.5: return +0.50
    elif diff_pct >  0.5: return +0.20
    elif diff_pct > -0.5: return  0.00
    elif diff_pct > -1.5: return -0.20
    elif diff_pct > -3.0: return -0.50
    else:                 return -0.90


def _seasonal_signal(commodity: str, today: date) -> float:
    """
    Demand/supply seasonal bias for MCX commodities.
    Crude: strong Mar–Jun (summer driving season in US/EU); weak Oct–Jan.
    Nat Gas: strong Oct–Feb (winter heating demand); weak Apr–Aug.
    """
    m = today.month
    if commodity == "crude":
        if   m in (3, 4, 5, 6): return +0.30
        elif m in (7, 8):       return +0.05
        elif m in (9, 10):      return -0.15
        else:                   return -0.30   # Nov, Dec, Jan, Feb
    elif commodity == "natgas":
        if   m in (11, 12, 1, 2): return +0.40
        elif m in (3, 10):        return +0.15
        elif m in (4, 9):         return -0.10
        else:                     return -0.40   # May–Aug injection season
    return 0.0


# ── DB-based signal functions (no yfinance, no external APIs) ─────────────────

def _db_vix_signal() -> float:
    """India VIX from market_snapshot. Same scale as _india_vix_signal()."""
    from screener.db import _get_conn, is_available as _ok
    if not _ok():
        return 0.0
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT vix FROM market_snapshot
                    WHERE instrument = 'NIFTY' AND vix IS NOT NULL
                    ORDER BY ts DESC LIMIT 1
                """)
                row = cur.fetchone()
        if not row or not row[0]:
            return 0.0
        vix = float(row[0])
        if   vix < 11: return +0.70
        elif vix < 14: return +0.40
        elif vix < 17: return +0.10
        elif vix < 20: return -0.30
        elif vix < 25: return -0.60
        else:          return -0.90
    except Exception:
        return 0.0


def _db_pcr_trend_signal() -> float:
    """
    PCR level + 3-day trend from market_snapshot.
    Rising PCR = more put buying = hedging = bullish for underlying.
    Returns -1..+1.
    """
    from screener.db import _get_conn, is_available as _ok
    if not _ok():
        return 0.0
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT pcr_oi FROM market_snapshot
                    WHERE instrument = 'NIFTY' AND pcr_oi IS NOT NULL
                    ORDER BY ts DESC LIMIT 40
                """)
                rows = [float(r[0]) for r in cur.fetchall()]
        if not rows:
            return 0.0

        latest = rows[0]
        if   latest > 1.5: level = +0.90
        elif latest > 1.2: level = +0.60
        elif latest > 1.0: level = +0.30
        elif latest > 0.8: level = -0.20
        elif latest > 0.6: level = -0.60
        else:              level = -0.90

        trend = 0.0
        if len(rows) >= 12:
            older = rows[12:24] if len(rows) >= 24 else rows[6:]
            older_avg = sum(older) / len(older)
            delta = (latest - older_avg) / (abs(older_avg) + 1e-9)
            if   delta >  0.15: trend = +0.50
            elif delta >  0.05: trend = +0.25
            elif delta < -0.15: trend = -0.50
            elif delta < -0.05: trend = -0.25

        return round(max(-1.0, min(1.0, level * 0.60 + trend * 0.40)), 3)
    except Exception:
        return 0.0


def _db_oi_change_signal(expiry: "date") -> float:
    """
    Net OI change direction from option_chain (last trading session).
    PE OI adding + CE OI reducing → bulls defending → bullish.
    CE OI adding + PE OI reducing → bears pressing → bearish.

    Uses 3-day window because screener runs at 9 AM IST (before market),
    so yesterday's session (9:14 AM–3:30 PM) needs a wider lookback.
    """
    from screener.db import _get_conn, is_available as _ok
    if not _ok() or expiry is None:
        return 0.0
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT option_type, SUM(oi_change) AS net
                    FROM option_chain
                    WHERE instrument = 'NIFTY'
                      AND expiry = %s
                      AND oi_change IS NOT NULL
                      AND ts >= NOW() - INTERVAL '3 days'
                    GROUP BY option_type
                """, (expiry,))
                data = {r[0]: float(r[1]) for r in cur.fetchall() if r[1] is not None}
        ce = data.get("CE", 0.0)
        pe = data.get("PE", 0.0)
        total = abs(ce) + abs(pe)
        if total < 100:
            return 0.0
        # pe - ce > 0 means puts are being added relative to calls → bullish
        raw = (pe - ce) / (total + 1e-9)
        return round(max(-1.0, min(1.0, raw * 1.5)), 3)
    except Exception:
        return 0.0


def _db_iv_percentile_signal(expiry: "date", atm: int) -> float:
    """
    ATM IV percentile vs 52-week range from option_chain.iv.
    High percentile (>75%) → expensive options → mean reversion bias (slight bearish directional).
    Low percentile (<25%) → compressed → trend continuation more likely.
    """
    from screener.db import _get_conn, is_available as _ok
    if not _ok() or expiry is None:
        return 0.0
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT AVG(iv) FROM option_chain
                    WHERE instrument = 'NIFTY' AND expiry = %s
                      AND strike = %s AND iv > 0
                      AND ts >= NOW() - INTERVAL '4 days'
                """, (expiry, atm))
                r = cur.fetchone()
                current_iv = float(r[0]) if r and r[0] else None
                if current_iv is None:
                    return 0.0
                cur.execute("""
                    SELECT MIN(iv), MAX(iv) FROM option_chain
                    WHERE instrument = 'NIFTY' AND strike = %s
                      AND iv > 0
                      AND ts >= NOW() - INTERVAL '365 days'
                """, (atm,))
                r = cur.fetchone()
                if not r or not r[0] or not r[1]:
                    return 0.0
                iv_min, iv_max = float(r[0]), float(r[1])
        if iv_max <= iv_min:
            return 0.0
        pct = (current_iv - iv_min) / (iv_max - iv_min) * 100
        if   pct > 80: return -0.40
        elif pct > 65: return -0.20
        elif pct > 45: return  0.00
        elif pct > 25: return +0.15
        else:          return +0.30
    except Exception:
        return 0.0


def _db_max_pain_signal(expiry: "date", cmp: float) -> Optional[float]:
    """
    Compute max pain from option_chain OI (DB-native, no NSE API needed).
    Returns the max_pain_to_signal value, or None if data unavailable.
    """
    from screener.db import _get_conn, is_available as _ok
    if not _ok() or expiry is None or cmp <= 0:
        return None
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT strike, option_type, MAX(oi) AS max_oi
                    FROM option_chain
                    WHERE instrument = 'NIFTY' AND expiry = %s AND oi > 0
                    GROUP BY strike, option_type
                """, (expiry,))
                rows = cur.fetchall()
        if not rows:
            return None
        ce_oi: Dict[float, float] = {}
        pe_oi: Dict[float, float] = {}
        for strike, opt_type, oi in rows:
            if opt_type == "CE":
                ce_oi[float(strike)] = float(oi)
            elif opt_type == "PE":
                pe_oi[float(strike)] = float(oi)
        all_strikes = sorted(set(ce_oi) | set(pe_oi))
        if not all_strikes:
            return None
        losses: Dict[float, float] = {}
        for s in all_strikes:
            ce_loss = sum(max(0.0, s - k) * v for k, v in ce_oi.items())
            pe_loss = sum(max(0.0, k - s) * v for k, v in pe_oi.items())
            losses[s] = ce_loss + pe_loss
        max_pain = float(min(losses, key=losses.get))
        return _max_pain_to_signal(max_pain, cmp)
    except Exception:
        return None


# ── MCX DataFrame signals (operate on already-loaded mcx_ohlc DataFrame) ───────

def _mcx_oi_confluence(df: "pd.DataFrame") -> float:
    """
    Price + OI 5-bar confluence — most reliable futures directional signal.
      Price UP  + OI UP   → new longs entering    → strong bull (+0.80)
      Price UP  + OI DOWN → short covering rally   → weak   bull (+0.30)
      Price DOWN + OI UP  → new shorts entering    → strong bear (-0.80)
      Price DOWN + OI DOWN → long liquidation      → weak   bear (-0.30)
    """
    oi_col = next((c for c in ("oi", "OI") if c in df.columns), None)
    if oi_col is None or len(df) < 5:
        return 0.0
    try:
        tail = df[["Close", oi_col]].dropna().tail(5)
        if len(tail) < 2:
            return 0.0
        price_chg = float(tail["Close"].iloc[-1]) - float(tail["Close"].iloc[0])
        oi_chg    = float(tail[oi_col].iloc[-1])  - float(tail[oi_col].iloc[0])
        p = 1 if price_chg > 0 else (-1 if price_chg < 0 else 0)
        o = 1 if oi_chg    > 0 else (-1 if oi_chg    < 0 else 0)
        if p == 0:         return  0.00
        if p ==  1 and o ==  1: return +0.80
        if p ==  1 and o == -1: return +0.30
        if p == -1 and o ==  1: return -0.80
        if p == -1 and o == -1: return -0.30
    except Exception:
        pass
    return 0.0


def _mcx_volume_signal(df: "pd.DataFrame") -> float:
    """Volume vs 20-day average — confirms or weakens price direction."""
    vol_col = next((c for c in ("volume", "Volume") if c in df.columns), None)
    if vol_col is None or len(df) < 20:
        return 0.0
    try:
        avg_vol  = float(df[vol_col].tail(20).mean())
        curr_vol = float(df[vol_col].iloc[-1])
        if avg_vol <= 0:
            return 0.0
        ratio     = curr_vol / avg_vol
        close     = df["Close"].dropna()
        price_dir = 1 if float(close.iloc[-1]) > float(close.iloc[-2]) else -1
        if   ratio > 1.5: return round(price_dir * 0.70, 2)
        elif ratio > 1.2: return round(price_dir * 0.40, 2)
        elif ratio < 0.7: return round(price_dir * 0.10, 2)
        else:             return round(price_dir * 0.20, 2)
    except Exception:
        return 0.0



# ── Expert seasonal priors for energy commodities ─────────────────────────────
# Used ONLY as fallback when DB lacks ≥2 years of per-month history.
# Based on well-established demand cycles:
#   Crude: weak Apr (shoulder season, refineries switching), strong Jun-Aug (driving),
#          strong Oct-Nov (winter stocking).
#   NatGas: very weak Apr-Sep (injection season, low demand),
#            very strong Nov-Feb (winter heating demand).
# Scaled to ±0.35 max — weaker than DB-derived signal so new data overrides quickly.
_MCX_SEASONAL_PRIOR: Dict[str, Dict[int, float]] = {
    "CRUDEOIL": {
        1: +0.10, 2: +0.05, 3: -0.10, 4: -0.25,   # Jan-Apr
        5: +0.05, 6: +0.20, 7: +0.25, 8: +0.20,   # May-Aug (driving season)
        9: -0.05, 10: +0.15, 11: +0.10, 12: -0.05, # Sep-Dec
    },
    "NATURALGAS": {
        1: +0.30, 2: +0.20, 3: -0.20, 4: -0.35,   # Jan-Apr
        5: -0.25, 6: -0.15, 7: -0.10, 8: +0.05,   # May-Aug
        9: +0.10, 10: +0.20, 11: +0.35, 12: +0.35, # Sep-Dec (winter demand)
    },
}


def _mcx_seasonal_from_db(symbol: str) -> float:
    """
    Empirical seasonal signal: average monthly return for today's calendar month.

    Primary: computed from full mcx_ohlc history (requires ≥2 data points per month).
    Fallback: expert-calibrated seasonal prior for energy commodities — used at
              60 % weight when DB history is insufficient.  As DB accumulates data,
              the DB-derived signal automatically takes over.
    """
    from screener.db import _get_conn, is_available as _ok

    current_month = date.today().month

    # ── DB-derived signal ─────────────────────────────────────────────────────
    db_signal = None
    if _ok():
        try:
            with _get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        WITH monthly AS (
                            SELECT
                                DATE_TRUNC('month', ts)::date                  AS m,
                                (ARRAY_AGG(close ORDER BY ts ASC))[1]          AS m_open,
                                (ARRAY_AGG(close ORDER BY ts DESC))[1]         AS m_close
                            FROM mcx_ohlc
                            WHERE instrument = %s AND interval = 'day'
                              AND ts < DATE_TRUNC('month', CURRENT_DATE)
                            GROUP BY DATE_TRUNC('month', ts)
                        )
                        SELECT EXTRACT(month FROM m)::int AS mnum,
                               AVG((m_close - m_open) / NULLIF(m_open, 0) * 100) AS avg_ret,
                               COUNT(*) AS months
                        FROM monthly
                        WHERE m_open > 0
                        GROUP BY EXTRACT(month FROM m)
                        HAVING COUNT(*) >= 2
                    """, (symbol,))
                    rows = {int(r[0]): (float(r[1]), int(r[2])) for r in cur.fetchall()}

            if current_month in rows:
                avg_ret, n_months = rows[current_month]
                raw = max(-1.0, min(1.0, avg_ret / 6.25))
                # Confidence scales from 0.6 (at 2 months) → 1.0 (at 10+ months)
                conf = min(1.0, 0.60 + (n_months - 2) * 0.05)
                db_signal = round(raw * conf, 3)
        except Exception as exc:
            logger.debug("_mcx_seasonal_from_db %s: %s", symbol, exc)

    if db_signal is not None:
        return db_signal

    # ── Expert seasonal prior (fallback) ──────────────────────────────────────
    prior = _MCX_SEASONAL_PRIOR.get(symbol, {})
    return round(prior.get(current_month, 0.0), 3)


# ── Probability engine ─────────────────────────────────────────────────────────

def _multi_signal_prob(
    signals: Dict[str, float],
    weights: Dict[str, float],
    time_decay: float = 1.0,
) -> Tuple[str, float]:
    """
    Compute (direction, probability%) from multiple -1..+1 signals.

    score = Σ signal_i × weight_i  (normalised by present-signal weights)
    prob  = 50 + score × _PROB_SWING

    time_decay pulls score toward 0 for further-out weeks (higher uncertainty).
    """
    present = {k: v for k, v in signals.items() if k in weights}
    total_w = sum(weights[k] for k in present)
    if total_w < 0.01:
        return "UP", 50.0

    score = sum(present[k] * weights[k] for k in present) / total_w
    score = max(-1.0, min(1.0, score * time_decay))

    prob_up = 50.0 + score * _PROB_SWING
    prob_up = max(22.0, min(78.0, prob_up))

    if prob_up >= 50.0:
        return "UP", round(prob_up, 1)
    else:
        return "DOWN", round(100.0 - prob_up, 1)


# ── Weekly ATR and options-based expected move ─────────────────────────────────

def _weekly_atr(df: pd.DataFrame) -> float:
    close = df["Close"]
    if "High" in df.columns and "Low" in df.columns:
        hi, lo = df["High"], df["Low"]
        tr = pd.concat([
            hi - lo,
            (hi - close.shift()).abs(),
            (lo - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean().dropna()
        if not atr14.empty:
            return float(atr14.iloc[-1]) * math.sqrt(5)
    diff = close.diff().abs().rolling(14).mean().dropna()
    return float(diff.iloc[-1]) * 2.5 if not diff.empty else float(close.iloc[-1]) * 0.02


def _options_expected_move(cmp: float, atm_iv_pct: float, dte: int) -> float:
    """
    1-sigma expected move from ATM Implied Volatility.
    Far more accurate than ATR for derivatives (Nifty).
    atm_iv_pct: annualised IV in % (e.g. 14.5 → 14.5 %)
    dte: calendar days to expiry
    """
    if atm_iv_pct <= 0 or dte <= 0:
        return 0.0
    return cmp * (atm_iv_pct / 100.0) * math.sqrt(dte / 365.0)


# ── Historical win rates ────────────────────────────────────────────────────────

def _week_num_in_month(d: date, expiry_wd: int) -> int:
    return sum(1 for day in range(1, d.day + 1)
               if date(d.year, d.month, day).weekday() == expiry_wd)


def _weekly_win_rates(df: pd.DataFrame, expiry_wd: int) -> dict:
    """
    For each week-of-month position 1..5:
      win rate = % of historical expiry days where close > week-open.
    Uses boolean-mask slicing — tz-safe.
    """
    buckets: Dict[int, list] = {1: [], 2: [], 3: [], 4: [], 5: []}
    today_ts = pd.Timestamp(date.today())
    df2 = df.copy()
    df2["_wd"] = df2.index.weekday

    expiry_mask = (df2["_wd"] == expiry_wd) & (df2.index < today_ts)
    for ts in df2.index[expiry_mask]:
        d   = ts.date()
        mon = d - timedelta(days=d.weekday())
        if mon.month != d.month:
            mon = date(d.year, d.month, 1)
        week_df = df2[(df2.index >= pd.Timestamp(mon)) & (df2.index <= ts)]
        if week_df.empty:
            continue
        try:
            o = float(week_df["Open"].iloc[0])
            c = float(week_df["Close"].iloc[-1])
        except Exception:
            continue
        if o <= 0:
            continue
        wk = _week_num_in_month(d, expiry_wd)
        if wk in buckets:
            buckets[wk].append(1 if c > o else 0)

    result: dict = {}
    for wk, vals in buckets.items():
        n = len(vals)
        if n >= 8:
            result[wk] = round(sum(vals) / n * 100, 1)
        elif n >= 4:
            raw = sum(vals) / n * 100
            result[wk] = round(raw * 0.70 + 50.0 * 0.30, 1)
        else:
            result[wk] = 52.0
    return result


def _monthly_win_rate(df: pd.DataFrame) -> float:
    monthly = df["Close"].resample("ME").agg(["first", "last"]).dropna()
    if len(monthly) < 6:
        return 55.0
    return round(float((monthly["last"] > monthly["first"]).sum()) / len(monthly) * 100, 1)


def _monthly_expected_move(df: pd.DataFrame) -> float:
    monthly = df["Close"].resample("ME").agg(["first", "last"]).dropna()
    if len(monthly) < 3:
        return 0.0
    return float((monthly["last"] - monthly["first"]).abs().tail(18).mean())


def _recent_week_momentum(df: pd.DataFrame) -> float:
    """Last completed week close vs open: +1 (bullish), -1 (bearish), 0 (flat)."""
    today    = date.today()
    last_fri = today - timedelta(days=(today.weekday() - 4) % 7 + 7)
    last_mon = last_fri - timedelta(days=4)
    week_df  = df[(df.index >= pd.Timestamp(last_mon)) & (df.index <= pd.Timestamp(last_fri))]
    if week_df.empty or len(week_df) < 3:
        return 0.0
    try:
        o = float(week_df["Open"].iloc[0])
        c = float(week_df["Close"].iloc[-1])
        pct = (c - o) / o * 100 if o else 0.0
        return +1.0 if pct > 0.3 else (-1.0 if pct < -0.3 else 0.0)
    except Exception:
        return 0.0


# ── Weekly schedule builder ────────────────────────────────────────────────────

def _weekly_schedule(
    df: pd.DataFrame,
    cmp: float,
    signals_base: Dict[str, float],
    weights: Dict[str, float],
    expiry_wd: int,
    monthly_exp: date,
    weekly_wr: dict,
    w_atr: float,
    recent_momentum: float,
    nse_opts: dict,
    monthly_exp_move: float = 0.0,   # pre-computed monthly expected move for consistency
) -> List[dict]:
    today       = date.today()
    year, month = monthly_exp.year, monthly_exp.month
    expiry_days = _weekdays_in_month(year, month, expiry_wd)
    result      = []

    if "max_pain" in nse_opts:
        mp_base_sig = _max_pain_to_signal(nse_opts["max_pain"], cmp)
    elif "_max_pain_sig" in nse_opts:
        mp_base_sig = float(nse_opts["_max_pain_sig"])
    else:
        mp_base_sig = 0.0

    for i, exp_date in enumerate(expiry_days):
        week_num   = i + 1
        mon        = exp_date - timedelta(days=exp_date.weekday())
        if mon.month != month:
            mon    = date(year, month, 1)
        is_past    = exp_date < today
        is_current = (not is_past) and (mon <= today <= exp_date)
        is_monthly = (exp_date == monthly_exp)

        wr    = weekly_wr.get(week_num, 52.0)
        decay = _DECAY.get(week_num, 0.40)

        # Per-week signals
        sigs = dict(signals_base)
        sigs["hist_wr"] = _hist_wr_to_signal(wr)

        # Blend recent momentum into tech (decays week by week)
        mom_w = max(0.0, 1.0 - (week_num - 1) * 0.30)
        if "tech" in sigs:
            sigs["tech"] = max(-1.0, min(1.0,
                                         sigs["tech"] + recent_momentum * 0.15 * mom_w))

        # Max pain pull degrades for further-out weeks
        if "max_pain" in weights and mp_base_sig != 0.0:
            mp_decay      = max(0.0, 1.0 - (week_num - 1) * 0.50)
            sigs["max_pain"] = mp_base_sig * mp_decay

        direction, probability = _multi_signal_prob(sigs, weights, time_decay=decay)

        # Expected move: for monthly expiry week use the same monthly_exp_move
        # so weekly and monthly targets are always consistent.
        # For other weeks: prefer ATM IV, fall back to scaled ATR.
        dte      = max(1, (exp_date - today).days)
        exp_move = 0.0
        if is_monthly and monthly_exp_move > 0:
            exp_move = monthly_exp_move
        elif nse_opts.get("atm_iv"):
            exp_move = _options_expected_move(cmp, nse_opts["atm_iv"], dte)
        if exp_move <= 0:
            exp_move = w_atr * math.sqrt(week_num)

        target   = round(cmp + exp_move if direction == "UP" else cmp - exp_move, 2)
        range_hi = round(target + exp_move * 0.35, 2)
        range_lo = round(target - exp_move * 0.35, 2)

        # Actual result for past weeks
        actual_close = actual_dir = actual_pct = None
        if is_past:
            wdf = df[(df.index >= pd.Timestamp(mon)) & (df.index <= pd.Timestamp(exp_date))]
            if not wdf.empty:
                try:
                    wo = float(wdf["Open"].iloc[0])
                    wc = float(wdf["Close"].iloc[-1])
                    actual_close = round(wc, 2)
                    actual_dir   = "UP" if wc >= wo else "DOWN"
                    actual_pct   = round((wc - wo) / wo * 100, 2) if wo else 0.0
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
    signals_base: Dict[str, float],
    weights: Dict[str, float],
    monthly_exp: date,
    nse_opts: dict,
) -> dict:
    today   = date.today()
    m_wr    = _monthly_win_rate(df)
    sigs    = dict(signals_base)
    sigs["hist_wr"] = _hist_wr_to_signal(m_wr)
    if "max_pain" in weights and "max_pain" in nse_opts:
        sigs["max_pain"] = _max_pain_to_signal(nse_opts["max_pain"], cmp) * 0.60
    elif "max_pain" in weights and "_max_pain_sig" in nse_opts:
        sigs["max_pain"] = float(nse_opts["_max_pain_sig"]) * 0.60

    direction, probability = _multi_signal_prob(sigs, weights, time_decay=0.80)

    m_move = _monthly_expected_move(df)
    dte    = max(1, (monthly_exp - today).days)
    # For Nifty: blend historical move with IV-derived expected move
    if nse_opts.get("atm_iv"):
        iv_move = _options_expected_move(cmp, nse_opts["atm_iv"], dte)
        if iv_move > 0:
            m_move = m_move * 0.40 + iv_move * 0.60  # IV is forward-looking, weight more

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
        "days_to_expiry": dte,
        "direction":      direction,
        "probability":    probability,
        "expected_price": expected,
        "bull_target":    bull_target,
        "bear_target":    bear_target,
        "hist_wr":        m_wr,
        "_exp_move":      round(m_move, 2),   # internal — passed to weekly schedule
    }


# ── Per-instrument analysis ────────────────────────────────────────────────────

def _analyze_instrument(
    ticker: str,
    name: str,
    fx_mult: float,
    expiry_wd: int,
    monthly_expiry_fn,
    note: str,
    weights: Dict[str, float],
    extra_signals: Dict[str, float] = None,
    nse_opts: dict                  = None,
) -> dict:
    """
    extra_signals: pre-computed signals dict (keys matching weights).
                   max_pain handled separately (applied with decay per-week).
    nse_opts:      NSE options data (pcr, max_pain raw price, atm_iv, etc.)
                   Used for target-price computation (ATM IV) and max_pain fallback.
    """
    if nse_opts is None:
        nse_opts = {}
    today = date.today()

    df_raw = _download(ticker, period="3y")
    if df_raw is None or len(df_raw) < 60:
        return {"name": name, "cmp": None, "note": note, "unit": "₹",
                "weekly_schedule": [], "monthly": {}, "signals": {}, "error": True}

    df = df_raw.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in df.columns:
            df[col] = df[col] * fx_mult

    cmp         = round(float(df["Close"].dropna().iloc[-1]), 2)
    tech        = _enhanced_tech_score(df)
    recent_mom  = _recent_week_momentum(df)
    monthly_exp = _next_monthly_expiry(monthly_expiry_fn, today)
    weekly_wr   = _weekly_win_rates(df, expiry_wd)
    w_atr       = _weekly_atr(df)

    # Extract max_pain signal separately — applied per-week with decay in _weekly_schedule
    mp_extra_sig = 0.0
    clean_extra: Dict[str, float] = {}
    if extra_signals:
        for k, v in extra_signals.items():
            if k == "max_pain":
                mp_extra_sig = float(v) if v else 0.0
            elif v != 0.0:
                clean_extra[k] = float(v)

    # If DB max_pain available and NSE API didn't return raw max_pain, store signal for downstream
    if mp_extra_sig != 0.0 and "max_pain" not in nse_opts:
        nse_opts["_max_pain_sig"] = mp_extra_sig

    # Build base signals dict — tech always included; extras merged in
    signals_base: Dict[str, float] = {"tech": tech}
    signals_base.update(clean_extra)

    # Compute monthly prediction first so we can use its expected move
    # in the weekly schedule — ensures last-week and monthly targets match.
    monthly  = _monthly_prediction(df, cmp, signals_base, weights, monthly_exp, nse_opts)
    m_move   = monthly.pop("_exp_move", 0.0)   # extract internal field, don't expose in output

    schedule = _weekly_schedule(
        df, cmp, signals_base, weights,
        expiry_wd, monthly_exp, weekly_wr, w_atr, recent_mom, nse_opts,
        monthly_exp_move=m_move,
    )

    # Assemble signal display metadata for the report
    signals_display = {k: round(v, 2) for k, v in signals_base.items()}
    if "max_pain" in nse_opts:
        signals_display["max_pain_signal"] = round(
            _max_pain_to_signal(nse_opts["max_pain"], cmp), 2)
    for k in ("pcr", "max_pain", "atm_iv", "support", "resistance"):
        if k in nse_opts:
            signals_display[k] = nse_opts[k]

    # ── Key S/R levels + options recommendation ────────────────────────────────
    m_direction  = monthly.get("direction", "UP")
    m_prob       = monthly.get("probability", 65.0)
    atr_pct_val  = round(w_atr / cmp * 100, 2) if cmp > 0 else 1.5
    try:
        key_levels = calc_key_levels(df, m_direction, m_prob, atr_pct_val)
    except Exception:
        key_levels = {}

    return {
        "name":            name,
        "cmp":             cmp,
        "note":            note,
        "unit":            "₹",
        "weekly_schedule": schedule,
        "monthly":         monthly,
        "signals":         signals_display,
        "levels":          key_levels,
        "error":           False,
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def _analyze_mcx_from_db(
    symbol: str,
    name: str,
    monthly_expiry_fn,
    note: str,
    weights: Dict[str, float],
) -> dict:
    """
    Analyze MCX instrument (CRUDEOIL / NATURALGAS) using mcx_ohlc DB data.
    Monthly expiry ONLY — MCX has no weekly options expiry.
    Falls back to static error result if DB has no data.
    """
    from screener.db import _get_conn, is_available as _db_ok

    error_result = {
        "name": name, "cmp": None, "note": note, "unit": "₹",
        "weekly_schedule": [], "monthly": {}, "signals": {}, "error": True,
    }

    if not _db_ok():
        return error_result

    # ── Fetch daily candles from mcx_ohlc ─────────────────────────────────────
    rows = []
    try:
        with _get_conn() as conn:
            if conn is None:
                return error_result
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ts, open, high, low, close, volume, oi
                    FROM mcx_ohlc
                    WHERE instrument = %s AND interval = 'day'
                    ORDER BY ts ASC
                """, (symbol,))
                rows = cur.fetchall()
    except Exception as exc:
        logger.warning("mcx_ohlc query failed for %s: %s", symbol, exc)
        return error_result

    if not rows or len(rows) < 30:
        logger.warning("mcx_ohlc: not enough data for %s (%d rows)", symbol, len(rows))
        return error_result

    df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume", "oi"])
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
    df = df.set_index("ts").sort_index()
    for col in ("Open", "High", "Low", "Close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Close"])

    cmp        = round(float(df["Close"].iloc[-1]), 2)
    tech       = _enhanced_tech_score(df) if len(df) >= 50 else _simple_tech_score(df)
    today      = date.today()

    monthly_exp = _next_monthly_expiry(monthly_expiry_fn, today)
    w_atr       = _weekly_atr(df)

    # ── DB-native signals ─────────────────────────────────────────────────────
    oi_conf_sig = _mcx_oi_confluence(df)
    vol_sig     = _mcx_volume_signal(df)
    seas_sig    = _mcx_seasonal_from_db(symbol)

    signals_base: Dict[str, float] = {
        "tech":     tech,
        "oi_conf":  oi_conf_sig,
        "volume":   vol_sig,
        "seasonal": seas_sig,
    }

    monthly = _monthly_prediction(
        df, cmp, signals_base, weights, monthly_exp, {}
    )
    monthly.pop("_exp_move", None)

    signals_display = {k: round(v, 2) for k, v in signals_base.items()}

    return {
        "name":            name,
        "cmp":             cmp,
        "note":            note,
        "unit":            "₹",
        "weekly_schedule": [],      # MCX: monthly expiry only
        "monthly":         monthly,
        "signals":         signals_display,
        "error":           False,
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_market_overview() -> dict:
    """
    Fetch Nifty 50, MCX Crude Oil, MCX Natural Gas analysis.

    Signal sourcing (DB-first):
      Nifty: VIX, PCR trend, OI change, IV percentile, Max Pain — all from DB.
             Falls back to yfinance VIX and NSE API if DB returns nothing.
      MCX:   price+OI confluence, volume, seasonal — all from mcx_ohlc DB table.
    """
    global _DL_CACHE, _NSE_SESSION
    _DL_CACHE.clear()
    _NSE_SESSION = None

    # Ensure all tables (including mcx_ohlc) exist before any queries
    try:
        from screener.db import init_schema as _init_schema
        _init_schema()
    except Exception:
        pass

    usd_inr = _usdinr()
    today   = date.today()

    # ── Nifty CMP (needed before DB signal calls requiring ATM/expiry) ────────
    _nifty_q  = _download("^NSEI", period="5d")
    nifty_cmp = (float(_nifty_q["Close"].dropna().iloc[-1])
                 if _nifty_q is not None else 0.0)

    # ── Nifty weekly expiry: next Thursday (0=Mon … 3=Thu) ───────────────────
    days_ahead   = (3 - today.weekday()) % 7
    nifty_expiry = today + timedelta(days=days_ahead if days_ahead else 7)
    nifty_atm    = int(round(nifty_cmp / 50) * 50) if nifty_cmp > 0 else 0

    # ── DB-native signals for Nifty ───────────────────────────────────────────
    vix_sig = _db_vix_signal()
    if vix_sig == 0.0:
        vix_sig = _india_vix_signal()          # yfinance fallback

    pcr_sig = _db_pcr_trend_signal()
    oi_sig  = _db_oi_change_signal(nifty_expiry)
    iv_sig  = _db_iv_percentile_signal(nifty_expiry, nifty_atm)
    mp_sig  = _db_max_pain_signal(nifty_expiry, nifty_cmp)  # returns signal or None

    nifty_extra: Dict[str, float] = {
        "vix":       vix_sig,
        "pcr_trend": pcr_sig,
        "oi_change": oi_sig,
        "iv_pct":    iv_sig,
    }
    if mp_sig is not None:
        nifty_extra["max_pain"] = mp_sig

    # NSE options: used for ATM IV (target-price calculation) and as max_pain fallback
    nse_opts = _nse_options_analysis(nifty_cmp)

    nifty = _analyze_instrument(
        ticker="^NSEI", name="Nifty 50", fx_mult=1.0,
        expiry_wd=3,
        monthly_expiry_fn=_last_thursday,
        note="NSE • Weekly: every Thursday • Monthly: last Thursday",
        weights=_NIFTY_W,
        extra_signals=nifty_extra,
        nse_opts=nse_opts,
    )
    crude = _analyze_mcx_from_db(
        symbol="CRUDEOIL",
        name="MCX Crude Oil",
        monthly_expiry_fn=_mcx_crude_expiry,
        note="MCX • Monthly expiry only (~20th)",
        weights=_CRUDE_W,
    )
    nat_gas = _analyze_mcx_from_db(
        symbol="NATURALGAS",
        name="MCX Natural Gas",
        monthly_expiry_fn=_mcx_natgas_expiry,
        note="MCX • Monthly expiry only (last day)",
        weights=_NATGAS_W,
    )

    return {
        "nifty":     nifty,
        "crude_oil": crude,
        "nat_gas":   nat_gas,
        "usd_inr":   usd_inr,
        "meta_signals": {
            "india_vix":  round(vix_sig,  2),
            "pcr_trend":  round(pcr_sig,  2),
            "oi_change":  round(oi_sig,   2),
            "iv_pct":     round(iv_sig,   2),
            "max_pain_sig": round(mp_sig, 2) if mp_sig is not None else None,
            "atm_iv":     nse_opts.get("atm_iv"),
            "support":    nse_opts.get("support"),
            "resistance": nse_opts.get("resistance"),
        },
    }
