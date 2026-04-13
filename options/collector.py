"""
Options chain per-minute collector — orchestrator.

Triggered by cron every minute during combined market hours:
  */1 5-20 * * 1-5   (CEST timezone on Contabo server)

Each ExchangeCollector checks its own market hours via is_open(),
so NFO and MCX activate/deactivate independently within the same cron.

Adding a new exchange: create options/exchange/<name>.py and append
its collector instance to COLLECTORS below.  Nothing else changes.

EOD flags (sent at market close via separate cron):
  python options/collector.py --eod NFO   →  3:35 PM IST cron
  python options/collector.py --eod MCX   → 11:35 PM IST cron
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from options.kite_auth import get_kite
from options.exchange.nfo import NFOCollector
from options.exchange.mcx import MCXCollector
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

# ── Register exchanges here — only line to change when adding a new one ───────
COLLECTORS = [
    NFOCollector(),
    MCXCollector(),
]


def collect_once() -> None:
    """Run one collection cycle across all registered exchanges."""
    kite = get_kite()
    if kite is None:
        logger.warning("Kite not authorised. Visit /kite/login to authorise.")
        return

    for collector in COLLECTORS:
        if collector.is_open():
            collector.collect(kite)
        else:
            logger.debug("[%s] Market closed — skipping.", collector.exchange)


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
    args = parser.parse_args()

    if args.eod:
        send_eod(args.eod)
    else:
        collect_once()
