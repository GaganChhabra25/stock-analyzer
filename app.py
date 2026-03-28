"""
Stock Analyzer — Flask Web App
Serves portfolio and screener reports with Google OAuth login.
Run with gunicorn in production:
    gunicorn -w 2 -b 127.0.0.1:5000 app:app
"""

import os
import sys
import threading
import subprocess
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (Flask, redirect, url_for, session, render_template,
                   send_file, abort, jsonify)
from authlib.integrations.flask_client import OAuth

# ── Bootstrap ──────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]
app.permanent_session_lifetime = timedelta(days=7)

# ── Allowed emails: env var takes precedence over config.py ───────────────────

_env_emails = os.environ.get("ALLOWED_EMAILS", "")
if _env_emails:
    ALLOWED_EMAILS = {e.strip().lower() for e in _env_emails.split(",") if e.strip()}
else:
    from config import ALLOWED_EMAILS  # type: ignore
    ALLOWED_EMAILS = {e.lower() for e in ALLOWED_EMAILS}

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
            cwd=str(BASE_DIR),
            timeout=600,
        )
        log = result.stdout + result.stderr
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


@app.route("/reports/<name>")
@login_required
def serve_report(name):
    allowed = {"portfolio_report.html", "screener_report.html"}
    if name not in allowed:
        abort(404)
    path = BASE_DIR / "reports" / name
    if not path.exists():
        abort(404)
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


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
