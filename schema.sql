-- ============================================================
--  Stock Analyzer — PostgreSQL Schema
--  Run this ONCE in pgAdmin Query Tool against the
--  'stock_analyzer' database.
--
--  Steps:
--    1. Open pgAdmin → right-click Databases → Create → Database
--       Name: stock_analyzer
--    2. Select stock_analyzer → Tools → Query Tool
--    3. Paste this file and click Execute (F5)
-- ============================================================


-- ── Predictions ───────────────────────────────────────────────────────────────
-- One row per prediction per screener run.
-- Multiple runs on same day = multiple rows (full history).

CREATE TABLE IF NOT EXISTS market_predictions (
    id              BIGSERIAL       PRIMARY KEY,
    run_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    run_date        DATE            NOT NULL DEFAULT CURRENT_DATE,
    instrument      VARCHAR(10)     NOT NULL,   -- NIFTY | CRUDE | NATGAS
    expiry_date     DATE            NOT NULL,
    week_num        SMALLINT,                   -- 1-5 for weekly, NULL for monthly
    prediction_type VARCHAR(10)     NOT NULL,   -- WEEKLY | MONTHLY
    direction       VARCHAR(4)      NOT NULL,   -- UP | DOWN
    probability     NUMERIC(5,2)    NOT NULL,   -- e.g. 67.50
    cmp             NUMERIC(12,2)   NOT NULL,   -- price at time of prediction
    target_price    NUMERIC(12,2),              -- predicted expiry price
    range_hi        NUMERIC(12,2),              -- upper bound of expected range
    range_lo        NUMERIC(12,2),              -- lower bound of expected range
    -- ── Signal inputs (what drove this prediction) ──────────────────────────
    hist_wr         NUMERIC(5,1),   -- historical win rate % for this week slot
    tech_signal     NUMERIC(6,3),   -- enhanced tech score -1..+1
    vix_signal      NUMERIC(6,3),   -- India VIX signal -1..+1
    pcr_value       NUMERIC(6,3),   -- actual Put-Call Ratio (e.g. 1.24)
    max_pain        NUMERIC(12,2),  -- max pain strike level
    atm_iv          NUMERIC(6,2),   -- ATM implied volatility %
    global_signal   NUMERIC(6,3),   -- S&P500 + Nikkei signal -1..+1
    fii_signal      NUMERIC(6,3),   -- FII net flow signal -1..+1
    seasonal_signal NUMERIC(6,3),   -- seasonal demand signal -1..+1
    put_oi_wall     NUMERIC(12,2),  -- highest PE OI strike (key support)
    call_oi_wall    NUMERIC(12,2)   -- highest CE OI strike (key resistance)
);

-- Fast lookups by instrument + expiry
CREATE INDEX IF NOT EXISTS idx_pred_lookup
    ON market_predictions (instrument, expiry_date, run_date DESC);

COMMENT ON TABLE market_predictions IS
    'Every prediction made by the market overview model. '
    'Used to backtest accuracy and optimise signal weights.';


-- ── Outcomes ──────────────────────────────────────────────────────────────────
-- Actual result after each expiry. Written once, never updated.

CREATE TABLE IF NOT EXISTS market_outcomes (
    id               BIGSERIAL      PRIMARY KEY,
    instrument       VARCHAR(10)    NOT NULL,
    expiry_date      DATE           NOT NULL,
    week_num         SMALLINT,
    prediction_type  VARCHAR(10)    NOT NULL,
    actual_direction VARCHAR(4),    -- UP | DOWN
    actual_close     NUMERIC(12,2), -- closing price on expiry day
    actual_pct       NUMERIC(6,2),  -- % change from week/month open
    week_open        NUMERIC(12,2), -- opening price at start of period
    recorded_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

-- One outcome row per (instrument, expiry, week, type)
CREATE UNIQUE INDEX IF NOT EXISTS idx_outcomes_unique
    ON market_outcomes (instrument, expiry_date, COALESCE(week_num, -1), prediction_type);

COMMENT ON TABLE market_outcomes IS
    'Actual market results after each expiry. '
    'Joined with market_predictions to compute model accuracy.';


-- ── View: Model Accuracy ───────────────────────────────────────────────────────
-- Shows how accurate each instrument / week slot / prediction type has been.
-- Uses only the LATEST prediction before each expiry (not stale mid-week runs).

CREATE OR REPLACE VIEW v_model_accuracy AS
SELECT
    p.instrument,
    p.prediction_type,
    p.week_num,
    COUNT(*)                                                                      AS total_predictions,
    SUM(CASE WHEN p.direction = o.actual_direction THEN 1 ELSE 0 END)            AS correct,
    ROUND(
        100.0 * SUM(CASE WHEN p.direction = o.actual_direction THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0),
    1)                                                                            AS accuracy_pct,
    ROUND(AVG(p.probability), 1)                                                 AS avg_predicted_prob,
    ROUND(AVG(
        CASE WHEN p.direction = o.actual_direction THEN p.probability END), 1)   AS avg_prob_when_correct,
    MIN(p.run_date)                                                               AS first_run,
    MAX(p.run_date)                                                               AS latest_run
FROM market_predictions p
JOIN market_outcomes o
    ON  p.instrument                    = o.instrument
    AND p.expiry_date                   = o.expiry_date
    AND COALESCE(p.week_num, -1)        = COALESCE(o.week_num, -1)
    AND p.prediction_type               = o.prediction_type
-- Latest prediction before expiry only
WHERE p.run_date = (
    SELECT MAX(p2.run_date)
    FROM   market_predictions p2
    WHERE  p2.instrument                = p.instrument
    AND    p2.expiry_date               = p.expiry_date
    AND    COALESCE(p2.week_num, -1)    = COALESCE(p.week_num, -1)
    AND    p2.prediction_type           = p.prediction_type
    AND    p2.run_date                 <= p.expiry_date
)
GROUP BY p.instrument, p.prediction_type, p.week_num
ORDER BY p.instrument, p.prediction_type, p.week_num;

COMMENT ON VIEW v_model_accuracy IS
    'Model directional accuracy per instrument / week slot. '
    'Query this after 4+ weeks of data to tune signal weights.';


-- ── View: Signal Importance ────────────────────────────────────────────────────
-- Pearson correlation of each signal with correct outcome.
-- +1 = perfect predictor, 0 = noise, -1 = inverse predictor.

CREATE OR REPLACE VIEW v_signal_importance AS
WITH resolved AS (
    SELECT
        p.instrument,
        p.prediction_type,
        CASE WHEN p.direction = o.actual_direction THEN 1.0 ELSE 0.0 END AS correct,
        -- Normalise signals so direction is already encoded:
        -- positive = bullish signal, negative = bearish signal
        -- correct = 1 when direction matches → high positive signal should correlate with correct
        CASE WHEN p.direction = 'UP' THEN  p.tech_signal    ELSE -p.tech_signal    END AS tech_aligned,
        CASE WHEN p.direction = 'UP' THEN  p.vix_signal     ELSE -p.vix_signal     END AS vix_aligned,
        CASE WHEN p.direction = 'UP' THEN  p.global_signal  ELSE -p.global_signal  END AS global_aligned,
        CASE WHEN p.direction = 'UP' THEN  p.fii_signal     ELSE -p.fii_signal     END AS fii_aligned,
        CASE WHEN p.direction = 'UP' THEN  p.seasonal_signal ELSE -p.seasonal_signal END AS seasonal_aligned,
        -- PCR: contrarian — high PCR = bullish signal (already stored as raw ratio)
        CASE WHEN p.direction = 'UP' THEN  p.pcr_value - 1  ELSE  1 - p.pcr_value  END AS pcr_aligned,
        -- hist_wr: 50% = neutral → normalise to -1..+1
        CASE WHEN p.direction = 'UP' THEN (p.hist_wr - 50)/50 ELSE -(p.hist_wr - 50)/50 END AS histwr_aligned
    FROM market_predictions p
    JOIN market_outcomes o
        ON  p.instrument                 = o.instrument
        AND p.expiry_date                = o.expiry_date
        AND COALESCE(p.week_num, -1)     = COALESCE(o.week_num, -1)
        AND p.prediction_type            = o.prediction_type
    WHERE p.run_date = (
        SELECT MAX(p2.run_date) FROM market_predictions p2
        WHERE p2.instrument  = p.instrument  AND p2.expiry_date = p.expiry_date
        AND COALESCE(p2.week_num,-1) = COALESCE(p.week_num,-1)
        AND p2.prediction_type = p.prediction_type
        AND p2.run_date <= p.expiry_date
    )
)
SELECT
    instrument,
    prediction_type,
    COUNT(*)                                             AS samples,
    ROUND(CORR(tech_aligned,     correct)::NUMERIC, 3)  AS tech_corr,
    ROUND(CORR(vix_aligned,      correct)::NUMERIC, 3)  AS vix_corr,
    ROUND(CORR(global_aligned,   correct)::NUMERIC, 3)  AS global_corr,
    ROUND(CORR(fii_aligned,      correct)::NUMERIC, 3)  AS fii_corr,
    ROUND(CORR(seasonal_aligned, correct)::NUMERIC, 3)  AS seasonal_corr,
    ROUND(CORR(pcr_aligned,      correct)::NUMERIC, 3)  AS pcr_corr,
    ROUND(CORR(histwr_aligned,   correct)::NUMERIC, 3)  AS hist_wr_corr
FROM resolved
GROUP BY instrument, prediction_type
HAVING COUNT(*) >= 5
ORDER BY instrument, prediction_type;

COMMENT ON VIEW v_signal_importance IS
    'Pearson correlation of each signal with correct directional outcome. '
    'Use this after 20+ samples to re-weight signals empirically.';


-- ── View: Probability Calibration ─────────────────────────────────────────────
-- Does "70% probability" actually win 70% of the time?
-- Good calibration = accuracy_pct ≈ probability bucket.

CREATE OR REPLACE VIEW v_prob_calibration AS
SELECT
    instrument,
    prediction_type,
    FLOOR(probability / 5) * 5          AS prob_bucket,   -- 50, 55, 60, 65, 70, 75
    COUNT(*)                             AS predictions,
    SUM(CASE WHEN p.direction = o.actual_direction THEN 1 ELSE 0 END) AS correct,
    ROUND(
        100.0 * SUM(CASE WHEN p.direction = o.actual_direction THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0), 1)  AS actual_accuracy_pct
FROM market_predictions p
JOIN market_outcomes o
    ON  p.instrument              = o.instrument
    AND p.expiry_date             = o.expiry_date
    AND COALESCE(p.week_num, -1)  = COALESCE(o.week_num, -1)
    AND p.prediction_type         = o.prediction_type
WHERE p.run_date = (
    SELECT MAX(p2.run_date) FROM market_predictions p2
    WHERE p2.instrument = p.instrument AND p2.expiry_date = p.expiry_date
    AND COALESCE(p2.week_num,-1) = COALESCE(p.week_num,-1)
    AND p2.prediction_type = p.prediction_type
    AND p2.run_date <= p.expiry_date
)
GROUP BY instrument, prediction_type, prob_bucket
HAVING COUNT(*) >= 3
ORDER BY instrument, prediction_type, prob_bucket;

COMMENT ON VIEW v_prob_calibration IS
    'Checks if stated probability matches actual win rate. '
    'If model says 70% but wins only 55%, it is over-confident.';


-- ── Sanity check ──────────────────────────────────────────────────────────────
SELECT 'Schema created successfully' AS status,
       (SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN ('market_predictions','market_outcomes')) AS tables_created;
