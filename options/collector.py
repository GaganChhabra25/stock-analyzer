"""
MCX options chain per-minute collector — orchestrator.

Triggered by cron every minute during combined market hours:
  */1 5-20 * * 1-5   (CEST timezone on Contabo server)

NIFTY is collected by the persistent options/nifty_ws.py WebSocket service.
This cron process is intentionally MCX-only so the NIFTY WebSocket and REST
collector never write duplicate snapshots.

Adding a new exchange: create options/exchange/<name>.py and append
its collector instance to COLLECTORS below.  Nothing else changes.

EOD flags (sent at market close via separate cron):
  python options/collector.py --eod NFO   →  3:35 PM IST cron
  python options/collector.py --eod MCX   → 11:35 PM IST cron

Symbol-isolated collection:
  python options/collector.py --symbol NATURALGAS
  python options/collector.py                 → CRUDEOIL
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from options.kite_auth import get_kite
from options.exchange.mcx import MCXCollector
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

# ── Register exchanges here — only line to change when adding a new one ───────
COLLECTORS = [
    MCXCollector(symbols=["CRUDEOIL"]),
]


def _try_symbol_lock(symbol: str):
    """Return a held DB advisory-lock connection, or None when busy."""
    from screener.db import _get_conn

    conn = _get_conn()
    if conn is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))",
            (f"options_collector:{symbol.upper()}",),
        )
        acquired = bool(cur.fetchone()[0])
    if not acquired:
        conn.close()
        return None
    return conn


def collect_once(symbol: str | None = None) -> None:
    """Run one collection cycle, optionally isolated to one symbol.

    The default keeps CRUDEOIL on its established path. NATURALGAS runs from
    its own cron entry so its slower API cycle cannot delay crude collection.
    """
    kite = get_kite()
    if kite is None:
        logger.warning("Kite not authorised. Visit /kite/login to authorise.")
        return

    lock_conn = None
    try:
        if symbol:
            symbol = symbol.upper()
            if symbol not in {"CRUDEOIL", "NATURALGAS"}:
                raise ValueError(f"Unsupported collection symbol: {symbol}")
            lock_conn = _try_symbol_lock(symbol)
            if lock_conn is None:
                logger.info("[%s] Previous collection still running; skipping overlap.", symbol)
                return
            collectors = [MCXCollector(symbols=[symbol])]
        else:
            # Default cron path is deliberately crude-only; NG has its own job.
            collectors = [MCXCollector(symbols=["CRUDEOIL"])]

        for collector in collectors:
            if collector.is_open():
                collector.collect(kite)
            else:
                logger.debug("[%s] Market closed — skipping.", collector.exchange)
    finally:
        if lock_conn is not None:
            lock_conn.close()


def send_eod(exchange: str) -> None:
    """Send 'collection stopped' Telegram for the given exchange."""
    from options.notifier import TelegramNotifier
    TelegramNotifier(exchange).collection_stopped()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Options data collector")
    parser.add_argument(
        "--eod",
        metavar="EXCHANGE",
        choices=["NFO", "MCX"],
        help="Send end-of-day 'collection stopped' message for NFO or MCX",
    )
    parser.add_argument(
        "--symbol",
        choices=["CRUDEOIL", "NATURALGAS"],
        help="Collect only this MCX symbol; useful for isolating slow feeds.",
    )
    args = parser.parse_args()

    if args.eod:
        send_eod(args.eod)
    else:
        collect_once(args.symbol)
