"""
One-time migration: add multi-user support to existing Contabo DB.

Run on Contabo:
    cd /home/stockapp/stock-analyzer
    python scripts/migrate_multiuser.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import psycopg2

DB_URL = os.environ.get("DATABASE_URL", "")
if not DB_URL:
    print("ERROR: DATABASE_URL not set in .env"); sys.exit(1)

conn = psycopg2.connect(DB_URL)
conn.autocommit = False
cur = conn.cursor()

print("Connected to:", DB_URL.split("@")[-1])

# ── 1. Create users table ─────────────────────────────────────────────────────
cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        zerodha_user_id  VARCHAR(20)  PRIMARY KEY,
        email            TEXT,
        full_name        TEXT,
        created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        last_login_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
""")
print("✓ users table ready")

# ── 2. Check kite_tokens schema ───────────────────────────────────────────────
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'kite_tokens'
""")
cols = {r[0] for r in cur.fetchall()}

if not cols:
    # Table doesn't exist — create fresh
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kite_tokens (
            zerodha_user_id  VARCHAR(20)  NOT NULL REFERENCES users(zerodha_user_id) ON DELETE CASCADE,
            token_date       DATE         NOT NULL,
            access_token     TEXT         NOT NULL,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            PRIMARY KEY (zerodha_user_id, token_date)
        )
    """)
    print("✓ kite_tokens table created (fresh)")

elif 'zerodha_user_id' not in cols:
    # Old single-user schema — migrate
    ADMIN_UID = os.environ.get("KITE_ADMIN_USER_ID", "ZT8659")

    # Insert admin user placeholder
    cur.execute("""
        INSERT INTO users (zerodha_user_id, email, full_name)
        VALUES (%s, '', 'Admin')
        ON CONFLICT DO NOTHING
    """, (ADMIN_UID,))

    # Drop old PK, add column, set values, new PK + FK
    cur.execute("ALTER TABLE kite_tokens DROP CONSTRAINT kite_tokens_pkey")
    cur.execute("ALTER TABLE kite_tokens ADD COLUMN zerodha_user_id VARCHAR(20)")
    cur.execute("UPDATE kite_tokens SET zerodha_user_id = %s WHERE zerodha_user_id IS NULL", (ADMIN_UID,))
    cur.execute("ALTER TABLE kite_tokens ALTER COLUMN zerodha_user_id SET NOT NULL")
    cur.execute("ALTER TABLE kite_tokens ADD PRIMARY KEY (zerodha_user_id, token_date)")
    cur.execute("""
        ALTER TABLE kite_tokens ADD CONSTRAINT fk_kt_user
            FOREIGN KEY (zerodha_user_id) REFERENCES users(zerodha_user_id) ON DELETE CASCADE
    """)
    print(f"✓ kite_tokens migrated (admin user: {ADMIN_UID})")

else:
    print("✓ kite_tokens already migrated")

# ── 3. Create option_chain if missing ────────────────────────────────────────
cur.execute("""
    CREATE TABLE IF NOT EXISTS option_chain (
        id              BIGSERIAL       PRIMARY KEY,
        ts              TIMESTAMPTZ     NOT NULL,
        instrument      VARCHAR(20)     NOT NULL,
        expiry          DATE            NOT NULL,
        strike          INTEGER         NOT NULL,
        option_type     CHAR(2)         NOT NULL,
        ltp             NUMERIC(10,2),
        bid             NUMERIC(10,2),
        ask             NUMERIC(10,2),
        oi              BIGINT,
        oi_change       BIGINT,
        volume          BIGINT,
        iv              NUMERIC(6,2),
        delta           NUMERIC(7,4),
        gamma           NUMERIC(9,6),
        theta           NUMERIC(9,4),
        vega            NUMERIC(9,4),
        underlying_ltp  NUMERIC(10,2)
    )
""")
cur.execute("CREATE INDEX IF NOT EXISTS idx_oc_lookup ON option_chain (instrument, ts DESC, expiry, strike, option_type)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_oc_ts ON option_chain (ts DESC)")
print("✓ option_chain table ready")

# ── 4. Create market_snapshot if missing ─────────────────────────────────────
cur.execute("""
    CREATE TABLE IF NOT EXISTS market_snapshot (
        id              BIGSERIAL       PRIMARY KEY,
        ts              TIMESTAMPTZ     NOT NULL,
        instrument      VARCHAR(20)     NOT NULL,
        spot_price      NUMERIC(10,2),
        vix             NUMERIC(6,2),
        pcr_oi          NUMERIC(6,3),
        atm_strike      INTEGER,
        atm_straddle    NUMERIC(10,2),
        expected_move   NUMERIC(6,2),
        call_oi_wall    INTEGER,
        put_oi_wall     INTEGER
    )
""")
cur.execute("CREATE INDEX IF NOT EXISTS idx_ms_lookup ON market_snapshot (instrument, ts DESC)")
print("✓ market_snapshot table ready")

conn.commit()
cur.close()
conn.close()
print("\nMigration complete. Now:")
print("  1. Add KITE_ADMIN_USER_ID=ZT8659 to .env on Contabo")
print("  2. Visit /kite/login to save today's token")
print("  3. Restart: systemctl restart stock-analyzer")
