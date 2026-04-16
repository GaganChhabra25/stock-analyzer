"""
Telegram notification service for market predictions.

Sends a daily alert to a Telegram chat after the screener runs:
  - All market predictions (Nifty / Crude / NatGas)
  - Running model accuracy from the DB

Configuration (in .env):
  TELEGRAM_BOT_TOKEN = <your bot token>
  TELEGRAM_CHAT_ID   = <your chat id>
"""

import logging
import os
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

_TG_URL = "https://api.telegram.org/bot{token}/sendMessage"


# ── Config ─────────────────────────────────────────────────────────────────────

def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "")


def is_configured() -> bool:
    return bool(_token() and _chat_id())


# ── Low-level send ─────────────────────────────────────────────────────────────

def _send(text: str) -> bool:
    """POST a single HTML-formatted message to Telegram. Returns True on success."""
    if not is_configured():
        logger.debug("Telegram not configured — skipping notification.")
        return False
    try:
        import requests
        resp = requests.post(
            _TG_URL.format(token=_token()),
            json={
                "chat_id":    _chat_id(),
                "text":       text,
                "parse_mode": "HTML",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("Telegram API error %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


# ── Formatting helpers ─────────────────────────────────────────────────────────

def _dir_icon(direction: str) -> str:
    return "🔴 ▼" if direction == "DOWN" else "🟢 ▲"


def _inr(value, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        return f"₹{float(value):,.0f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _sig_label(value: Optional[float], pos_label: str, neg_label: str) -> str:
    if value is None:
        return ""
    if value > 0.05:
        return pos_label
    if value < -0.05:
        return neg_label
    return "Neutral"


# ── Public API ─────────────────────────────────────────────────────────────────

def send_predictions(overview: dict, accuracy_rows: list) -> bool:
    """
    Build and send the daily prediction alert.

    Args:
        overview:       Result of fetch_market_overview()
        accuracy_rows:  Result of get_accuracy_summary() — may be empty list
    Returns:
        True if message was delivered successfully.
    """
    if not overview:
        return False

    today_str = date.today().strftime("%d %b %Y")
    lines: list = [f"<b>📊 Market Predictions — {today_str}</b>\n"]

    INSTRUMENTS = [
        ("nifty",     "NIFTY 50",   ""),
        ("crude_oil", "CRUDE OIL",  "/bbl"),
        ("nat_gas",   "NAT GAS",    "/mmBtu"),
    ]

    for key, label, unit in INSTRUMENTS:
        inst = overview.get(key)
        if not inst or inst.get("error"):
            continue

        cmp = inst.get("cmp")
        cmp_str = _inr(cmp, unit) if cmp else "—"
        lines.append(f"<b>{label}</b>  |  CMP: {cmp_str}")

        # Monthly prediction
        monthly = inst.get("monthly", {})
        if monthly and monthly.get("direction"):
            icon    = _dir_icon(monthly["direction"])
            prob    = monthly.get("probability", 0)
            target  = _inr(monthly.get("expected_price"))
            exp_d   = monthly.get("expiry_date", "")
            hist_wr = monthly.get("hist_wr", 0)
            lines.append(
                f"  Monthly ({exp_d}): {icon} <b>{monthly['direction']} {prob:.0f}%</b>"
                f" → {target}  |  Hist WR: {hist_wr:.0f}%"
            )

        # Next two upcoming weekly expiries
        schedule     = inst.get("weekly_schedule", [])
        future_weeks = [w for w in schedule if not w.get("is_past")][:2]
        for w in future_weeks:
            icon   = _dir_icon(w["direction"])
            prob   = w.get("probability", 0)
            target = _inr(w.get("target"))
            exp_d  = w.get("expiry_date", "")
            label_wk = "This week" if w is future_weeks[0] else "Next week"
            lines.append(
                f"  {label_wk} ({exp_d}): {icon} <b>{w['direction']} {prob:.0f}%</b>"
                f" → {target}"
            )

        # Signal summary (Nifty has the richest signals)
        sigs = inst.get("signals", {})
        if sigs:
            parts = []
            pcr = sigs.get("pcr")
            if pcr is not None:
                parts.append(f"PCR {float(pcr):.2f}")
            vix_s = sigs.get("vix")
            if vix_s is not None:
                parts.append("VIX " + _sig_label(vix_s, "Bullish", "Bearish"))
            tech_s = sigs.get("tech")
            if tech_s is not None:
                parts.append("Tech " + _sig_label(tech_s, "Bullish", "Bearish"))
            glo_s = sigs.get("global")
            if glo_s is not None:
                parts.append("Global " + _sig_label(glo_s, "Bullish", "Bearish"))
            if parts:
                lines.append(f"  Signals: {' · '.join(parts)}")

        lines.append("")

    # Running model accuracy (only shown once enough data exists)
    if accuracy_rows:
        lines.append("<b>📈 Running Model Accuracy</b>")
        for row in accuracy_rows:
            inst_code = row.get("instrument", "")
            ptype     = row.get("prediction_type", "")
            acc       = row.get("accuracy_pct") or 0
            total     = row.get("total_predictions", 0)
            correct   = row.get("correct", 0)
            lines.append(
                f"  {inst_code} {ptype}: <b>{acc}%</b>  ({correct}/{total} correct)"
            )
        lines.append("")

    lines.append(f"<i>Auto-generated · {today_str}</i>")

    message = "\n".join(lines)

    # Telegram hard limit is 4096 chars — truncate gracefully if needed
    if len(message) > 4000:
        message = message[:3980] + "\n\n<i>…message truncated</i>"

    return _send(message)


def send_error(context: str, error: str) -> bool:
    """Send a brief error alert — useful for cron job failure monitoring."""
    today_str = date.today().strftime("%d %b %Y")
    text = (
        f"⚠️ <b>Screener Error — {today_str}</b>\n\n"
        f"<b>Where:</b> {context}\n"
        f"<b>Error:</b> <code>{error[:500]}</code>"
    )
    return _send(text)


def send_fii_status(fii_rows: list, stored: int) -> bool:
    """
    Send FII data collection status alert (5 PM IST).

    fii_rows: list of (trade_date, fii_net_fut, fii_net_opt) from DB
    stored:   number of rows stored/updated this run
    """
    today_str = date.today().strftime("%d %b %Y")

    if stored == 0 and not fii_rows:
        text = (
            f"⚠️ <b>FII Data — {today_str}</b>\n\n"
            f"No data fetched from NSE. Market closed or data not published yet."
        )
        return _send(text)

    lines = [f"<b>🏦 FII Flow Update — {today_str}</b>\n"]

    def _cr(v) -> str:
        try:
            f = float(v)
            sign = "+" if f >= 0 else ""
            return f"{sign}{f:,.0f} Cr"
        except Exception:
            return "—"

    def _sentiment(v) -> str:
        try:
            f = float(v)
            if   f >  2000: return "🟢 Strong Buy"
            elif f >   500: return "🟢 Buy"
            elif f >  -500: return "⚪ Neutral"
            elif f > -2000: return "🔴 Sell"
            else:           return "🔴 Strong Sell"
        except Exception:
            return "—"

    for row in fii_rows[:5]:
        d, fii_net, dii_net = row
        fii_s = _sentiment(fii_net)
        lines.append(
            f"  <b>{d}</b>  FII: {_cr(fii_net)} {fii_s}  |  DII: {_cr(dii_net)}"
        )

    # 5-day cumulative
    if len(fii_rows) >= 2:
        cum = sum(float(r[1] or 0) for r in fii_rows[:5])
        lines.append(f"\n  5-day FII cumulative: <b>{_cr(cum)}</b>")
        if cum > 0:
            lines.append("  Trend: 🟢 Net Accumulation")
        else:
            lines.append("  Trend: 🔴 Net Distribution")

    lines.append(f"\n<i>Stored {stored} row(s) · {today_str}</i>")
    return _send("\n".join(lines))


def send_outcomes_status(recorded: int, accuracy_rows: list) -> bool:
    """
    Send outcomes recording status alert (6:30 PM IST).

    recorded:      number of outcomes recorded this run
    accuracy_rows: result of get_accuracy_summary()
    """
    today_str = date.today().strftime("%d %b %Y")
    lines = [f"<b>📋 Outcomes Recorder — {today_str}</b>\n"]

    if recorded == 0:
        lines.append("  No new expiries to record today.")
    else:
        lines.append(f"  ✅ Recorded <b>{recorded}</b> new outcome(s).")

    if accuracy_rows:
        lines.append("\n<b>Model Accuracy (running total):</b>")
        for row in accuracy_rows:
            inst  = row.get("instrument", "")
            ptype = row.get("prediction_type", "")
            wk    = row.get("week_num")
            acc   = row.get("accuracy_pct") or 0
            total = row.get("total_predictions", 0)
            corr  = row.get("correct", 0)
            wk_label = f"W{wk}" if wk else "Monthly"
            icon = "✅" if float(acc) >= 55 else "⚠️"
            lines.append(
                f"  {icon} {inst} {wk_label}: <b>{acc}%</b>  ({corr}/{total})"
            )
    else:
        lines.append("\n  <i>Accuracy data not yet available — need 4+ weeks of expiries.</i>")

    lines.append(f"\n<i>{today_str}</i>")
    return _send("\n".join(lines))
