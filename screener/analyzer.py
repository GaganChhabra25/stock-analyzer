"""
Core screening & statistical analysis engine.

Orchestrates batch OHLCV download, liquidity filtering, and per-stock analysis.
All technical indicator logic lives in screener/signals.py.
All prediction logic lives in screener/predictions.py.
"""

import logging
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

from core.constants import MIN_DAILY_VALUE_CR, MIN_DATA_DAYS, BATCH_SIZE
from screener.universe import get_symbols, get_meta
from screener.signals import (
    score_direction,
    calc_win_rate,
    calc_probability,
    weekly_win_rate,
)
from screener.predictions import (
    monthly_history,
    weekly_history,
    current_month_prediction,
    next_month_prediction,
)

logger = logging.getLogger(__name__)


class StockScreener:
    """
    Screens NSE stocks for high-conviction directional trades.

    Usage:
        screener = StockScreener()
        top_candidates, all_candidates = screener.run(top_n=15)
    """

    def __init__(self):
        self.end   = datetime.today()
        self.start = self.end - timedelta(days=500)   # ~16 months

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self, top_n: int = 8, fast: bool = False):
        """
        Returns (top_candidates, all_candidates).

        top_candidates — top_n stocks by probability (detailed report cards).
        all_candidates — every stock that passed liquidity filter
                         (used so any NSE stock in Active Trades has live data).
        """
        symbols = get_symbols()
        if fast:
            symbols = symbols[:60]

        print(f"\n  Downloading {len(symbols)} tickers in batches…")
        all_data = self._batch_download(symbols)

        print(f"  Analysing {len(all_data)} downloaded tickers…")
        candidates = []
        for sym, hist in all_data.items():
            result = self._analyze_one(sym, hist)
            if result:
                candidates.append(result)

        print(f"  {len(candidates)} stocks passed liquidity filter.")
        candidates.sort(key=lambda x: x["probability"], reverse=True)
        top = candidates[:top_n]
        print(f"  Top {len(top)} candidates selected.\n")
        return top, candidates

    def analyze_extra_symbols(self, symbols: list) -> dict:
        """
        Download and fully analyze specific symbols regardless of liquidity.
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

    # ── Batch download ────────────────────────────────────────────────────────

    def _batch_download(self, symbols: list) -> dict:
        """Download OHLCV for all symbols in BATCH_SIZE chunks."""
        result  = {}
        batches = [symbols[i:i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]

        for idx, batch in enumerate(batches):
            tickers = [s + ".NS" for s in batch]
            print(f"    Batch {idx+1}/{len(batches)} ({len(batch)} stocks)…", end=" ", flush=True)
            try:
                raw = yf.download(
                    tickers,
                    start    = self.start.strftime("%Y-%m-%d"),
                    end      = self.end.strftime("%Y-%m-%d"),
                    auto_adjust = True,
                    progress    = False,
                    threads     = True,
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
            except Exception as exc:
                print(f"✗ ({exc})")
                logger.warning("Batch %d download failed: %s", idx + 1, exc)

        return result

    # ── Single stock analysis ─────────────────────────────────────────────────

    def _analyze_one(self, symbol: str, hist: pd.DataFrame, force: bool = False):
        try:
            close  = hist["Close"]
            volume = hist["Volume"]
            high   = hist["High"]
            low    = hist["Low"]

            # ── Liquidity filter ──────────────────────────────────────────────
            avg_price    = float(close.tail(20).mean())
            avg_vol      = float(volume.tail(20).mean())
            daily_val_cr = avg_price * avg_vol / 1e7   # crores
            if daily_val_cr < MIN_DAILY_VALUE_CR and not force:
                return None

            latest_close = float(close.iloc[-1])

            # ── Direction & technicals ────────────────────────────────────────
            raw_score, norm_score, direction, breakdown, rsi_val, vol_ratio = \
                score_direction(close, volume)

            # ── Historical patterns ───────────────────────────────────────────
            monthly_hist = monthly_history(close)
            weekly_hist  = weekly_history(hist)
            win_rate, bt = calc_win_rate(monthly_hist, direction)
            weekly_wr    = weekly_win_rate(close)

            # ── Composite probability ─────────────────────────────────────────
            probability = calc_probability(norm_score, win_rate, vol_ratio)

            # ── Volatility / expected range ───────────────────────────────────
            daily_std   = float(close.pct_change().dropna().std())
            monthly_vol = round(daily_std * (21 ** 0.5) * 100, 1)
            atr_14      = float((high - low).tail(14).mean())
            atr_pct     = round(atr_14 / latest_close * 100, 2)

            # ── 52-week high / low ────────────────────────────────────────────
            wk52_high = round(float(high.tail(252).max()), 2)
            wk52_low  = round(float(low.tail(252).min()), 2)
            vs_52h    = round((latest_close / wk52_high - 1) * 100, 1)
            vs_52l    = round((latest_close / wk52_low  - 1) * 100, 1)

            # ── Month predictions ─────────────────────────────────────────────
            cur_month  = current_month_prediction(close, daily_std, norm_score)
            next_month = next_month_prediction(close, direction, norm_score, monthly_vol)

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
        except Exception as exc:
            logger.debug("Analysis failed for %s: %s", symbol, exc)
            return None
