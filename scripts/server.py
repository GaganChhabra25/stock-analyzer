"""
scripts/server.py — Contabo server management CLI

Usage:
  python scripts/server.py status          # collector + watchdog process status
  python scripts/server.py logs [n]        # tail options.log (default 30 lines)
  python scripts/server.py start           # manually start collector
  python scripts/server.py stop            # kill collector process
  python scripts/server.py cron            # show current crontab (live on server)
  python scripts/server.py cron-apply      # apply deploy/contabo/crontab to server
  python scripts/server.py db              # last DB ingestion timestamp
  python scripts/server.py run <cmd>       # run arbitrary command on server
"""

import sys
import os
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
import paramiko

# ── Server config ────────────────────────────────────────────────────────────
HOST     = "185.211.6.5"
USER     = "root"
PASSWORD = "S3p3hU9zqtmQh4c1X8r2opF"
APP_DIR  = "/home/stockapp/stock-analyzer"
VENV_PY  = f"{APP_DIR}/venv/bin/python"
APP_USER = "stockapp"

# ── SSH helper ───────────────────────────────────────────────────────────────
def connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=15)
    return client


def run(client, cmd):
    _, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return out, err


def ssh_run(cmd):
    """One-shot: connect, run, print, disconnect."""
    with connect() as c:
        out, err = run(c, cmd)
        if out: print(out)
        if err: print("STDERR:", err)

# ── Commands ─────────────────────────────────────────────────────────────────
def cmd_status():
    client = connect()
    out, _ = run(client, "ps aux | grep -E 'collector\\.py|watchdog\\.py' | grep -v grep")
    if out:
        print("Running processes:")
        for line in out.splitlines():
            parts = line.split()
            print(f"  PID {parts[1]:>6}  {' '.join(parts[10:])}")
    else:
        print("No collector/watchdog process running.")

    # Last log line
    out2, _ = run(client, f"tail -1 {APP_DIR}/logs/options.log")
    print(f"\nLast options.log entry:\n  {out2}")
    client.close()


def cmd_logs(n=30):
    ssh_run(f"tail -{n} {APP_DIR}/logs/options.log")


def cmd_start():
    client = connect()
    # Check if already running
    out, _ = run(client, "ps aux | grep 'collector\\.py' | grep -v grep")
    if out:
        print("Collector already running:")
        print(f"  {out.splitlines()[0].split()[1]}")
        client.close()
        return

    run(client, f"cd {APP_DIR} && nohup {VENV_PY} options/collector.py "
                f">> {APP_DIR}/logs/options.log 2>&1 &")

    import time; time.sleep(3)
    out2, _ = run(client, f"tail -3 {APP_DIR}/logs/options.log")
    print("Collector started:\n" + out2)
    client.close()


def cmd_stop():
    client = connect()
    out, _ = run(client, "pgrep -f 'collector.py'")
    if not out:
        print("No collector process found.")
        client.close()
        return
    pids = out.strip().splitlines()
    run(client, f"kill {' '.join(pids)}")
    print(f"Killed PIDs: {', '.join(pids)}")
    client.close()


def cmd_cron():
    ssh_run(f"crontab -u {APP_USER} -l")


def cmd_cron_apply():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_crontab = os.path.join(repo_root, "deploy", "contabo", "crontab")

    if not os.path.exists(local_crontab):
        print(f"ERROR: {local_crontab} not found.")
        sys.exit(1)

    with open(local_crontab) as f:
        content = f.read()

    client = connect()
    # Upload file to server then apply
    sftp = client.open_sftp()
    remote_path = "/tmp/stockapp_crontab"
    with sftp.open(remote_path, "w") as rf:
        rf.write(content)
    sftp.close()

    out, err = run(client, f"crontab -u {APP_USER} {remote_path} && echo OK")
    print("Applied." if "OK" in out else f"Failed: {err}")

    # Show what's live now
    out2, _ = run(client, f"crontab -u {APP_USER} -l")
    print("\nLive crontab:\n" + out2)
    client.close()


def cmd_db():
    client = connect()
    tables_out, _ = run(client,
        "sudo -u postgres psql -d stock_analyzer -c "
        "\"SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' ORDER BY table_name;\"")
    print("Tables:", tables_out)

    # Try common timestamp columns
    for query in [
        "SELECT MAX(timestamp) FROM option_chain_data;",
        "SELECT MAX(created_at) FROM option_chain_data;",
        "SELECT MAX(timestamp) FROM options_data;",
    ]:
        out, err = run(client,
            f"sudo -u postgres psql -d stock_analyzer -c \"{query}\" 2>&1")
        if "ERROR" not in out:
            print(f"\nLast ingestion ({query.split('FROM')[1].strip()[:-1]}):")
            print(out)
            break
    client.close()


def cmd_run(command):
    ssh_run(command)

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "status":
        cmd_status()
    elif cmd == "logs":
        n = int(args[1]) if len(args) > 1 else 30
        cmd_logs(n)
    elif cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "cron":
        cmd_cron()
    elif cmd == "cron-apply":
        cmd_cron_apply()
    elif cmd == "db":
        cmd_db()
    elif cmd == "run":
        if len(args) < 2:
            print("Usage: python scripts/server.py run <command>")
            sys.exit(1)
        cmd_run(" ".join(args[1:]))
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
