"""
Telegram notifications for options data collection events.

Each exchange has its own TelegramNotifier instance so that
NATURALGAS/CRUDEOIL messages are independent of NIFTY.

Sentinel files (data/started_<EXCHANGE>_<date>.flag etc.) ensure
each message is sent exactly once per day regardless of how many
times the collector runs.
"""

import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SENTINEL_DIR = Path(__file__).parent.parent / "data"


def _sentinel(tag: str, exchange: str) -> Path:
    return _SENTINEL_DIR / f"{tag}_{exchange}_{date.today()}.flag"


class TelegramNotifier:
    def __init__(self, exchange: str) -> None:
        self.exchange = exchange
        self._token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat    = os.environ.get("TELEGRAM_CHAT_ID", "")

    def _send(self, msg: str) -> None:
        if not self._token or not self._chat:
            logger.debug("[%s] Telegram credentials not set — skipping.", self.exchange)
            return
        try:
            import requests
            resp = requests.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": self._chat, "text": msg},
                timeout=10,
            )
            if not resp.ok:
                logger.warning("[%s] Telegram send failed (%s): %s",
                               self.exchange, resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("[%s] Telegram send error: %s", self.exchange, exc)

    # ── Collection started — fires once per day on first successful batch ──────

    def collection_started(
        self,
        symbols: list,
        rows: int,
        ts: datetime,
    ) -> bool:
        """
        Send 'collection started' message.
        Returns True if the message was sent (first call today), False otherwise.
        """
        sentinel = _sentinel("started", self.exchange)
        if sentinel.exists():
            return False
        sentinel.touch()

        ts_str   = ts.strftime("%d-%b-%Y %I:%M %p IST")
        syms_str = " + ".join(symbols)
        self._send(
            f"\U0001f7e2 [{self.exchange}] Collection started\n"
            f"Time       : {ts_str}\n"
            f"Instruments: {syms_str}\n"
            f"First batch: {rows} rows"
        )
        logger.info("[%s] Collection-started Telegram sent.", self.exchange)
        return True

    # ── Collection stopped — called by collector.py --eod at market close ─────

    def collection_stopped(self, ts: Optional[datetime] = None) -> None:
        """
        Send 'collection stopped' message with today's DB stats.
        Fires once per day (sentinel protected).
        """
        sentinel = _sentinel("stopped", self.exchange)
        if sentinel.exists():
            return
        sentinel.touch()

        if ts is None:
            from zoneinfo import ZoneInfo
            ts = datetime.now(ZoneInfo("Asia/Kolkata"))
        ts_str = ts.strftime("%d-%b-%Y %I:%M %p IST")

        rows, minutes, first_ts, last_ts = self._fetch_today_stats()

        lines = [
            f"\U0001f534 [{self.exchange}] Collection stopped",
            f"Time             : {ts_str}",
            f"Rows today       : {rows:,}",
            f"Minutes captured : {minutes}",
        ]
        if first_ts:
            lines.append(f"First capture    : {first_ts}")
        if last_ts:
            lines.append(f"Last capture     : {last_ts}")

        self._send("\n".join(lines))
        logger.info("[%s] Collection-stopped Telegram sent.", self.exchange)

    # ── DB stats helper ────────────────────────────────────────────────────────

    def _fetch_today_stats(self):
        """Return (total_rows, minutes_captured, first_ts_str, last_ts_str)."""
        from screener.db import _get_conn
        try:
            with _get_conn() as conn:
                if conn is None:
                    return 0, 0, None, None
                with conn.cursor() as cur:
                    # Instruments belonging to this exchange
                    instruments = tuple(self._exchange_instruments())
                    if not instruments:
                        return 0, 0, None, None

                    cur.execute("""
                        SELECT
                            COUNT(*)                        AS total_rows,
                            COUNT(DISTINCT ts)              AS minutes_captured,
                            MIN(ts) AT TIME ZONE 'Asia/Kolkata' AS first_ts,
                            MAX(ts) AT TIME ZONE 'Asia/Kolkata' AS last_ts
                        FROM option_chain
                        WHERE ts::date = CURRENT_DATE
                          AND instrument = ANY(%s)
                    """, ([*instruments],))
                    row = cur.fetchone()
                    if not row or not row[0]:
                        return 0, 0, None, None

                    total, mins, first, last = row
                    fmt = lambda t: t.strftime("%I:%M %p IST") if t else None
                    return int(total), int(mins), fmt(first), fmt(last)
        except Exception as exc:
            logger.warning("[%s] Stats fetch failed: %s", self.exchange, exc)
            return 0, 0, None, None

    def _exchange_instruments(self) -> list:
        """Map exchange name to the instrument names stored in option_chain."""
        mapping = {
            "NFO": ["NIFTY"],
            "MCX": ["NATURALGAS", "CRUDEOIL"],
        }
        return mapping.get(self.exchange, [])
