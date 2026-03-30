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
    redirect_uri = url_for("auth_callback", _external=True)
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


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
