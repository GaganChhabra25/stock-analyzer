-- ============================================================
--  Options Data Schema
--  Run ONCE against the stock_analyzer database:
--    psql -U stockanalyzer -d stock_analyzer -h localhost -f options/schema.sql
-- ============================================================


-- ── Kite Connect daily tokens ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kite_tokens (
    token_date    DATE        PRIMARY KEY,
    access_token  TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE kite_tokens IS
    'One row per day. access_token expires at midnight; regenerated each morning via /kite/login.';


-- ── Per-minute option chain snapshots ────────────────────────────────────────
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
    vega            NUMERIC(9,4),
    underlying_ltp  NUMERIC(10,2)               -- Nifty/BankNifty spot at capture time
);

-- Primary query pattern: fetch all strikes for a symbol at a given minute
CREATE INDEX IF NOT EXISTS idx_oc_lookup
    ON option_chain (instrument, ts DESC, expiry, strike, option_type);

-- Range queries for backtesting
CREATE INDEX IF NOT EXISTS idx_oc_ts
    ON option_chain (ts DESC);

COMMENT ON TABLE option_chain IS
    'Per-minute option chain. ~52 rows/min (NIFTY+BankNifty, ATM±6, CE+PE). ~5M rows/year.';


-- ── Per-minute market snapshots ────────────────────────────────────────────────
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
    'Derived market-level metrics per minute. Used for range prediction model.';


-- ── Sanity check ──────────────────────────────────────────────────────────────
SELECT
    'Options schema ready' AS status,
    (SELECT COUNT(*) FROM information_schema.tables
     WHERE table_schema = 'public'
       AND table_name IN ('kite_tokens', 'option_chain', 'market_snapshot')) AS tables_created;
