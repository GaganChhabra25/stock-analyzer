# ============================================================
#  USER GOALS — Edit this to match your investment goals
# ============================================================

GOALS = {
    "target_corpus": 6_00_00_000,   # ₹6 Crores
    "time_horizon_years": 15,
    "expected_cagr": 0.15,           # 15% per annum
    "risk_tolerance": "moderate",    # low | moderate | high
    "investment_style": "long_term",
}

# ============================================================
#  SCORING THRESHOLDS  (tune as needed)
# ============================================================

STOCK_THRESHOLDS = {
    "min_roe": 12,           # Return on Equity %
    "max_pe": 60,            # Trailing P/E ceiling
    "max_debt_equity": 1.5,  # Debt/Equity ratio
    "min_current_ratio": 1.0,
    "min_revenue_growth": 5, # YoY %
}

MF_THRESHOLDS = {
    "min_1yr_return": 10,    # %
    "min_3yr_return": 12,    # %
    "min_5yr_return": 12,    # %
    "max_expense_ratio": 1.5,
    "min_aum_crores": 500,
}

# Scoring weights for overall stock score (must sum to 1.0)
SCORE_WEIGHTS = {
    "fundamentals": 0.50,
    "valuation":    0.30,
    "momentum":     0.20,
}

# Preferred MF categories for a moderate long-term investor
PREFERRED_MF_CATEGORIES = [
    "Large Cap Fund",
    "Flexi Cap Fund",
    "Multi Cap Fund",
    "Large & Mid Cap Fund",
    "Index Fund",
    "Balanced Advantage Fund",
    "Aggressive Hybrid Fund",
]

# ============================================================
#  FILE PATHS
# ============================================================

ZERODHA_HOLDINGS_FILE = "data/zerodha_holdings.csv"
MF_HOLDINGS_FILE      = "data/mf_holdings.csv"
REPORT_OUTPUT_FILE    = "reports/analysis_report.txt"
HTML_REPORT_FILE      = "reports/portfolio_report.html"
