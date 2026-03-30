"""
Named constants — single source of truth for all magic numbers.

Import from here instead of scattering literals across modules.
"""

# ── Currency formatting ───────────────────────────────────────────────────────

CRORE = 1_00_00_000   # 1e7
LAKH  = 1_00_000      # 1e5

# ── Stock scoring thresholds ──────────────────────────────────────────────────

SCORE_GREAT = 72   # → ADD MORE
SCORE_GOOD  = 58   # → HOLD (strong)
SCORE_WARN  = 42   # → HOLD (cautious)
SCORE_BAD   = 28   # → EXIT

# Default score when fundamentals data is unavailable
SCORE_DEFAULT_NEUTRAL = 50.0

# ── Screener: data quality filters ───────────────────────────────────────────

MIN_DAILY_VALUE_CR = 5      # ₹5 Crore minimum average daily turnover
MIN_DATA_DAYS      = 120    # Need at least 4 months of price history
BATCH_SIZE         = 30     # Download N tickers per yfinance batch request

# ── Screener: direction scoring ───────────────────────────────────────────────

# Normalisation factor: raw_score ∈ [-110, +110] → 0-100 via: 50 + raw * SCALE
DIRECTION_SCORE_SCALE = 0.45

# ── Screener: composite probability ──────────────────────────────────────────

PROB_RANGE_MIN = 72.0   # Only high-conviction setups qualify
PROB_RANGE_MAX = 92.0

# ── Market overview: probability output range ─────────────────────────────────

MARKET_PROB_SWING  = 28.0
MARKET_PROB_MIN    = 50.0
MARKET_PROB_MAX    = 78.0
