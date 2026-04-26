-- =============================================================================
--  Schema v2 — Extended data collection tables
--  Run ONCE: psql -U stockanalyzer -d stock_analyzer -h localhost -f options/schema_v2.sql
--
--  New tables (additive — does NOT modify existing tables):
--    nse_ohlc      : NSE indices + sector indices + USDINR per-minute OHLC
--    us_market     : US futures + DXY + US VIX + US 10Y yield (intraday + daily)
--    derived_daily : EOD computed metrics — Max Pain, GEX, IV Skew
-- =============================================================================


-- ── NSE indices + sector indices + USDINR OHLC ────────────────────────────────
-- Symbols: NIFTY50 | BANKNIFTY | FINNIFTY | INDIAVIX | MIDCPNIFTY
--          NIFTYIT | NIFTYAUTO | NIFTYPHARMA | NIFTYMETAL | NIFTYFMCG
--          NIFTYENERGY | NIFTYPSUBANK | NIFTYPRIVBANK | NIFTYMEDIA | NIFTYREALTY
--          USDINR
-- Source: Kite Connect historical API
-- Intervals: 'minute' (collected every minute during market hours) | 'day' (daily)
CREATE TABLE IF NOT EXISTS nse_ohlc (
    id       BIGSERIAL    PRIMARY KEY,
    ts       TIMESTAMPTZ  NOT NULL,
    symbol   VARCHAR(30)  NOT NULL,
    interval VARCHAR(10)  NOT NULL,   -- 'minute' | 'day'
    open     NUMERIC(12,4),
    high     NUMERIC(12,4),
    low      NUMERIC(12,4),
    close    NUMERIC(12,4),
    volume   BIGINT,
    UNIQUE (ts, symbol, interval)
);

CREATE INDEX IF NOT EXISTS idx_nse_ohlc_lookup
    ON nse_ohlc (symbol, interval, ts DESC);

COMMENT ON TABLE nse_ohlc IS
    'NSE broad + sector indices + USDINR OHLC. Per-minute during market hours, daily backfill.';


-- ── US markets: S&P500 / Nasdaq / Dow futures + DXY + US VIX + US 10Y ─────────
-- Symbols: SP500F | NASDAQF | DOWF | DXY | USVIX | US10Y | GOLDF
-- Source: yfinance (free, no auth)
-- Intervals: '5minute' (collected every 5 min during US hours) | 'day'
CREATE TABLE IF NOT EXISTS us_market (
    id       BIGSERIAL    PRIMARY KEY,
    ts       TIMESTAMPTZ  NOT NULL,
    symbol   VARCHAR(20)  NOT NULL,
    interval VARCHAR(10)  NOT NULL,   -- '5minute' | 'day'
    open     NUMERIC(12,4),
    high     NUMERIC(12,4),
    low      NUMERIC(12,4),
    close    NUMERIC(12,4),
    volume   BIGINT,
    UNIQUE (ts, symbol, interval)
);

CREATE INDEX IF NOT EXISTS idx_us_market_lookup
    ON us_market (symbol, interval, ts DESC);

COMMENT ON TABLE us_market IS
    'US futures (ES, NQ, YM), DXY, CBOE VIX, US 10Y yield via yfinance. 5-min intraday + daily.';


-- ── EOD derived metrics ────────────────────────────────────────────────────────
-- Computed from option_chain data after market close.
-- Max Pain  : strike that maximises loss for option buyers
-- GEX       : net gamma exposure in Rs. across all strikes
-- IV Skew   : 25-delta PE implied vol minus 25-delta CE implied vol
CREATE TABLE IF NOT EXISTS derived_daily (
    id             BIGSERIAL    PRIMARY KEY,
    date           DATE         NOT NULL,
    instrument     VARCHAR(20)  NOT NULL,   -- NIFTY | BANKNIFTY
    expiry         DATE,                    -- expiry used for calculations
    max_pain       INTEGER,                 -- max pain strike
    gex_total      NUMERIC(18,2),           -- total net GEX (Rs. crore)
    gex_flip       INTEGER,                 -- strike where net GEX flips sign
    iv_skew_25d    NUMERIC(8,4),            -- 25d PE IV minus 25d CE IV (positive = fearful)
    atm_iv         NUMERIC(6,2),            -- ATM straddle IV at close
    pcr_oi         NUMERIC(6,3),            -- final PCR OI
    call_oi_wall   INTEGER,                 -- strike with highest CE OI
    put_oi_wall    INTEGER,                 -- strike with highest PE OI
    computed_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (date, instrument)
);

CREATE INDEX IF NOT EXISTS idx_derived_daily_lookup
    ON derived_daily (instrument, date DESC);

COMMENT ON TABLE derived_daily IS
    'EOD derived metrics from option_chain: Max Pain, GEX, IV Skew, OI walls. Computed at 15:40 IST.';


-- ── Sanity check ──────────────────────────────────────────────────────────────
SELECT
    'Schema v2 ready' AS status,
    (SELECT COUNT(*) FROM information_schema.tables
     WHERE table_schema = 'public'
       AND table_name IN ('nse_ohlc', 'us_market', 'derived_daily')) AS new_tables_created;
