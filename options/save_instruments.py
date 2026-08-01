"""
Daily instrument dump saver — runs at 8:45 AM IST via cron.

Saves a dated copy of all NFO instruments so we can fetch historical
data for expired contracts in the future (Kite removes them after expiry).
"""

import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from options.kite_auth import get_kite
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

ARCHIVE_DIR = Path(__file__).parent.parent / "data" / "instrument_archive"


def save_instruments():
    kite = get_kite()
    if not kite:
        logger.error("Kite not authorized — cannot save instruments.")
        return

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    today     = date.today()
    out_file  = ARCHIVE_DIR / f"nfo_instruments_{today}.json"

    if out_file.exists():
        logger.info("Instrument dump already saved for %s", today)
        return

    logger.info("Fetching NFO instrument list...")
    instruments = kite.instruments("NFO")

    # Keep only NIFTY options
    filtered = [
        i for i in instruments
        if i.get("tradingsymbol", "").startswith("NIFTY")
        and i.get("instrument_type") in ("CE", "PE")
    ]

    # Convert date objects to strings for JSON serialisation
    for i in filtered:
        if hasattr(i.get("expiry"), "isoformat"):
            i["expiry"] = i["expiry"].isoformat()

    with open(out_file, "w") as f:
        json.dump(filtered, f)

    logger.info("Saved %d instruments to %s", len(filtered), out_file.name)


if __name__ == "__main__":
    save_instruments()
