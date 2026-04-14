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


-- ── MCX futures OHLC candles ──────────────────────────────────────────────────
-- Stores daily + 15-min OHLC for NATURALGAS and CRUDEOIL futures.
-- Source: Kite Connect historical API (near-month futures contract).
-- Used for: ATR, S/R levels, trend, intraday setup for option selling.
CREATE TABLE IF NOT EXISTS mcx_ohlc (
    id          BIGSERIAL       PRIMARY KEY,
    ts          TIMESTAMPTZ     NOT NULL,           -- candle open time (IST-aware)
    instrument  VARCHAR(20)     NOT NULL,           -- NATURALGAS | CRUDEOIL
    interval    VARCHAR(10)     NOT NULL,           -- 'day' | '15minute'
    open        NUMERIC(12,2),
    high        NUMERIC(12,2),
    low         NUMERIC(12,2),
    close       NUMERIC(12,2),
    volume      BIGINT,
    oi          BIGINT,
    UNIQUE (ts, instrument, interval)
);

CREATE INDEX IF NOT EXISTS idx_mcx_ohlc_lookup
    ON mcx_ohlc (instrument, interval, ts DESC);

COMMENT ON TABLE mcx_ohlc IS
    'MCX futures OHLC (daily + 15-min). Used for ATR, trend, S/R for MCX option selling.';


-- ── Global commodity reference prices ─────────────────────────────────────────
-- Daily OHLC for WTI Crude, Brent Crude, Henry Hub NatGas, USD/INR.
-- Source: Yahoo Finance via yfinance.
-- Used for: overnight gap analysis, MCX open prediction, correlation.
CREATE TABLE IF NOT EXISTS global_prices (
    id          BIGSERIAL       PRIMARY KEY,
    date        DATE            NOT NULL,
    symbol      VARCHAR(20)     NOT NULL,   -- WTI | BRENT | NATGAS | USDINR
    open        NUMERIC(12,4),
    high        NUMERIC(12,4),
    low         NUMERIC(12,4),
    close       NUMERIC(12,4),
    change_pct  NUMERIC(8,4),              -- day-over-day % change
    UNIQUE (date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_gp_lookup
    ON global_prices (symbol, date DESC);

COMMENT ON TABLE global_prices IS
    'Daily global prices: WTI, Brent, NatGas (Henry Hub), USD/INR. Used for MCX gap/correlation analysis.';


-- ── App users (Google OAuth allowed accounts) ────────────────────────────────
-- Stores emails allowed to log in via Google OAuth.
-- Seeded from config.py ALLOWED_EMAILS on first use if table is empty.
CREATE TABLE IF NOT EXISTS app_users (
    email       TEXT        PRIMARY KEY,
    is_admin    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE app_users IS
    'Google OAuth allowed accounts. Managed via /users UI. Seeded from config.py on first use.';


-- ── Sanity check ──────────────────────────────────────────────────────────────
SELECT
    'Options schema ready' AS status,
    (SELECT COUNT(*) FROM information_schema.tables
     WHERE table_schema = 'public'
       AND table_name IN ('users', 'kite_tokens', 'option_chain', 'market_snapshot',
                          'mcx_ohlc', 'global_prices', 'app_users')) AS tables_created;


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
