"""
Stock Analyzer — Flask Web App
Serves portfolio and screener reports with Google OAuth login.
Run with gunicorn in production:
    gunicorn -w 2 -b 127.0.0.1:5000 app:app
"""

import os
import sys
import json
import threading
import subprocess
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

# Load .env for local development (no-op if file absent or vars already set)
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from flask import (Flask, redirect, url_for, session, render_template,
                   send_file, abort, jsonify, request)
from authlib.integrations.flask_client import OAuth

# ── Bootstrap ──────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]
app.permanent_session_lifetime = timedelta(days=7)

# Trust X-Forwarded-Proto from Nginx so url_for() generates https:// URLs
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# ── Allowed emails: union of env var + config.py ──────────────────────────────

from config import ALLOWED_EMAILS as _cfg_emails  # type: ignore
from screener.db import (get_accuracy_summary, get_signal_importance,
                         get_recent_predictions, is_available as db_available,
                         _get_conn)
ALLOWED_EMAILS = {e.lower() for e in _cfg_emails}
_env_emails = os.environ.get("ALLOWED_EMAILS", "")
if _env_emails:
    ALLOWED_EMAILS |= {e.strip().lower() for e in _env_emails.split(",") if e.strip()}

# ── Google OAuth ──────────────────────────────────────────────────────────────

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ── Auth decorator ────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ── Background job runner ─────────────────────────────────────────────────────
# Keeps the last run state for each job so the dashboard can poll it.

_jobs: dict = {}
_lock = threading.Lock()


def _run_job(job_id: str, cmd: list):
    with _lock:
        _jobs[job_id] = {
            "status": "running",
            "started_at": datetime.now().strftime("%d %b %Y %I:%M %p"),
            "finished_at": None,
            "log": "",
        }
    try:
        result = subprocess.run(
            [sys.executable] + cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(BASE_DIR),
            timeout=600,
        )
        log = (result.stdout or "") + (result.stderr or "")
        status = "done" if result.returncode == 0 else "error"
    except subprocess.TimeoutExpired:
        log = "Analysis timed out after 10 minutes."
        status = "error"
    except Exception as exc:
        log = str(exc)
        status = "error"

    with _lock:
        _jobs[job_id].update({
            "status": status,
            "finished_at": datetime.now().strftime("%d %b %Y %I:%M %p"),
            "log": log[-4000:],
        })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _report_info(filename: str) -> dict:
    path = BASE_DIR / "reports" / filename
    if path.exists():
        ts = datetime.fromtimestamp(path.stat().st_mtime)
        return {"exists": True, "updated": ts.strftime("%d %b %Y %I:%M %p")}
    return {"exists": False, "updated": None}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    reports = {
        "portfolio": _report_info("portfolio_report.html"),
        "screener":  _report_info("screener_report.html"),
    }
    with _lock:
        jobs = dict(_jobs)
    return render_template("dashboard.html", user=session["user"],
                           reports=reports, jobs=jobs)


@app.route("/login")
def login():
    if "user" in session:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/auth/google")
def auth_google():
    scheme = "https" if request.headers.get("X-Forwarded-Proto", "http") == "https" else request.scheme
    redirect_uri = url_for("auth_callback", _external=True, _scheme=scheme)
    return google.authorize_redirect(redirect_uri)


@app.route("/auth/callback")
def auth_callback():
    token = google.authorize_access_token()
    userinfo = token.get("userinfo")
    if not userinfo or not userinfo.get("email"):
        abort(400)

    email = userinfo["email"].lower()
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        return render_template("login.html", error=(
            f"Access denied for {email}. "
            "This app is restricted to specific accounts."
        )), 403

    session.permanent = True
    session["user"] = {
        "email": email,
        "name":  userinfo.get("name", email),
        "picture": userinfo.get("picture", ""),
    }
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Kite Connect OAuth routes ──────────────────────────────────────────────────

@app.route("/auth/zerodha")
def auth_zerodha():
    """Redirect to Zerodha Kite Connect login for user authentication."""
    from options.kite_auth import get_login_url
    return redirect(get_login_url())


@app.route("/kite/login")
@login_required
def kite_login():
    """Redirect to Zerodha login page to re-authorize Kite Connect."""
    from options.kite_auth import get_login_url
    return redirect(get_login_url())


@app.route("/kite/callback")
def kite_callback():
    """
    Zerodha redirects here after login with ?request_token=XXX.
    1. Exchange request_token for access token and save to DB.
    2. Fetch Zerodha profile and create a Flask login session.
    """
    request_token = request.args.get("request_token")
    status        = request.args.get("status")

    if status != "success" or not request_token:
        return render_template("login.html", error="Zerodha login was cancelled or failed."), 400

    try:
        from options.kite_auth import generate_access_token, get_kite, send_auth_success_telegram
        generate_access_token(request_token)
        # Notify via Telegram that auth succeeded
        threading.Thread(target=send_auth_success_telegram, daemon=True).start()
    except Exception as exc:
        return render_template("login.html", error=f"Zerodha auth failed: {exc}"), 500

    # Set Flask session so the user is logged in
    if "user" not in session:
        try:
            kite    = get_kite()
            profile = kite.profile()
            session.permanent = True
            session["user"] = {
                "email":   profile.get("email", profile.get("user_id", "zerodha-user")),
                "name":    profile.get("user_name", profile.get("user_id", "Zerodha User")),
                "picture": "",
                "via":     "zerodha",
            }
        except Exception:
            pass  # Token saved; session login optional

    return redirect(url_for("index"))


@app.route("/kite/status")
@login_required
def kite_status():
    """Show Kite Connect authorization status."""
    from options.kite_auth import get_kite, get_login_url
    kite = get_kite()
    if kite:
        try:
            profile = kite.profile()
            name    = profile.get("user_name", "Unknown")
            return (
                f"<h2>Kite Connected</h2>"
                f"<p>Authorized as: <strong>{name}</strong></p>"
                f"<p>Options data collection is active today.</p>"
                f"<p><a href='{url_for('index')}'>Back to dashboard</a></p>"
            )
        except Exception:
            pass
    login_url = get_login_url()
    return (
        f"<h2>Kite Not Authorized</h2>"
        f"<p>Click below to authorize Kite Connect for today:</p>"
        f"<p><a href='{login_url}'>Authorize Kite Connect</a></p>"
    )


_SCREENER_SYNC_SCRIPT = """
<script>
/* Cross-device active-trades sync — injected by Flask */
(function () {
  var SK = 'screener_active_trades';
  function _ls() {
    try { return JSON.parse(localStorage.getItem(SK) || '{}'); } catch(e) { return {}; }
  }
  function _post(trades) {
    fetch('/api/ui-trades', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(trades)
    }).catch(function () {});
  }
  /* Wrap saveActiveTrades so every UI action also syncs to server */
  if (typeof saveActiveTrades === 'function') {
    var _orig = saveActiveTrades;
    saveActiveTrades = function (t) { _orig(t); _post(t); };
  }
  /* On page load: pull server state, merge, re-render */
  document.addEventListener('DOMContentLoaded', function () {
    fetch('/api/ui-trades')
      .then(function (r) { return r.ok ? r.json() : {}; })
      .catch(function () { return {}; })
      .then(function (sv) {
        var local  = _ls();
        var merged = Object.assign({}, local, sv);
        var lLen   = Object.keys(local).length;
        var mLen   = Object.keys(merged).length;
        if (mLen > lLen) {
          /* Server had trades this device didn't — update localStorage */
          try { localStorage.setItem(SK, JSON.stringify(merged)); } catch(e) {}
          if (typeof renderActiveTrades === 'function') renderActiveTrades();
          Object.keys(merged).forEach(function (sym) {
            var cb  = document.getElementById('cb_'  + sym);
            var lbl = document.getElementById('tl_'  + sym);
            if (cb)  cb.checked = true;
            if (lbl) lbl.style.display = 'block';
          });
        }
        if (mLen > Object.keys(sv).length) {
          /* This device had trades the server didn't — push up */
          _post(merged);
        }
      });
  });
})();
</script>
"""


@app.route("/reports/<name>")
@login_required
def serve_report(name):
    allowed = {"portfolio_report.html", "screener_report.html"}
    if name not in allowed:
        abort(404)
    path = BASE_DIR / "reports" / name
    if not path.exists():
        abort(404)
    if name == "screener_report.html":
        html = path.read_text(encoding="utf-8")
        html = html.replace("</body>", _SCREENER_SYNC_SCRIPT + "</body>", 1)
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    return send_file(path)


@app.route("/run/<job>", methods=["POST"])
@login_required
def run_job(job):
    job_cmds = {
        "portfolio": ["main.py"],
        "screener":  ["run_screener.py", "--no-open"],
    }
    if job not in job_cmds:
        abort(404)

    with _lock:
        if _jobs.get(job, {}).get("status") == "running":
            return jsonify({"status": "already_running"}), 409

    thread = threading.Thread(
        target=_run_job,
        args=(job, job_cmds[job]),
        daemon=True,
    )
    thread.start()
    return jsonify({"status": "started"})


@app.route("/job-status/<job>")
@login_required
def job_status(job):
    with _lock:
        info = _jobs.get(job, {"status": "idle"})
    # Also include latest report mtime so dashboard can refresh link
    report_map = {
        "portfolio": "portfolio_report.html",
        "screener":  "screener_report.html",
    }
    if job in report_map:
        info = dict(info)
        info["report"] = _report_info(report_map[job])
    return jsonify(info)


# ── Active trades API (cross-device sync) ────────────────────────────────────

_UI_TRADES_FILE = BASE_DIR / "data" / "ui_active_trades.json"


@app.route("/api/ui-trades", methods=["GET"])
@login_required
def get_ui_trades():
    if _UI_TRADES_FILE.exists():
        with open(_UI_TRADES_FILE, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify({})


@app.route("/api/ui-trades", methods=["POST"])
@login_required
def save_ui_trades():
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        abort(400)
    with open(_UI_TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return jsonify({"ok": True})


# ── Insights helpers ──────────────────────────────────────────────────────────

def _get_insights() -> dict:
    """Fetch all data needed for the insights page from PostgreSQL."""
    empty = {
        "available": False,
        "summary": {},
        "predictions": [],
        "outcomes": [],
        "accuracy": [],
        "signals": [],
    }
    if not db_available():
        return empty

    try:
        with _get_conn() as conn:
            if conn is None:
                return empty
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # Summary counts
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM market_predictions)  AS total_predictions,
                    (SELECT COUNT(*) FROM market_outcomes)      AS total_outcomes,
                    (SELECT COUNT(DISTINCT instrument)
                     FROM market_predictions)                   AS instruments,
                    (SELECT MIN(run_date) FROM market_predictions) AS tracking_since
            """)
            summary = dict(cur.fetchone() or {})

            # Latest unique predictions per expiry (most recent run_date wins)
            cur.execute("""
                SELECT DISTINCT ON (instrument, expiry_date, prediction_type)
                    instrument, prediction_type, expiry_date, direction,
                    probability, cmp, hist_wr, pcr_value, tech_signal,
                    vix_signal, global_signal, run_date
                FROM market_predictions
                WHERE expiry_date >= CURRENT_DATE
                ORDER BY instrument, expiry_date, prediction_type, run_date DESC
            """)
            predictions = [dict(r) for r in cur.fetchall()]

            # Outcomes joined with original prediction
            cur.execute("""
                SELECT
                    o.instrument, o.expiry_date, o.prediction_type,
                    o.actual_direction, o.actual_close, o.actual_pct,
                    o.recorded_at::date AS recorded_date,
                    p.direction AS predicted_direction,
                    p.probability,
                    p.cmp AS predicted_cmp,
                    (p.direction = o.actual_direction) AS was_correct
                FROM market_outcomes o
                JOIN LATERAL (
                    SELECT direction, probability, cmp
                    FROM market_predictions p2
                    WHERE p2.instrument     = o.instrument
                      AND p2.expiry_date    = o.expiry_date
                      AND p2.prediction_type = o.prediction_type
                    ORDER BY run_date DESC LIMIT 1
                ) p ON true
                ORDER BY o.expiry_date DESC
                LIMIT 30
            """)
            outcomes = [dict(r) for r in cur.fetchall()]

            cur.close()

        accuracy = get_accuracy_summary()
        signals  = get_signal_importance()

        return {
            "available":   True,
            "summary":     summary,
            "predictions": predictions,
            "outcomes":    outcomes,
            "accuracy":    accuracy,
            "signals":     signals,
        }
    except Exception as exc:
        return {**empty, "available": True, "error": str(exc)}


# ── Insights route ─────────────────────────────────────────────────────────────

@app.route("/insights")
@login_required
def insights():
    data = _get_insights()
    return render_template("insights.html", user=session["user"], data=data)


# ── Nifty Options Intelligence ───────────────────────────────────────────────

@app.route("/nifty")
@login_required
def nifty_intelligence():
    from options.nifty_analysis import get_nifty_intelligence
    try:
        data = get_nifty_intelligence()
    except Exception as exc:
        data = {"available": False, "error": str(exc)}
    return render_template("nifty_levels.html", user=session["user"], data=data)


# ── Auto-exit background thread (starts once on app boot) ─────────────────────

def _start_auto_exit_thread():
    """Runs auto_exit_check() every 60s during market hours."""
    import time as _time
    def _loop():
        while True:
            try:
                from options.trade_executor import auto_exit_check
                auto_exit_check()
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("auto_exit_check error: %s", exc)
            _time.sleep(60)
    t = threading.Thread(target=_loop, daemon=True, name="auto-exit")
    t.start()

_start_auto_exit_thread()


# ── Weekly Range Prediction ───────────────────────────────────────────────────

@app.route("/weekly")
@login_required
def weekly_range():
    return render_template("weekly_range.html", user=session["user"])


def _fetch_db_ltps(instrument: str, expiry, strikes_types: list) -> dict:
    """
    Get last known LTP from option_chain DB for given strikes.
    Works after market hours — returns the last collected price (closing price).
    Returns {(strike, option_type): ltp}.
    """
    from screener.db import _get_conn, is_available
    result = {}
    if not is_available():
        return result
    try:
        with _get_conn() as conn:
            if conn is None:
                return result
            with conn.cursor() as cur:
                for strike, otype in strikes_types:
                    cur.execute("""
                        SELECT ltp FROM option_chain
                        WHERE instrument  = %s
                          AND expiry      = %s
                          AND strike      = %s
                          AND option_type = %s
                          AND ltp IS NOT NULL
                        ORDER BY ts DESC LIMIT 1
                    """, (instrument.upper(), expiry, int(strike), otype))
                    row = cur.fetchone()
                    if row and row[0]:
                        result[(strike, otype)] = float(row[0])
    except Exception as exc:
        app.logger.warning("_fetch_db_ltps failed: %s", exc)
    return result


def _fetch_live_ltps(kite, instrument: str, expiry, strikes_types: list) -> dict:
    """
    Fetch live LTP from Kite for a list of (strike, option_type) pairs.
    Returns {(strike, option_type): ltp, "_debug": {...}}.
    strikes_types: [(strike, 'CE'), (strike, 'PE'), ...]
    """
    debug = {"step": "init", "token_map": {}, "quotes_raw": {}}
    try:
        from options.instruments import load_instruments
        debug["step"] = "load_instruments"
        df = load_instruments(kite)
        debug["df_rows"] = len(df)
        debug["expiry_sample"] = str(df["expiry"].iloc[0]) if not df.empty else "empty"
        debug["expiry_dtype"] = str(df["expiry"].dtype)

        # Build token → (strike, option_type) map for requested strikes
        token_map = {}
        for strike, otype in strikes_types:
            # Use tolerance comparison for float strikes (avoid precision mismatch)
            mask = (
                df["tradingsymbol"].str.startswith(instrument) &
                (df["expiry"] == expiry) &
                ((df["strike"] - float(strike)).abs() < 0.5) &
                (df["instrument_type"] == otype)
            )
            rows = df[mask]
            debug[f"rows_{strike}_{otype}"] = len(rows)
            if not rows.empty:
                tok = int(rows.iloc[0]["instrument_token"])
                token_map[tok] = (strike, otype)
                debug["token_map"][str(tok)] = f"{strike}_{otype}"

        debug["step"] = "tokens_built"
        if not token_map:
            app.logger.warning("_fetch_live_ltps: no tokens matched. debug=%s", debug)
            return {"_debug": debug}

        debug["step"] = "kite_quote"
        quotes = kite.quote([f"NFO:{t}" for t in token_map])
        debug["quotes_raw"] = {k: v.get("last_price") for k, v in quotes.items()}
        result = {"_debug": debug}
        for sym, q in quotes.items():
            raw_tok = int(sym.split(":")[1])
            key = token_map.get(raw_tok)
            ltp = q.get("last_price")
            if key and ltp:
                result[key] = float(ltp)
        debug["step"] = "done"
        return result
    except Exception as exc:
        app.logger.warning("_fetch_live_ltps failed at step=%s: %s", debug.get("step"), exc)
        return {"_debug": debug, "_error": str(exc)}


@app.route("/api/backtest")
@login_required
def api_backtest():
    """Run Iron Fly backtest using actual DB history. Cached 4 hours server-side."""
    from options.backtest import backtest_iron_fly, find_optimal_wing_width
    instrument = request.args.get("instrument", "NIFTY").upper()
    wing_width = int(request.args.get("wing_width", 200))
    lookback   = min(int(request.args.get("lookback", 60)), 90)
    wing_scan  = request.args.get("wing_scan", "false").lower() == "true"

    if wing_scan:
        result = find_optimal_wing_width(instrument, lookback_days=lookback)
    else:
        result = backtest_iron_fly(instrument, wing_width, lookback)
    return jsonify(result)


@app.route("/api/sell-setup")
@login_required
def api_sell_setup():
    """Iron Fly intraday setup with real backtested win rate and live LTPs."""
    import traceback
    try:
        from options.range_predict import fetch_summary, get_nearest_db_expiry
        from options.intraday_setup import compute_iron_fly_setup
        from options.backtest import backtest_iron_fly
        from options.kite_auth import get_kite

        instrument  = request.args.get("instrument", "NIFTY").upper()
        expiry_type = request.args.get("expiry_type", "weekly").lower()
        wing_width  = int(request.args.get("wing_width", 200))

        expiry = get_nearest_db_expiry(instrument, expiry_type)
        snap   = fetch_summary(expiry, instrument)
        if not snap:
            return jsonify({"error": "DB unavailable or no data yet"}), 503

        # Fetch backtest (uses its own 4-hour in-process cache)
        bt = backtest_iron_fly(instrument, wing_width, lookback_days=60)

        setup = compute_iron_fly_setup(snap, expiry, instrument, wing_width, cached_backtest=bt)

        # ── Override LTPs: Kite live → DB last-known → BS approx ─────────────
        all_strikes = [
            (setup["sell_ce"], "CE"), (setup["sell_pe"], "PE"),
            (setup["buy_ce"],  "CE"), (setup["buy_pe"],  "PE"),
        ]
        kite = get_kite()
        prices = {}
        ltp_source = setup.get("ltp_source", "bs_approx")

        # 1. Kite live
        if kite:
            live = _fetch_live_ltps(kite, instrument, expiry, all_strikes)
            setup["_ltp_debug"] = live.get("_debug", {})
            prices = {k: v for k, v in live.items()
                      if k != "_debug" and not isinstance(k, str)}
            if prices:
                ltp_source = "kite_live" if len(prices) == 4 else "kite_partial"

        # 2. DB fallback
        if len(prices) < 4:
            db_prices = _fetch_db_ltps(instrument, expiry, all_strikes)
            for key, val in db_prices.items():
                if key not in prices:
                    prices[key] = val
            if db_prices:
                ltp_source = "db_last" if ltp_source == "bs_approx" else "kite_partial+db"

        # Apply prices to all 4 legs
        lot = setup["lot_size"]
        ce_p  = prices.get((setup["sell_ce"], "CE"))
        pe_p  = prices.get((setup["sell_pe"], "PE"))
        bce_p = prices.get((setup["buy_ce"],  "CE"))
        bpe_p = prices.get((setup["buy_pe"],  "PE"))

        if ce_p:  setup["ce_ltp"]     = round(ce_p,  1); setup["ce_sl"]  = round(ce_p  * 2.5, 1)
        if pe_p:  setup["pe_ltp"]     = round(pe_p,  1); setup["pe_sl"]  = round(pe_p  * 2.5, 1)
        if bce_p: setup["buy_ce_ltp"] = round(bce_p, 1)
        if bpe_p: setup["buy_pe_ltp"] = round(bpe_p, 1)

        # Recompute net premium + derived values
        net = round((setup["ce_ltp"] + setup["pe_ltp"])
                    - (setup["buy_ce_ltp"] + setup["buy_pe_ltp"]), 1)
        setup["net_premium"]    = net
        setup["max_profit_pts"] = net
        setup["max_loss_pts"]   = round(setup["wing_width"] - net, 1)
        setup["target_pts"]     = round(net * 0.50, 1)
        setup["sl_pts"]         = round(net, 1)
        setup["target_inr"]     = int(setup["target_pts"] * lot)
        setup["sl_inr"]         = int(setup["sl_pts"]     * lot)
        setup["max_profit_inr"] = int(net * lot)
        setup["max_loss_inr"]   = int(setup["max_loss_pts"] * lot)
        setup["ltp_source"]     = ltp_source

        # Rebuild legs with updated prices
        setup["legs"] = [
            {"action": "SELL", "strike": setup["sell_ce"], "type": "CE",
             "ltp": setup["ce_ltp"],    "sl": setup["ce_sl"]},
            {"action": "SELL", "strike": setup["sell_pe"], "type": "PE",
             "ltp": setup["pe_ltp"],    "sl": setup["pe_sl"]},
            {"action": "BUY",  "strike": setup["buy_ce"],  "type": "CE",
             "ltp": setup["buy_ce_ltp"], "sl": None},
            {"action": "BUY",  "strike": setup["buy_pe"],  "type": "PE",
             "ltp": setup["buy_pe_ltp"], "sl": None},
        ]

        setup["expiry"] = str(setup["expiry"])
        return jsonify(setup)
    except Exception as exc:
        app.logger.error("api_sell_setup error: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": str(exc)}), 500


@app.route("/api/paper-trade", methods=["POST"])
@login_required
def api_paper_trade():
    """Place a paper trade for the Iron Fly setup."""
    from options.range_predict import fetch_summary, get_nearest_db_expiry
    from options.intraday_setup import compute_iron_fly_setup
    from options.backtest import backtest_iron_fly
    from options.trade_executor import place_paper_trade
    from datetime import datetime as _dt

    data        = request.get_json(silent=True) or {}
    instrument  = data.get("instrument", "NIFTY").upper()
    expiry_type = data.get("expiry_type", "weekly").lower()
    wing_width  = int(data.get("wing_width", 200))

    expiry_str = data.get("expiry")
    if expiry_str:
        expiry = _dt.strptime(expiry_str, "%Y-%m-%d").date()
    else:
        expiry = get_nearest_db_expiry(instrument, expiry_type)

    snap = fetch_summary(expiry, instrument)
    if not snap:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    bt    = backtest_iron_fly(instrument, wing_width, lookback_days=60)
    setup = compute_iron_fly_setup(snap, expiry, instrument, wing_width, cached_backtest=bt)
    result = place_paper_trade(setup)
    return jsonify(result), (200 if result["ok"] else 500)


@app.route("/api/open-trades")
@login_required
def api_open_trades():
    """Open paper trades for today with live P&L."""
    from options.trade_executor import get_open_trades
    return jsonify(get_open_trades())


@app.route("/api/trade-history")
@login_required
def api_trade_history():
    """Closed paper trades for analysis."""
    from options.trade_executor import get_trade_history
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify(get_trade_history(limit))


@app.route("/api/exit-trade/<int:trade_id>", methods=["POST"])
@login_required
def api_exit_trade(trade_id):
    """Manually exit a single trade leg."""
    from options.trade_executor import exit_trade
    result = exit_trade(trade_id, "MANUAL")
    return jsonify(result), (200 if result["ok"] else 500)


@app.route("/api/exit-group/<group_id>", methods=["POST"])
@login_required
def api_exit_group(group_id):
    """Manually exit all legs of a group."""
    from options.trade_executor import exit_group
    result = exit_group(group_id, "MANUAL")
    return jsonify(result), (200 if result["ok"] else 500)


@app.route("/api/weekly-range")
@login_required
def api_weekly_range():
    """
    Runs fetch_summary + compute_range entirely in SQL aggregation.
    35L rows → ~13 numbers → JSON response. Never loads raw rows into Python.
    """
    from options.range_predict import fetch_summary, compute_range, get_nearest_db_expiry
    from datetime import datetime as _dt

    instrument  = request.args.get("instrument", "NIFTY").upper()
    expiry_type = request.args.get("expiry_type", "weekly").lower()

    expiry_param = request.args.get("expiry")
    if expiry_param:
        try:
            expiry = _dt.strptime(expiry_param, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Invalid expiry date. Use YYYY-MM-DD"}), 400
    else:
        # Use actual expiry from DB — correctly handles NSE holidays
        expiry = get_nearest_db_expiry(instrument, expiry_type)

    summary = fetch_summary(expiry, instrument)
    if not summary:
        return jsonify({"error": "DB unavailable or no data collected yet."}), 503

    result = compute_range(summary, expiry)
    # Make all values JSON-serialisable
    return jsonify({k: (str(v) if hasattr(v, 'isoformat') else v)
                    for k, v in result.items()})


# ── Real-time spot price from Kite ────────────────────────────────────────────

_SPOT_SYMBOLS = {
    "NIFTY":     "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
}


@app.route("/api/spot-price")
@login_required
def api_spot_price():
    """Live spot price directly from Kite — no DB, always fresh."""
    from options.kite_auth import get_kite
    from datetime import datetime
    from zoneinfo import ZoneInfo

    instrument = request.args.get("instrument", "NIFTY").upper()
    sym = _SPOT_SYMBOLS.get(instrument)
    if not sym:
        return jsonify({"error": "Unknown instrument"}), 400

    kite = get_kite()
    if kite is None:
        return jsonify({"error": "Kite not authorized — visit /kite/login"}), 503

    try:
        quote = kite.quote([sym])
        ltp   = quote[sym]["last_price"]
        ist   = datetime.now(ZoneInfo("Asia/Kolkata"))
        return jsonify({
            "instrument": instrument,
            "spot":       ltp,
            "as_of":      ist.strftime("%I:%M:%S %p IST"),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── End-of-day collection summary Telegram ────────────────────────────────────

@app.route("/api/eod-summary", methods=["POST"])
@login_required
def api_eod_summary():
    """Send end-of-day data collection summary via Telegram. Called by cron at 3:35 PM IST."""
    import os, requests as req
    from datetime import date

    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return jsonify({"ok": False, "error": "Telegram not configured"}), 400

    try:
        with _get_conn() as conn:
            if conn is None:
                return jsonify({"ok": False, "error": "DB unavailable"}), 503
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) AS rows,
                           MIN(ts)  AS first_ts,
                           MAX(ts)  AS last_ts
                    FROM option_chain
                    WHERE ts::date = CURRENT_DATE
                """)
                row = cur.fetchone()
                total_rows = row[0] if row else 0
                first_ts   = row[1].strftime("%I:%M %p IST") if row and row[1] else "N/A"
                last_ts    = row[2].strftime("%I:%M %p IST") if row and row[2] else "N/A"

                cur.execute("SELECT COUNT(*) FROM market_snapshot WHERE ts::date = CURRENT_DATE")
                snaps = cur.fetchone()[0]

        msg = (
            f"\U0001f4ca Market Data Collection Summary — {date.today().strftime('%d %b %Y')}\n\n"
            f"Option chain rows : {total_rows:,}\n"
            f"Market snapshots  : {snaps:,}\n"
            f"First collection  : {first_ts}\n"
            f"Last collection   : {last_ts}\n\n"
            "Data ingestion complete for today. \u2705"
        )
        req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=10,
        )
        return jsonify({"ok": True, "rows": total_rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
