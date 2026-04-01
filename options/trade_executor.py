"""
Paper Trade Executor + Auto-Exit Engine
────────────────────────────────────────
Handles:
  - DB schema for intraday_trades
  - Paper trade placement (2 legs per short strangle)
  - Current P&L calculation from option_chain LTP
  - Auto-exit: target / SL / 3:15 PM time exit
  - Trade history for analysis
"""

import logging
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from screener.db import _get_conn, is_available

logger = logging.getLogger(__name__)

LOT_SIZE = 75
IST = timezone(timedelta(hours=5, minutes=30))


# ── Schema ─────────────────────────────────────────────────────────────────────

_SCHEMA_READY = False

def _init_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _get_conn() as conn:
        if conn is None:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS intraday_trades (
                    id              BIGSERIAL       PRIMARY KEY,
                    group_id        VARCHAR(40)     NOT NULL,
                    trade_date      DATE            NOT NULL DEFAULT CURRENT_DATE,
                    strategy        VARCHAR(30)     NOT NULL,
                    trade_type      VARCHAR(10)     NOT NULL DEFAULT 'PAPER',
                    instrument      VARCHAR(20)     NOT NULL DEFAULT 'NIFTY',
                    expiry          DATE            NOT NULL,
                    strike          INTEGER         NOT NULL,
                    option_type     CHAR(2)         NOT NULL,
                    direction       VARCHAR(4)      NOT NULL,
                    lots            INTEGER         NOT NULL DEFAULT 1,
                    lot_size        INTEGER         NOT NULL DEFAULT 75,
                    entry_price     NUMERIC(10,2),
                    entry_time      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
                    target_price    NUMERIC(10,2),
                    sl_price        NUMERIC(10,2),
                    exit_price      NUMERIC(10,2),
                    exit_time       TIMESTAMPTZ,
                    exit_reason     VARCHAR(20),
                    pnl_pts         NUMERIC(10,2),
                    pnl_inr         NUMERIC(10,2),
                    status          VARCHAR(15)     NOT NULL DEFAULT 'OPEN',
                    setup_prob      NUMERIC(5,1),
                    notes           TEXT,
                    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_it_date_status
                    ON intraday_trades (trade_date, status);
                CREATE INDEX IF NOT EXISTS idx_it_group
                    ON intraday_trades (group_id);
            """)
        conn.commit()
    _SCHEMA_READY = True


# ── Paper trade placement ──────────────────────────────────────────────────────

def place_paper_trade(setup: dict) -> dict:
    """
    Insert 2 legs (CE + PE) for a short strangle paper trade.
    Returns {"ok": True, "group_id": ..., "ids": [ce_id, pe_id]}
    """
    if not is_available():
        return {"ok": False, "error": "Database not available"}

    _init_schema()
    group_id = f"PT-{date.today().strftime('%y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    legs = [
        {
            "strike":      setup["sell_ce"],
            "option_type": "CE",
            "entry_price": setup["ce_ltp"],
            "sl_price":    setup["ce_sl"],
            # Individual leg target: 35% of leg premium
            "target_price": round(setup["ce_ltp"] * 0.65, 1),   # price drops to 65% of entry
        },
        {
            "strike":      setup["sell_pe"],
            "option_type": "PE",
            "entry_price": setup["pe_ltp"],
            "sl_price":    setup["pe_sl"],
            "target_price": round(setup["pe_ltp"] * 0.65, 1),
        },
    ]

    ids = []
    try:
        with _get_conn() as conn:
            if conn is None:
                return {"ok": False, "error": "DB connection failed"}
            with conn.cursor() as cur:
                for leg in legs:
                    cur.execute("""
                        INSERT INTO intraday_trades
                            (group_id, strategy, trade_type, instrument, expiry,
                             strike, option_type, direction, lots, lot_size,
                             entry_price, target_price, sl_price,
                             status, setup_prob,
                             notes)
                        VALUES (%s,%s,'PAPER','NIFTY',%s,
                                %s,%s,'SELL',1,%s,
                                %s,%s,%s,
                                'OPEN',%s,
                                %s)
                        RETURNING id
                    """, (
                        group_id,
                        setup["strategy"],
                        setup["expiry"],
                        leg["strike"], leg["option_type"],
                        setup["lot_size"],
                        leg["entry_price"], leg["target_price"], leg["sl_price"],
                        setup["probability"],
                        f"Entry: {setup['total_premium']:.1f} pts total premium. "
                        f"Group target: +{setup['target_pts']} pts / SL: -{setup['sl_pts']} pts",
                    ))
                    ids.append(cur.fetchone()[0])
            conn.commit()
        logger.info("Paper trade placed: group=%s legs=%s", group_id, ids)
        return {"ok": True, "group_id": group_id, "ids": ids}
    except Exception as exc:
        logger.error("place_paper_trade failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ── Current LTP from option_chain ─────────────────────────────────────────────

def _get_current_ltp(expiry: date, strike: int, option_type: str) -> Optional[float]:
    try:
        with _get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ltp FROM option_chain
                    WHERE instrument  = 'NIFTY'
                      AND expiry      = %s
                      AND strike      = %s
                      AND option_type = %s
                      AND ltp IS NOT NULL
                    ORDER BY ts DESC LIMIT 1
                """, (expiry, strike, option_type))
                row = cur.fetchone()
                return float(row[0]) if row else None
    except Exception as exc:
        logger.warning("_get_current_ltp failed: %s", exc)
        return None


# ── Open trades with live P&L ──────────────────────────────────────────────────

def get_open_trades() -> list:
    """
    Return all OPEN intraday_trades for today with current LTP + P&L.
    Groups legs by group_id for combined P&L display.
    """
    if not is_available():
        return []
    _init_schema()

    try:
        with _get_conn() as conn:
            if conn is None:
                return []
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, group_id, strategy, expiry, strike, option_type,
                           direction, lots, lot_size, entry_price,
                           target_price, sl_price, setup_prob,
                           entry_time, status, notes
                    FROM intraday_trades
                    WHERE trade_date = CURRENT_DATE
                      AND trade_type = 'PAPER'
                      AND status     = 'OPEN'
                    ORDER BY group_id, option_type DESC
                """)
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
    except Exception as exc:
        logger.warning("get_open_trades failed: %s", exc)
        return []

    trades = []
    for row in rows:
        t = dict(zip(cols, row))
        # Fetch current LTP
        ltp = _get_current_ltp(t["expiry"], t["strike"], t["option_type"])
        t["current_ltp"] = ltp

        if ltp is not None and t["entry_price"]:
            # SELL direction: profit when LTP falls below entry
            pnl_pts = float(t["entry_price"]) - float(ltp)
            t["pnl_pts"] = round(pnl_pts, 1)
            t["pnl_inr"] = round(pnl_pts * t["lot_size"] * t["lots"], 0)

            # SL check per leg
            t["sl_breached"]     = ltp >= float(t["sl_price"])
            t["target_reached"]  = ltp <= float(t["target_price"])
        else:
            t["pnl_pts"] = None
            t["pnl_inr"] = None
            t["sl_breached"]    = False
            t["target_reached"] = False

        # Serialise dates/times for JSON
        t["expiry"] = str(t["expiry"])
        if t["entry_time"]:
            et = t["entry_time"]
            if hasattr(et, 'tzinfo') and et.tzinfo is None:
                et = et.replace(tzinfo=timezone.utc)
            t["entry_time"] = et.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%H:%M:%S")
        else:
            t["entry_time"] = None

        trades.append(t)

    return trades


def get_trade_history(limit: int = 50) -> list:
    """Closed trades for analysis — latest first."""
    if not is_available():
        return []
    _init_schema()

    try:
        with _get_conn() as conn:
            if conn is None:
                return []
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        group_id,
                        trade_date,
                        strategy,
                        COUNT(*)                         AS legs,
                        SUM(pnl_pts)                     AS total_pnl_pts,
                        SUM(pnl_inr)                     AS total_pnl_inr,
                        MAX(setup_prob)                  AS probability,
                        MAX(exit_reason)                 AS exit_reason,
                        MIN(entry_time)::time(0)         AS entry_time,
                        MAX(exit_time)::time(0)          AS exit_time
                    FROM intraday_trades
                    WHERE trade_type = 'PAPER'
                      AND status     = 'CLOSED'
                    GROUP BY group_id, trade_date, strategy
                    ORDER BY trade_date DESC, MIN(entry_time) DESC
                    LIMIT %s
                """, (limit,))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                # Serialise
                for r in rows:
                    r["trade_date"] = str(r["trade_date"])
                    # entry_time / exit_time are ::time(0) casts — plain time objects, no tz
                    # They come from TIMESTAMPTZ columns so the DB (IST session) returns IST times
                    r["entry_time"] = str(r["entry_time"]) if r["entry_time"] else None
                    r["exit_time"]  = str(r["exit_time"])  if r["exit_time"]  else None
                    for k in ("total_pnl_pts", "total_pnl_inr", "probability"):
                        if r[k] is not None:
                            r[k] = float(r[k])
                return rows
    except Exception as exc:
        logger.warning("get_trade_history failed: %s", exc)
        return []


# ── Manual exit ───────────────────────────────────────────────────────────────

def exit_trade(trade_id: int, reason: str = "MANUAL") -> dict:
    """Exit a single paper trade leg at current LTP."""
    if not is_available():
        return {"ok": False, "error": "DB unavailable"}
    _init_schema()

    try:
        with _get_conn() as conn:
            if conn is None:
                return {"ok": False, "error": "DB connection failed"}
            with conn.cursor() as cur:
                # Fetch trade details
                cur.execute("""
                    SELECT expiry, strike, option_type, entry_price, lot_size, lots
                    FROM intraday_trades WHERE id = %s AND status = 'OPEN'
                """, (trade_id,))
                row = cur.fetchone()
                if not row:
                    return {"ok": False, "error": "Trade not found or already closed"}
                expiry, strike, otype, entry_price, lot_size, lots = row

                ltp = _get_current_ltp(expiry, strike, otype)
                if ltp is None:
                    ltp = float(entry_price)  # fallback: exit at entry (flat)

                pnl_pts = float(entry_price) - ltp
                pnl_inr = pnl_pts * lot_size * lots

                cur.execute("""
                    UPDATE intraday_trades
                    SET exit_price  = %s,
                        exit_time   = NOW(),
                        exit_reason = %s,
                        pnl_pts     = %s,
                        pnl_inr     = %s,
                        status      = 'CLOSED'
                    WHERE id = %s
                """, (ltp, reason, round(pnl_pts, 2), round(pnl_inr, 2), trade_id))
            conn.commit()
        return {"ok": True, "exit_price": ltp, "pnl_pts": round(pnl_pts, 1), "pnl_inr": round(pnl_inr, 0)}
    except Exception as exc:
        logger.error("exit_trade failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def exit_group(group_id: str, reason: str = "MANUAL") -> dict:
    """Exit all OPEN legs of a group."""
    if not is_available():
        return {"ok": False, "error": "DB unavailable"}
    _init_schema()

    try:
        with _get_conn() as conn:
            if conn is None:
                return {"ok": False, "error": "DB connection failed"}
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id FROM intraday_trades
                    WHERE group_id = %s AND status = 'OPEN'
                """, (group_id,))
                ids = [r[0] for r in cur.fetchall()]
        results = [exit_trade(i, reason) for i in ids]
        total_pnl = sum(r.get("pnl_inr", 0) for r in results if r.get("ok"))
        return {"ok": True, "legs_exited": len(ids), "total_pnl_inr": total_pnl}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Auto-exit engine (runs every 60s in background thread) ────────────────────

def _ist_now() -> datetime:
    return datetime.now(IST)


def auto_exit_check():
    """
    Called every 60 seconds by a background thread.
    Exits open paper trades that hit target / SL / 3:15 PM.
    """
    if not is_available():
        return

    now_ist = _ist_now()
    # Only during market hours 9:15 AM – 3:20 PM
    market_open  = now_ist.replace(hour=9,  minute=15, second=0, microsecond=0)
    hard_exit_at = now_ist.replace(hour=15, minute=15, second=0, microsecond=0)

    if now_ist < market_open:
        return

    force_exit = now_ist >= hard_exit_at

    trades = get_open_trades()
    if not trades:
        return

    # Group by group_id for combined P&L check
    groups: dict = {}
    for t in trades:
        gid = t["group_id"]
        if gid not in groups:
            groups[gid] = []
        groups[gid].append(t)

    for gid, legs in groups.items():
        if force_exit:
            logger.info("AUTO-EXIT (TIME): group=%s", gid)
            exit_group(gid, "TIME")
            continue

        # Combined P&L across legs
        combined_pnl = sum(
            (t["pnl_pts"] or 0) for t in legs if t["pnl_pts"] is not None
        )
        # Read target/SL from notes (stored at entry); simplify: use first leg's DB values
        # Group target: first leg notes has "Group target: +X / SL: -Y"
        # Use per-leg SL breached flag for individual exit
        any_sl = any(t["sl_breached"] for t in legs)

        if any_sl:
            logger.info("AUTO-EXIT (SL): group=%s combined_pnl=%.1f", gid, combined_pnl)
            exit_group(gid, "SL")
        elif all(t.get("target_reached") for t in legs):
            logger.info("AUTO-EXIT (TARGET): group=%s combined_pnl=%.1f", gid, combined_pnl)
            exit_group(gid, "TARGET")
