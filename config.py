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

# ============================================================
#  NSE TRADING HOLIDAYS
#  Update this list each year with official NSE holiday calendar.
#  Format: "YYYY-MM-DD"
# ============================================================

NSE_HOLIDAYS = {
    # 2025
    "2025-01-26",  # Republic Day
    "2025-03-14",  # Holi
    "2025-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2025-04-18",  # Good Friday
    "2025-05-01",  # Maharashtra Day
    "2025-08-15",  # Independence Day
    "2025-10-02",  # Gandhi Jayanti
    "2025-10-24",  # Dussehra
    "2025-11-05",  # Diwali Balipratipada
    "2025-11-15",  # Gurunanak Jayanti
    "2025-12-25",  # Christmas
    # 2026
    "2026-01-26",  # Republic Day
    "2026-03-03",  # Holi
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-10-02",  # Gandhi Jayanti
    "2026-10-22",  # Dussehra (tentative)
    "2026-11-14",  # Diwali Laxmi Puja (tentative)
    "2026-11-15",  # Diwali Balipratipada (tentative)
    "2026-12-25",  # Christmas
}

# ============================================================
#  MCX TRADING HOLIDAYS — days MCX is FULLY CLOSED
#  MCX has fewer holidays than NSE.
#  Source: MCX official holiday calendar.
# ============================================================

MCX_HOLIDAYS = {
    # 2025
    "2025-01-26",  # Republic Day
    "2025-08-15",  # Independence Day
    "2025-10-02",  # Gandhi Jayanti
    "2025-11-05",  # Diwali Laxmi Puja (MCX fully closed for Muhurat trading)
    # 2026
    "2026-01-26",  # Republic Day
    "2026-08-15",  # Independence Day
    "2026-10-02",  # Gandhi Jayanti
    "2026-11-14",  # Diwali Laxmi Puja (tentative)
}

# ============================================================
#  MCX EVENING-ONLY DAYS — MCX opens at 5 PM IST (not 9 AM)
#  On these days MCX runs a restricted evening session only.
#  Normal hours resume next trading day.
# ============================================================

MCX_EVENING_ONLY_DAYS = {
    # 2025
    "2025-03-14",  # Holi
    "2025-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2025-04-18",  # Good Friday
    "2025-05-01",  # Maharashtra Day
    "2025-10-24",  # Dussehra
    "2025-11-15",  # Diwali Balipratipada / Gurunanak Jayanti
    "2025-12-25",  # Christmas
    # 2026
    "2026-03-03",  # Holi
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-10-22",  # Dussehra (tentative)
    "2026-11-15",  # Diwali Balipratipada (tentative)
    "2026-12-25",  # Christmas
}

# ============================================================
#  GOOGLE OAUTH — Allowed email addresses
#  Only these Google accounts can log in to the web app.
#  You can also set ALLOWED_EMAILS env var (comma-separated)
#  which takes precedence over this list.
# ============================================================

ALLOWED_EMAILS = [
    "gagan.chhabra@gmail.com",
    "gagan.chhabra1990@gmail.com",
    "gc1133@gmail.com",
]
