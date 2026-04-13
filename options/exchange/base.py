"""
Abstract base class for per-exchange option chain collectors.

Subclasses declare exchange-specific constants and implement four
abstract methods.  All shared behaviour (DB insertion, Greeks,
OI analysis, Telegram first-batch notification) lives here once.

Adding a new exchange:
  1. Create options/exchange/<name>.py  subclassing ExchangeCollector
  2. Register the instance in options/collector.py → COLLECTORS list
  Zero existing files need to change.
"""

import logging
from abc import ABC, abstractmethod
from datetime import date, datetime, time
from typing import Optional

import pandas as pd

from options.greeks import implied_volatility, calculate_greeks
from screener.db import _get_conn

logger = logging.getLogger(__name__)

RISK_FREE_RATE = 0.07


class ExchangeCollector(ABC):
    """
    Template method pattern.  Subclasses must set class-level attributes
    and implement the four abstract methods below.
    """

    # ── Subclass must declare these ────────────────────────────────────────────
    exchange:     str            # "NFO" | "MCX"
    symbols:      list           # ["NIFTY", "BANKNIFTY"] | ["NATURALGAS", "CRUDEOIL"]
    n_strikes:    int            # strikes to collect above and below ATM
    market_open:  time           # IST
    market_close: time           # IST
    quote_prefix: str            # "NFO:" | "MCX:"
    vix_symbol:   Optional[str]  # NSE:INDIA VIX or None (no VIX for MCX)

    # ── Abstract — exchange-specific data source ───────────────────────────────

    @abstractmethod
    def load_instruments(self, kite) -> pd.DataFrame:
        """Load (and cache) the instrument universe for this exchange."""

    @abstractmethod
    def get_spot(self, kite, symbol: str, df: pd.DataFrame) -> Optional[float]:
        """
        Return the current underlying price.
        NFO  → NSE index quote (NSE:NIFTY 50)
        MCX  → near-month futures LTP (no cash segment for commodities)
        df is already loaded by the time this is called.
        """

    @abstractmethod
    def get_expiries(self, df: pd.DataFrame, symbol: str) -> list:
        """
        Return ordered list of expiry dates to collect for symbol.
        NFO  → [weekly_expiry, monthly_expiry]  (deduplicated)
        MCX  → [current_month_expiry]
        """

    @abstractmethod
    def get_option_tokens(
        self,
        df: pd.DataFrame,
        symbol: str,
        expiry: date,
        atm: int,
        n_strikes: int,
    ) -> dict:
        """Return {(strike, option_type): tradingsymbol} for ATM ± n_strikes."""

    @abstractmethod
    def atm_strike(self, price: float, symbol: str) -> int:
        """Round underlying price to the nearest valid strike for this exchange."""

    # ── Shared helpers ─────────────────────────────────────────────────────────

    def is_open(self) -> bool:
        """True if current IST time is within market hours and not a trading holiday."""
        from zoneinfo import ZoneInfo
        from config import NSE_HOLIDAYS
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        if now.date().strftime("%Y-%m-%d") in NSE_HOLIDAYS:
            logger.info("[%s] Holiday — skipping.", self.exchange)
            return False
        return self.market_open <= now.time() <= self.market_close

    @staticmethod
    def _days_to_expiry(expiry: date) -> float:
        delta = (expiry - date.today()).days
        return max(delta / 365.0, 1 / 365.0)

    def _fetch_vix(self, kite) -> Optional[float]:
        if not self.vix_symbol:
            return None
        try:
            q = kite.quote([self.vix_symbol])
            return q.get(self.vix_symbol, {}).get("last_price")
        except Exception as exc:
            logger.warning("[%s] VIX fetch failed: %s", self.exchange, exc)
            return None

    # ── Template method — called every minute by orchestrator ─────────────────

    def collect(self, kite) -> int:
        """
        One full collection cycle.  Returns total option_chain rows inserted.
        Called only when is_open() is True.
        """
        from zoneinfo import ZoneInfo
        ts = datetime.now(ZoneInfo("Asia/Kolkata"))
        logger.info("[%s] Collecting at %s IST", self.exchange, ts.strftime("%H:%M:%S"))

        try:
            df = self.load_instruments(kite)
        except Exception as exc:
            logger.error("[%s] load_instruments failed: %s", self.exchange, exc)
            return 0

        vix = self._fetch_vix(kite)
        rows_inserted = 0

        for symbol in self.symbols:
            spot = self.get_spot(kite, symbol, df)
            if not spot:
                logger.warning("[%s] No spot for %s", self.exchange, symbol)
                continue

            atm      = self.atm_strike(spot, symbol)
            expiries = self.get_expiries(df, symbol)
            if not expiries:
                logger.warning("[%s] No expiry for %s", self.exchange, symbol)
                continue

            # Build combined {(expiry, strike, opt_type): tradingsymbol} map
            combined: dict = {}
            for expiry in expiries:
                for (strike, opt_type), sym in self.get_option_tokens(
                    df, symbol, expiry, atm, self.n_strikes
                ).items():
                    combined[(expiry, strike, opt_type)] = sym

            if not combined:
                logger.warning("[%s] No tokens for %s", self.exchange, symbol)
                continue

            quote_keys = [f"{self.quote_prefix}{ts_sym}" for ts_sym in combined.values()]
            try:
                quotes = kite.quote(quote_keys)
            except Exception as exc:
                logger.error("[%s] Quote fetch failed for %s: %s", self.exchange, symbol, exc)
                continue

            # Reverse map for O(1) lookups
            sym_to_key = {v: k for k, v in combined.items()}

            option_rows = []
            call_oi: dict = {}
            put_oi:  dict = {}
            snapshot_expiry = expiries[0]   # nearest expiry drives the snapshot

            for q_key, quote in quotes.items():
                raw_sym = q_key.split(":", 1)[1] if ":" in q_key else q_key
                key = sym_to_key.get(raw_sym)
                if not key:
                    continue

                row_expiry, strike, opt_type = key
                ltp    = quote.get("last_price", 0) or 0
                volume = quote.get("volume", 0) or 0
                oi     = quote.get("oi", 0) or 0
                depth  = quote.get("depth", {})
                bid    = depth.get("buy",  [{}])[0].get("price") if depth else None
                ask    = depth.get("sell", [{}])[0].get("price") if depth else None

                T      = self._days_to_expiry(row_expiry)
                iv     = implied_volatility(ltp, spot, strike, T, RISK_FREE_RATE, opt_type)
                sigma  = (iv / 100.0) if iv else 0.20
                greeks = calculate_greeks(spot, strike, T, RISK_FREE_RATE, sigma, opt_type)

                option_rows.append({
                    "ts": ts, "instrument": symbol, "expiry": row_expiry,
                    "strike": strike, "option_type": opt_type,
                    "ltp": ltp, "bid": bid, "ask": ask,
                    "oi": oi, "oi_change": 0, "volume": volume,  # oi_change filled below
                    "iv": iv,
                    "delta": greeks["delta"], "gamma": greeks["gamma"],
                    "theta": greeks["theta"], "vega": greeks["vega"],
                    "underlying_ltp": spot,
                })

                if row_expiry == snapshot_expiry:
                    if opt_type == "CE":
                        call_oi[strike] = oi
                    else:
                        put_oi[strike] = oi

            if not option_rows:
                continue

            # Fill per-minute OI change by comparing against last stored OI
            self._fill_oi_changes(ts, symbol, option_rows)

            self._insert_option_rows(option_rows)
            rows_inserted += len(option_rows)
            self._insert_snapshot(ts, symbol, spot, vix, atm, option_rows, call_oi, put_oi)

        logger.info("[%s] Inserted %d rows.", self.exchange, rows_inserted)

        if rows_inserted > 0:
            from options.notifier import TelegramNotifier
            TelegramNotifier(self.exchange).collection_started(self.symbols, rows_inserted, ts)

        return rows_inserted

    # ── DB writers (shared, not overridable) ──────────────────────────────────

    @staticmethod
    def _fill_oi_changes(ts: datetime, symbol: str, option_rows: list) -> None:
        """
        Compute per-minute OI delta for each row.

        Fetches the most recent OI stored in DB for each
        (instrument, expiry, strike, option_type) before the current ts,
        then sets oi_change = current_oi − previous_oi.

        First collection of the day gets oi_change = 0 (no previous row exists).
        This replaces the old broken formula: oi − oi_day_low.
        """
        with _get_conn() as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (expiry, strike, option_type)
                        expiry, strike, option_type, oi
                    FROM option_chain
                    WHERE instrument = %s
                      AND ts::date   = %s
                      AND ts         < %s
                    ORDER BY expiry, strike, option_type, ts DESC
                """, (symbol, ts.date(), ts))
                prev_oi = {
                    (row[0], int(row[1]), row[2]): row[3]
                    for row in cur.fetchall()
                }

        for row in option_rows:
            key  = (row["expiry"], int(row["strike"]), row["option_type"])
            prev = prev_oi.get(key)
            row["oi_change"] = (row["oi"] - prev) if prev is not None else 0

    @staticmethod
    def _insert_option_rows(rows: list) -> None:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO option_chain (
                        ts, instrument, expiry, strike, option_type,
                        ltp, bid, ask, oi, oi_change, volume, iv,
                        delta, gamma, theta, vega, underlying_ltp
                    ) VALUES (
                        %(ts)s, %(instrument)s, %(expiry)s, %(strike)s, %(option_type)s,
                        %(ltp)s, %(bid)s, %(ask)s, %(oi)s, %(oi_change)s, %(volume)s, %(iv)s,
                        %(delta)s, %(gamma)s, %(theta)s, %(vega)s, %(underlying_ltp)s
                    )
                """, rows)
            conn.commit()

    @staticmethod
    def _insert_snapshot(
        ts, symbol, spot, vix, atm,
        option_rows, call_oi, put_oi,
    ) -> None:
        total_call = sum(call_oi.values())
        total_put  = sum(put_oi.values())
        pcr = round(total_put / total_call, 3) if total_call else None

        atm_call = next((r["ltp"] for r in option_rows
                         if r["strike"] == atm and r["option_type"] == "CE"), None)
        atm_put  = next((r["ltp"] for r in option_rows
                         if r["strike"] == atm and r["option_type"] == "PE"), None)
        straddle     = round(atm_call + atm_put, 2) if (atm_call and atm_put) else None
        expected_mov = round((straddle / spot) * 100, 2) if (straddle and spot) else None

        call_wall = max(call_oi, key=call_oi.get) if call_oi else None
        put_wall  = max(put_oi,  key=put_oi.get)  if put_oi  else None

        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO market_snapshot (
                        ts, instrument, spot_price, vix, pcr_oi,
                        atm_strike, atm_straddle, expected_move,
                        call_oi_wall, put_oi_wall
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    ts, symbol, spot, vix, pcr,
                    atm, straddle, expected_mov,
                    call_wall, put_wall,
                ))
            conn.commit()
