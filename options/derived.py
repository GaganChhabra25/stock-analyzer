"""
EOD derived metrics calculator — Max Pain, GEX, IV Skew.

Reads from option_chain table (already collected per-minute).
Computes and stores into derived_daily table.

Run at 15:40 IST (after market close) via cron:
  10 12 * * 1-5   options/derived.py          (CEST 12:10 = IST 15:40)

Metrics computed per instrument (NIFTY, BANKNIFTY) per day:

  Max Pain
    Strike where total dollar loss for option buyers is maximised.
    = the level that option writers want price to settle at expiry.

  GEX (Gamma Exposure)
    Net gamma exposure in Rs. crore across all strikes.
    GEX > 0 → market makers long gamma → dampen moves (range-bound).
    GEX < 0 → market makers short gamma → amplify moves (trending).
    gex_flip = strike where cumulative GEX crosses zero.

  IV Skew (25-delta)
    25-delta PE implied vol minus 25-delta CE implied vol.
    Positive skew = puts more expensive → market fearful / hedging.
    Negative skew = calls more expensive → euphoria / upside chase.

  ATM IV, PCR OI, OI walls — captured from latest snapshot of the day.
"""

import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from screener.db import _get_conn
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

IST        = ZoneInfo("Asia/Kolkata")
INSTRUMENTS = ["NIFTY", "BANKNIFTY"]
LOT_SIZES   = {"NIFTY": 25, "BANKNIFTY": 15}


# ── Max Pain ──────────────────────────────────────────────────────────────────
def calc_max_pain(cur, instrument: str, expiry: date, last_ts) -> Optional[int]:
    """
    Max Pain = strike that maximises total ITM option losses for buyers.
    We compute total pain at every strike and return the minimum-pain strike
    (which is the maximum-pain strike for option sellers = max pain).
    """
    cur.execute("""
        SELECT strike, option_type, oi
        FROM option_chain
        WHERE instrument = %s
          AND expiry = %s
          AND ts = %s
          AND oi > 0
        ORDER BY strike
    """, (instrument, expiry, last_ts))
    rows = cur.fetchall()
    if not rows:
        return None

    strikes     = sorted(set(r[0] for r in rows))
    oi_by_key   = {(r[0], r[1]): r[2] for r in rows}
    lot         = LOT_SIZES.get(instrument, 25)

    min_pain    = float("inf")
    max_pain_strike = None

    for s in strikes:
        pain = 0
        for strike, otype, oi in rows:
            if otype == "CE" and strike < s:
                pain += (s - strike) * oi * lot
            elif otype == "PE" and strike > s:
                pain += (strike - s) * oi * lot
        if pain < min_pain:
            min_pain = pain
            max_pain_strike = s

    return max_pain_strike


# ── GEX ───────────────────────────────────────────────────────────────────────
def calc_gex(cur, instrument: str, expiry: date, last_ts,
             spot: float) -> tuple:
    """
    GEX at each strike = (gamma_CE × OI_CE - gamma_PE × OI_PE) × lot × spot²/100
    Returns (gex_total_crore, gex_flip_strike).
    """
    cur.execute("""
        SELECT strike, option_type, gamma, oi
        FROM option_chain
        WHERE instrument = %s
          AND expiry = %s
          AND ts = %s
          AND gamma IS NOT NULL
          AND oi > 0
        ORDER BY strike
    """, (instrument, expiry, last_ts))
    rows = cur.fetchall()
    if not rows:
        return None, None

    lot = LOT_SIZES.get(instrument, 25)
    gex_by_strike: dict = {}

    for strike, otype, gamma, oi in rows:
        contribution = float(gamma) * int(oi) * lot * (spot ** 2) / 100
        if otype == "CE":
            gex_by_strike[strike] = gex_by_strike.get(strike, 0) + contribution
        else:
            gex_by_strike[strike] = gex_by_strike.get(strike, 0) - contribution

    gex_total  = sum(gex_by_strike.values()) / 1e7   # in Rs. crore
    gex_flip   = None
    cumulative = 0
    prev_sign  = None

    for strike in sorted(gex_by_strike.keys()):
        cumulative += gex_by_strike[strike]
        sign = 1 if cumulative >= 0 else -1
        if prev_sign is not None and sign != prev_sign:
            gex_flip = strike
            break
        prev_sign = sign

    return round(gex_total, 4), gex_flip


# ── IV Skew ────────────────────────────────────────────────────────────────────
def calc_iv_skew(cur, instrument: str, expiry: date, last_ts) -> Optional[float]:
    """
    25-delta skew = IV of 25-delta PE minus IV of 25-delta CE.
    Finds the CE with delta closest to +0.25 and PE with delta closest to -0.25.
    """
    cur.execute("""
        SELECT option_type, delta, iv
        FROM option_chain
        WHERE instrument = %s
          AND expiry = %s
          AND ts = %s
          AND delta IS NOT NULL
          AND iv > 0
        ORDER BY ABS(ABS(delta) - 0.25)
        LIMIT 10
    """, (instrument, expiry, last_ts))
    rows = cur.fetchall()
    if not rows:
        return None

    ce_iv = next((float(r[2]) for r in rows if r[0] == "CE"), None)
    pe_iv = next((float(r[2]) for r in rows if r[0] == "PE"), None)

    if ce_iv is None or pe_iv is None:
        return None
    return round(pe_iv - ce_iv, 4)


# ── ATM IV ─────────────────────────────────────────────────────────────────────
def calc_atm_iv(cur, instrument: str, expiry: date, last_ts,
                atm_strike: int) -> Optional[float]:
    """Average IV of ATM CE and PE."""
    cur.execute("""
        SELECT option_type, iv
        FROM option_chain
        WHERE instrument = %s
          AND expiry = %s
          AND ts = %s
          AND strike = %s
          AND iv > 0
    """, (instrument, expiry, last_ts, atm_strike))
    rows = cur.fetchall()
    ivs = [float(r[1]) for r in rows if r[1]]
    return round(sum(ivs) / len(ivs), 2) if ivs else None


# ── OI walls ──────────────────────────────────────────────────────────────────
def calc_oi_walls(cur, instrument: str, expiry: date,
                  last_ts) -> tuple:
    """Return (call_oi_wall, put_oi_wall) — strikes with max CE/PE OI."""
    cur.execute("""
        SELECT strike, option_type, oi
        FROM option_chain
        WHERE instrument = %s
          AND expiry = %s
          AND ts = %s
          AND oi > 0
    """, (instrument, expiry, last_ts))
    rows = cur.fetchall()
    if not rows:
        return None, None

    ce_oi = {r[0]: r[2] for r in rows if r[1] == "CE"}
    pe_oi = {r[0]: r[2] for r in rows if r[1] == "PE"}

    call_wall = max(ce_oi, key=ce_oi.get) if ce_oi else None
    put_wall  = max(pe_oi, key=pe_oi.get) if pe_oi else None
    return call_wall, put_wall


# ── Main ──────────────────────────────────────────────────────────────────────
def run() -> None:
    today = date.today()
    logger.info("[DERIVED] Computing EOD metrics for %s", today)

    with _get_conn() as conn:
        with conn.cursor() as cur:

            for instrument in INSTRUMENTS:
                # Find the nearest expiry with data today
                cur.execute("""
                    SELECT expiry
                    FROM option_chain
                    WHERE instrument = %s
                      AND ts::date = %s
                    ORDER BY expiry
                    LIMIT 1
                """, (instrument, today))
                row = cur.fetchone()
                if not row:
                    logger.warning("[DERIVED] No option_chain data for %s today", instrument)
                    continue
                expiry = row[0]

                # Latest snapshot timestamp for today
                cur.execute("""
                    SELECT MAX(ts)
                    FROM option_chain
                    WHERE instrument = %s
                      AND expiry = %s
                      AND ts::date = %s
                """, (instrument, expiry, today))
                row = cur.fetchone()
                if not row or not row[0]:
                    continue
                last_ts = row[0]

                # Spot price from latest market_snapshot
                cur.execute("""
                    SELECT spot_price, pcr_oi, atm_strike
                    FROM market_snapshot
                    WHERE instrument = %s
                      AND ts::date = %s
                    ORDER BY ts DESC
                    LIMIT 1
                """, (instrument, today))
                snap = cur.fetchone()
                spot      = float(snap[0]) if snap and snap[0] else None
                pcr_oi    = float(snap[1]) if snap and snap[1] else None
                atm_strike = int(snap[2])  if snap and snap[2] else None

                if not spot:
                    logger.warning("[DERIVED] No spot price for %s", instrument)
                    continue

                # Compute all metrics
                max_pain              = calc_max_pain(cur, instrument, expiry, last_ts)
                gex_total, gex_flip   = calc_gex(cur, instrument, expiry, last_ts, spot)
                iv_skew               = calc_iv_skew(cur, instrument, expiry, last_ts)
                atm_iv                = calc_atm_iv(cur, instrument, expiry, last_ts, atm_strike) if atm_strike else None
                call_wall, put_wall   = calc_oi_walls(cur, instrument, expiry, last_ts)

                cur.execute("""
                    INSERT INTO derived_daily
                        (date, instrument, expiry, max_pain, gex_total, gex_flip,
                         iv_skew_25d, atm_iv, pcr_oi, call_oi_wall, put_oi_wall)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (date, instrument) DO UPDATE SET
                        expiry       = EXCLUDED.expiry,
                        max_pain     = EXCLUDED.max_pain,
                        gex_total    = EXCLUDED.gex_total,
                        gex_flip     = EXCLUDED.gex_flip,
                        iv_skew_25d  = EXCLUDED.iv_skew_25d,
                        atm_iv       = EXCLUDED.atm_iv,
                        pcr_oi       = EXCLUDED.pcr_oi,
                        call_oi_wall = EXCLUDED.call_oi_wall,
                        put_oi_wall  = EXCLUDED.put_oi_wall,
                        computed_at  = NOW()
                """, (today, instrument, expiry, max_pain, gex_total, gex_flip,
                      iv_skew, atm_iv, pcr_oi, call_wall, put_wall))

                logger.info(
                    "[DERIVED] %s | MaxPain=%s | GEX=%.2f Cr | Skew=%.4f | ATM_IV=%.2f",
                    instrument, max_pain, gex_total or 0, iv_skew or 0, atm_iv or 0
                )

        conn.commit()
    logger.info("[DERIVED] EOD metrics saved for %s.", today)


if __name__ == "__main__":
    run()
