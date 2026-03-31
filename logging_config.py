claude"""
Centralised logging configuration.

Call configure_logging() once at application startup (main.py, app.py,
run_screener.py). After that, every module uses:

    import logging
    logger = logging.getLogger(__name__)

Log level can be overridden via the LOG_LEVEL environment variable:
    LOG_LEVEL=DEBUG python main.py
"""

import logging
import os
from pathlib import Path


def configure_logging(level: str = None) -> None:
    """
    Set up the root logger with:
      - Console handler at INFO (or LOG_LEVEL env override)
      - Rotating file handler at DEBUG writing to logs/app.log

    Safe to call multiple times — adds handlers only once.
    """
    root = logging.getLogger()
    if root.handlers:
        return   # Already configured; don't add duplicate handlers

    env_level  = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    log_level  = getattr(logging, env_level, logging.INFO)

    root.setLevel(logging.DEBUG)   # Root captures everything; handlers filter

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console handler ────────────────────────────────────────────────────
    console = logging.StreamHandler()
    console.setLevel(log_level)
    console.setFormatter(fmt)
    root.addHandler(console)

    # ── File handler ───────────────────────────────────────────────────────
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "app.log"

    try:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:
        # Don't crash if log file can't be created (e.g., read-only FS)
        logging.warning("Could not create log file %s: %s", log_file, exc)
