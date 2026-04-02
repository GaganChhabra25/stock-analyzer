-- ============================================================
--  Options Data Schema
--  Run ONCE against the stock_analyzer database:
--    psql -U stockanalyzer -d stock_analyzer -h localhost -f options/schema.sql
--
--  Existing installs: run the migration block at the bottom.
-- ============================================================


-- ── Users (one row per Zerodha account) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    zerodha_user_id  VARCHAR(20)  PRIMARY KEY,   -- e.g. "AB1234" from kite.profile()
    email            TEXT,
    full_name        TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_login_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE users IS
    'One row per Zerodha account. Populated on first /kite/callback login.';


-- ── Kite Connect daily tokens (per user) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS kite_tokens (
    zerodha_user_id  VARCHAR(20)  NOT NULL REFERENCES users(zerodha_user_id) ON DELETE CASCADE,
    token_date       DATE         NOT NULL,
    access_token     TEXT         NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (zerodha_user_id, token_date)
);

COMMENT ON TABLE kite_tokens IS
    'One row per user per day. access_token expires at midnight; regenerated each morning via /kite/login.';


-- ── Per-minute option chain snapshots (shared market data) ───────────────────
CREATE TABLE IF NOT EXISTS option_chain (
    id              BIGSERIAL       PRIMARY KEY,
    ts              TIMESTAMPTZ     NOT NULL,
    instrument      VARCHAR(20)     NOT NULL,   -- NIFTY | BANKNIFTY
    expiry          DATE            NOT NULL,
    strike          INTEGER         NOT NULL,
    option_type     CHAR(2)         NOT NULL,   -- CE | PE
    ltp             NUMERIC(10,2),
    bid             NUMERIC(10,2),
    ask             NUMERIC(10,2),
    oi              BIGINT,
    oi_change       BIGINT,                     -- vs previous minute
    volume          BIGINT,
    iv              NUMERIC(6,2),               -- implied volatility %
    delta           NUMERIC(7,4),
    gamma           NUMERIC(9,6),
    theta           NUMERIC(9,4),
    vega             NUMERIC(9,4),
    underlying_ltp  NUMERIC(10,2)               -- Nifty/BankNifty spot at capture time
);

CREATE INDEX IF NOT EXISTS idx_oc_lookup
    ON option_chain (instrument, ts DESC, expiry, strike, option_type);

CREATE INDEX IF NOT EXISTS idx_oc_ts
    ON option_chain (ts DESC);

COMMENT ON TABLE option_chain IS
    'Per-minute option chain. Shared across all users (market data). ~52 rows/min. ~5M rows/year.';


-- ── Per-minute market snapshots (shared market data) ──────────────────────────
CREATE TABLE IF NOT EXISTS market_snapshot (
    id              BIGSERIAL       PRIMARY KEY,
    ts              TIMESTAMPTZ     NOT NULL,
    instrument      VARCHAR(20)     NOT NULL,   -- NIFTY | BANKNIFTY
    spot_price      NUMERIC(10,2),
    vix             NUMERIC(6,2),
    pcr_oi          NUMERIC(6,3),               -- put-call ratio by OI
    atm_strike      INTEGER,
    atm_straddle    NUMERIC(10,2),              -- ATM CE ltp + ATM PE ltp
    expected_move   NUMERIC(6,2),               -- straddle/spot * 100 (%)
    call_oi_wall    INTEGER,                    -- strike with highest CE OI
    put_oi_wall     INTEGER                     -- strike with highest PE OI
);

CREATE INDEX IF NOT EXISTS idx_ms_lookup
    ON market_snapshot (instrument, ts DESC);

COMMENT ON TABLE market_snapshot IS
    'Derived market-level metrics per minute. Shared across all users. Used for range prediction model.';


-- ── Sanity check ──────────────────────────────────────────────────────────────
SELECT
    'Options schema ready' AS status,
    (SELECT COUNT(*) FROM information_schema.tables
     WHERE table_schema = 'public'
       AND table_name IN ('users', 'kite_tokens', 'option_chain', 'market_snapshot')) AS tables_created;


-- ============================================================
--  MIGRATION (existing installs only)
--  Run these if kite_tokens already exists without user support:
-- ============================================================
--
--  ALTER TABLE kite_tokens DROP CONSTRAINT kite_tokens_pkey;
--  ALTER TABLE kite_tokens ADD COLUMN zerodha_user_id VARCHAR(20);
--
--  -- Create users table first, then insert a placeholder for existing tokens:
--  -- INSERT INTO users (zerodha_user_id, email, full_name)
--  --   VALUES ('ADMIN', 'admin@example.com', 'Admin')
--  --   ON CONFLICT DO NOTHING;
--  -- UPDATE kite_tokens SET zerodha_user_id = 'ADMIN' WHERE zerodha_user_id IS NULL;
--
--  ALTER TABLE kite_tokens ALTER COLUMN zerodha_user_id SET NOT NULL;
--  ALTER TABLE kite_tokens ADD PRIMARY KEY (zerodha_user_id, token_date);
--  ALTER TABLE kite_tokens ADD CONSTRAINT fk_kt_user
--      FOREIGN KEY (zerodha_user_id) REFERENCES users(zerodha_user_id) ON DELETE CASCADE;
