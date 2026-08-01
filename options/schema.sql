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
    'NIFTY per-second and MCX per-minute option snapshots. Shared market data.';


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
    'Derived market-level metrics per collection snapshot. Used for analysis and range prediction.';


-- ── NIFTY near-month futures snapshots ─────────────────────────────────────
-- One full-depth snapshot per second from the dedicated NIFTY WebSocket.
CREATE TABLE IF NOT EXISTS nifty_futures (
    ts                  TIMESTAMPTZ     PRIMARY KEY,
    tradingsymbol       VARCHAR(40)     NOT NULL,
    instrument_token    BIGINT          NOT NULL,
    expiry              DATE            NOT NULL,
    exchange_ts         TIMESTAMPTZ,
    received_at         TIMESTAMPTZ     NOT NULL,
    last_price          NUMERIC(12,2),
    last_quantity       BIGINT,
    average_price       NUMERIC(12,2),
    volume              BIGINT,
    total_buy_quantity  BIGINT,
    total_sell_quantity BIGINT,
    open                NUMERIC(12,2),
    high                NUMERIC(12,2),
    low                 NUMERIC(12,2),
    previous_close      NUMERIC(12,2),
    oi                  BIGINT,
    oi_day_high         BIGINT,
    oi_day_low          BIGINT,
    bid_prices          NUMERIC(12,2)[] NOT NULL DEFAULT '{}',
    bid_quantities      BIGINT[]        NOT NULL DEFAULT '{}',
    bid_orders          INTEGER[]       NOT NULL DEFAULT '{}',
    ask_prices          NUMERIC(12,2)[] NOT NULL DEFAULT '{}',
    ask_quantities      BIGINT[]        NOT NULL DEFAULT '{}',
    ask_orders          INTEGER[]       NOT NULL DEFAULT '{}',
    available_at        TIMESTAMPTZ     NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS idx_nifty_futures_expiry_ts
    ON nifty_futures (expiry, ts DESC);

COMMENT ON TABLE nifty_futures IS
    'Near-month NIFTY futures price, OI, volume and five-level depth per second.';

-- Causal NIFTY features from the same current-week option snapshot.
-- Historical backfill uses one-minute rows; live ingestion uses one-second rows.
-- Both paths deliberately leave all future target/label columns NULL.
CREATE TABLE IF NOT EXISTS nifty_features (
    ts TIMESTAMPTZ PRIMARY KEY, trade_date DATE NOT NULL,
    source_interval_seconds SMALLINT NOT NULL DEFAULT 1,
    underlying_ltp DOUBLE PRECISION, dte INTEGER,
    return_1min DOUBLE PRECISION, return_5min DOUBLE PRECISION,
    return_15min DOUBLE PRECISION, rolling_vol_5 DOUBLE PRECISION,
    rolling_vol_15 DOUBLE PRECISION, time_bucket_enc SMALLINT,
    atm_strike DOUBLE PRECISION, atm_ce_ltp DOUBLE PRECISION, atm_pe_ltp DOUBLE PRECISION,
    atm_ce_iv DOUBLE PRECISION, atm_pe_iv DOUBLE PRECISION,
    atm_ce_oi DOUBLE PRECISION, atm_pe_oi DOUBLE PRECISION,
    atm_ce_delta DOUBLE PRECISION, atm_pe_delta DOUBLE PRECISION,
    atm_ce_gamma DOUBLE PRECISION, atm_pe_gamma DOUBLE PRECISION,
    atm_ce_vega DOUBLE PRECISION, atm_pe_vega DOUBLE PRECISION,
    atm_ce_theta DOUBLE PRECISION, atm_pe_theta DOUBLE PRECISION,
    iv_skew DOUBLE PRECISION, straddle_price DOUBLE PRECISION, pcr DOUBLE PRECISION,
    oi_imbalance DOUBLE PRECISION, ce_oi_buildup SMALLINT, pe_oi_buildup SMALLINT,
    ce_oi_wt_strike DOUBLE PRECISION, pe_oi_wt_strike DOUBLE PRECISION,
    total_ce_oi DOUBLE PRECISION, total_pe_oi DOUBLE PRECISION,
    ce_oi_change DOUBLE PRECISION, pe_oi_change DOUBLE PRECISION,
    ce_delta_sum DOUBLE PRECISION, pe_delta_sum DOUBLE PRECISION,
    gamma_sum DOUBLE PRECISION, vega_sum DOUBLE PRECISION, theta_sum DOUBLE PRECISION,
    delta_imbalance DOUBLE PRECISION, gamma_spike SMALLINT,
    vega_change DOUBLE PRECISION, theta_decay DOUBLE PRECISION,
    dist_from_atm DOUBLE PRECISION, pe_max_oi_strike DOUBLE PRECISION,
    ce_max_oi_strike DOUBLE PRECISION, dist_from_support DOUBLE PRECISION,
    dist_from_resistance DOUBLE PRECISION,
    target_direction SMALLINT, target_move DOUBLE PRECISION,
    target_30min DOUBLE PRECISION, eod_close DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    ms_vix DOUBLE PRECISION, ms_pcr_oi DOUBLE PRECISION,
    ms_atm_straddle DOUBLE PRECISION, ms_expected_move DOUBLE PRECISION,
    ms_call_oi_wall DOUBLE PRECISION, ms_put_oi_wall DOUBLE PRECISION,
    ms_dist_call_wall DOUBLE PRECISION, ms_dist_put_wall DOUBLE PRECISION,
    prev_usdinr DOUBLE PRECISION, prev_wti DOUBLE PRECISION,
    prev_brent DOUBLE PRECISION, prev_natgas DOUBLE PRECISION,
    fii_net_fut DOUBLE PRECISION, fii_net_opt DOUBLE PRECISION,
    fii_pc_ratio DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_nifty_features_trade_date_ts
    ON nifty_features (trade_date, ts DESC);

CREATE TABLE IF NOT EXISTS nifty_expiry_features (
    ts TIMESTAMPTZ NOT NULL, expiry_date DATE NOT NULL, trade_date DATE NOT NULL,
    source_interval_seconds SMALLINT NOT NULL DEFAULT 1,
    underlying_ltp DOUBLE PRECISION, dte INTEGER, atm_strike DOUBLE PRECISION,
    total_ce_oi DOUBLE PRECISION, total_pe_oi DOUBLE PRECISION,
    oi_imbalance DOUBLE PRECISION, pcr_oi DOUBLE PRECISION,
    ce_oi_strike_1 DOUBLE PRECISION, ce_oi_strike_2 DOUBLE PRECISION,
    ce_oi_strike_3 DOUBLE PRECISION, pe_oi_strike_1 DOUBLE PRECISION,
    pe_oi_strike_2 DOUBLE PRECISION, pe_oi_strike_3 DOUBLE PRECISION,
    ce_oi_wt_strike DOUBLE PRECISION, pe_oi_wt_strike DOUBLE PRECISION,
    max_pain_strike DOUBLE PRECISION, dist_from_max_pain DOUBLE PRECISION,
    atm_ce_iv DOUBLE PRECISION, atm_pe_iv DOUBLE PRECISION,
    iv_skew DOUBLE PRECISION, atm_iv_change_30min DOUBLE PRECISION,
    straddle_price DOUBLE PRECISION, implied_move_pct DOUBLE PRECISION,
    gex_ce DOUBLE PRECISION, gex_pe DOUBLE PRECISION, gex_net DOUBLE PRECISION,
    delta_imbalance DOUBLE PRECISION,
    ms_vix DOUBLE PRECISION, ms_pcr_oi DOUBLE PRECISION,
    ms_atm_straddle DOUBLE PRECISION, ms_expected_move DOUBLE PRECISION,
    ms_call_oi_wall DOUBLE PRECISION, ms_put_oi_wall DOUBLE PRECISION,
    ms_dist_call_wall DOUBLE PRECISION, ms_dist_put_wall DOUBLE PRECISION,
    prev_usdinr DOUBLE PRECISION, prev_wti DOUBLE PRECISION,
    prev_brent DOUBLE PRECISION, prev_natgas DOUBLE PRECISION,
    fii_net_fut DOUBLE PRECISION, fii_net_opt DOUBLE PRECISION,
    fii_pc_ratio DOUBLE PRECISION,
    target_expiry_price DOUBLE PRECISION, target_range_low DOUBLE PRECISION,
    target_range_high DOUBLE PRECISION, target_pin SMALLINT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ts, expiry_date)
);

CREATE INDEX IF NOT EXISTS idx_nifty_expiry_features_expiry_ts
    ON nifty_expiry_features (expiry_date, ts DESC);

COMMENT ON TABLE nifty_features IS
    'Causal NIFTY features: per-minute historical backfill and per-second live current-week ATM +/-10 snapshots.';
COMMENT ON TABLE nifty_expiry_features IS
    'Current-expiry NIFTY OI, IV, max-pain and normalized GEX features with explicit source interval.';

ALTER TABLE nifty_features
    ADD COLUMN IF NOT EXISTS source_interval_seconds SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE nifty_expiry_features
    ADD COLUMN IF NOT EXISTS source_interval_seconds SMALLINT NOT NULL DEFAULT 1;


-- ── MCX futures OHLC candles ──────────────────────────────────────────────────
-- Stores daily + 15-min + 1-min OHLC for NATURALGAS and CRUDEOIL futures.
-- Source: Kite Connect historical API (near-month futures contract).
-- Used for: ATR, S/R levels, trend, intraday setup for option selling.
CREATE TABLE IF NOT EXISTS mcx_ohlc (
    id          BIGSERIAL       PRIMARY KEY,
    ts          TIMESTAMPTZ     NOT NULL,           -- candle open time (IST-aware)
    instrument  VARCHAR(20)     NOT NULL,           -- NATURALGAS | CRUDEOIL
    interval    VARCHAR(10)     NOT NULL,           -- 'day' | '15minute' | 'minute'
    tradingsymbol VARCHAR(40),                       -- exact futures contract used
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
    'MCX futures OHLC (daily + 15-min + 1-min). CRUDEOIL collected per-minute; NATURALGAS at 15-min.';

ALTER TABLE IF EXISTS mcx_ohlc
    ADD COLUMN IF NOT EXISTS tradingsymbol VARCHAR(40);

-- Full five-level CRUDEOIL futures depth captured independently of OHLC writes.
CREATE TABLE IF NOT EXISTS mcx_futures_depth (
    ts                  TIMESTAMPTZ NOT NULL,
    instrument          VARCHAR(20) NOT NULL,
    tradingsymbol       VARCHAR(40) NOT NULL,
    instrument_token    BIGINT NOT NULL,
    expiry              DATE,
    exchange_ts         TIMESTAMPTZ,
    received_at         TIMESTAMPTZ NOT NULL,
    last_price          NUMERIC(12,2),
    bid_prices          NUMERIC(12,2)[] NOT NULL DEFAULT '{}',
    bid_quantities      BIGINT[] NOT NULL DEFAULT '{}',
    bid_orders          INTEGER[] NOT NULL DEFAULT '{}',
    ask_prices          NUMERIC(12,2)[] NOT NULL DEFAULT '{}',
    ask_quantities      BIGINT[] NOT NULL DEFAULT '{}',
    ask_orders          INTEGER[] NOT NULL DEFAULT '{}',
    available_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (ts, instrument),
    CHECK (
        cardinality(bid_prices) = cardinality(bid_quantities)
        AND cardinality(bid_prices) = cardinality(bid_orders)
        AND cardinality(ask_prices) = cardinality(ask_quantities)
        AND cardinality(ask_prices) = cardinality(ask_orders)
    )
);

CREATE INDEX IF NOT EXISTS idx_mcx_futures_depth_contract
    ON mcx_futures_depth (tradingsymbol, ts DESC);

COMMENT ON TABLE mcx_futures_depth IS
    'Per-second CRUDEOIL futures bid/ask depth; asynchronous writes cannot block OHLC ingestion.';


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
