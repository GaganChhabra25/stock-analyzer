# 📊 Stock Analyzer — Personal Investment Analysis System

> A local Python toolkit with **two tools**: a **Portfolio Analyzer** that scores your Zerodha holdings against your financial goals, and a **Market Screener** that scans 140+ NSE stocks daily to find the highest-probability directional trades for the current and next month.

---

## Two Tools, One System

| Tool | Command | Output |
|------|---------|--------|
| **Portfolio Analyzer** | `python main.py` | `reports/portfolio_report.html` |
| **Market Screener** | `python run_screener.py` | `reports/screener_report.html` |

The portfolio report has a direct link to the screener report in its header.

---

## Tool 1 — Portfolio Analyzer

### What It Does

Given your Zerodha holdings CSV and MF holdings, it:

1. **Fetches live fundamentals** (PE, ROE, Debt/Equity, Revenue Growth) for every direct equity stock via Yahoo Finance
2. **Scores each stock out of 100** across Fundamentals (50%), Valuation (30%), Momentum (20%)
3. **Classifies ETFs** by category and gives category-appropriate advice
4. **Analyzes Mutual Funds** — pulls NAV history from mfapi.in, calculates 1Y / 3Y / 5Y CAGR returns
5. **Breaks down your portfolio** into Stocks and MF sections with separate Invested / Current Value / P&L cards
6. **SIP Goal Simulator** — live in-browser calculator: type any SIP per fund, goal amount, years, and CAGR to instantly see projected corpus vs target
7. **Projects your corpus** at 12%, 15%, and 18% CAGR scenarios
8. **Generates a single self-contained HTML file** — printable, no internet needed to view

### Score → Action (3 Actions Only)

| Score | Action | What to do |
|-------|--------|------------|
| ≥ 72 | 🟢 **ADD MORE** | Strong across all dimensions. Good candidate to increase allocation |
| 42–71 | 🔵 **HOLD** | Solid position. Stay invested, no action needed |
| < 42 | 🔴 **EXIT** | Weak fundamentals or overvalued. Exit and redeploy capital |

ETFs are not scored numerically — advice is category-based (broad market ETFs preferred, sector ETFs need review).

### How Scoring Works

```
Total Score = Fundamentals (50%) + Valuation (30%) + Momentum (20%)
```

**Fundamentals (50%)** — ROE, Debt/Equity, Revenue Growth, Current Ratio

**Valuation (30%)** — Trailing P/E, Price/Book, EV/EBITDA

**Momentum (20%)** — 1Y price return, 50-day vs 200-day MA crossover, annualised volatility

### MF Analysis

For each MF, the tool fetches NAV history from [mfapi.in](https://api.mfapi.in) (free, no auth) and calculates:
- Annualised 1Y, 3Y, 5Y CAGR returns
- Category fit score (prefers Flexi Cap, Large & Mid Cap, Multi Cap, Index Funds)
- Expense ratio flag
- Action: **ADD MORE** / **HOLD** / **EXIT**

### SIP Goal Simulator (Live in HTML Report)

The MF section of the report has an interactive simulator — no re-running Python needed:
- Edit the **SIP amount** for any fund directly in the table
- Change the **Target Goal** (default ₹6 Cr), **Time Horizon** (default 15Y), **Expected CAGR** (default 15%)
- Results update instantly: per-fund projected corpus, total corpus, gap/surplus, progress bar

### Portfolio Breakdown Cards

The report header shows three rows of cards:

| Row | Cards |
|-----|-------|
| 📈 Stocks & ETFs | Invested · Current Value · P&L · Actions count |
| 🏦 Mutual Funds | Invested · Current Value · P&L · Monthly SIP |
| 🎯 Goal Summary | Total Portfolio · Corpus Projected · SIP Needed/Month |

### Corpus Projection Formula

```
Total Corpus = FV of current portfolio + FV of future SIPs

FV of portfolio = Current Value × (1 + CAGR)^years
FV of SIPs      = Monthly SIP × [(1+r)^n − 1] / r × (1+r)
```

Three scenarios always shown: **Conservative 12%**, **Base 15%**, **Optimistic 18%**

---

## Tool 2 — NSE Market Screener

### What It Does

Scans 140+ liquid NSE stocks (Nifty 50 + Nifty Next 50 + liquid mid/small caps) and finds the **15 highest-probability directional trade setups** for the current and next month.

**Liquidity filter:** Average daily turnover > ₹5 Crore, minimum 4 months of price history.

### How to Run

```bash
# Full scan — top 15 picks (recommended, ~5 min)
python run_screener.py

# Change number of picks
python run_screener.py --top 10

# Fast mode — ~60 stocks, results in ~2 min
python run_screener.py --fast

# Don't auto-open browser
python run_screener.py --no-open
```

### What the Screener Analyses

**5 Technical Dimensions (scored -110 to +110):**

| Signal | What it measures | Max score |
|--------|-----------------|-----------|
| RSI (14) | Oversold (<30) = bullish, Overbought (>70) = bearish | ±30 |
| MACD crossover | Bullish/bearish crossover above/below zero line | ±25 |
| Price vs SMA-20/50/200 | Position relative to key moving averages | ±25 |
| Bollinger Band position | Near lower band = bullish, upper band = bearish | ±20 |
| Volume surge | 5-day vs 20-day average volume ratio | ±10 |

**Probability formula:**
```
Probability = Technical score (45%) + Historical monthly win rate (45%) + Volume confirmation (10%)
Range: 72% – 92% (high-conviction setups only)
```

### Current Month Prediction (e.g. March 2026)

For each top pick, the screener shows:
- **MTD return** — how much the stock has already moved from month-open
- **Trading days remaining** in the month
- **EOM probability** — adjusted based on MTD progress (already up 4% with 5 days left → 80%+ probability of closing positive)
- **LOCKED badge** — when the MTD move is large enough that reversal within remaining days is statistically unlikely
- **EOM price targets** — upside and downside targets based on remaining 1-sigma move
- **Seasonal history** — how this stock performed in this calendar month for the last 5 years

### Next Month Prediction (e.g. April 2026)

For each pick:
- **Direction** — UP or DOWN
- **Probability** — combining seasonal win rate + current momentum
- **Aligned / Conflict badge** — whether seasonal pattern and current momentum agree
- **April seasonal history** — year-by-year returns for the last 5 years
- **Expected price range** — ₹low – ₹high based on 1-month historical volatility
- **Base target price** — directional price target for end of next month

### Monthly Backtest

For each candidate, the screener backtests the predicted direction against the **last 12 months of actual monthly returns** and shows:
- Month-by-month: actual return, actual direction, predicted direction, win/loss
- Overall win rate as a percentage

### Weekly Data

Last 5 complete weeks (Monday open → Friday close) for each pick:
- Mon open price, Fri close price, week high, week low
- Weekly % move
- Historical weekly win rate (% of weeks that closed positive over last 12 months)

---

## Project Structure

```
stock-analyzer/
│
├── main.py                      # Portfolio Analyzer entry point
├── run_screener.py              # Market Screener entry point
├── config.py                    # YOUR GOALS + thresholds (edit this)
├── requirements.txt             # Python dependencies
├── find_scheme.py               # Helper: search MF scheme codes
│
├── data/
│   ├── zerodha_holdings.csv     # Your Zerodha export (drop here)
│   └── mf_holdings.csv          # Your MF holdings (fill manually)
│
├── analyzers/
│   ├── stock_analyzer.py        # Scores direct equity stocks via yfinance
│   ├── mf_analyzer.py           # Analyzes MFs via mfapi.in
│   └── portfolio_analyzer.py    # Aggregates results, runs projections
│
├── screener/
│   ├── universe.py              # 148 NSE stocks with name/sector metadata
│   ├── analyzer.py              # Screening engine: technicals + month predictions
│   └── report.py                # Dark-theme HTML screener report
│
├── utils/
│   ├── data_loader.py           # Reads & validates CSV files
│   ├── corpus_calculator.py     # Future value & SIP math
│   ├── report_generator.py      # Colored terminal output + .txt report
│   └── html_report.py           # Portfolio HTML report with SIP simulator
│
└── reports/
    ├── analysis_report.txt      # Plain text report (auto-generated)
    ├── portfolio_report.html    # Portfolio analysis report
    └── screener_report.html     # Market screener report
```

---

## Setup

### Step 1 — Install dependencies (one time)
```bash
pip install -r requirements.txt
```

### Step 2 — Add your Zerodha holdings
Export from Zerodha Kite → Portfolio → Holdings → download icon → `holdings.csv`

Drop at `data/zerodha_holdings.csv`. Auto-detects Zerodha's native CSV format.

### Step 3 — Add your Mutual Fund holdings

Find scheme codes:
```bash
python find_scheme.py "mirae asset large"
python find_scheme.py "parag parikh"
```

Fill `data/mf_holdings.csv`:
```csv
Fund_Name,Scheme_Code,Monthly_SIP,Total_Units,Avg_Purchase_NAV,Expense_Ratio,Notes
Mirae Asset Large Cap - Direct Growth,118989,5000,250.123,42.50,0.54,Core large cap
Parag Parikh Flexi Cap - Direct Growth,122639,3000,100.456,38.90,0.63,Flexi cap
```

| Column | What to enter |
|--------|--------------|
| `Scheme_Code` | 6-digit AMFI code from `find_scheme.py` |
| `Monthly_SIP` | Active SIP amount (₹). Put 0 if no SIP |
| `Total_Units` | Current units held |
| `Avg_Purchase_NAV` | Average NAV at which you bought units |
| `Expense_Ratio` | e.g. 0.54 for 0.54%. Put 0 if unknown |

### Step 4 — Run

```bash
# Portfolio analysis (stocks + MFs)
python main.py

# Only stocks/ETFs
python main.py --stocks-only

# Only mutual funds
python main.py --mf-only

# Market screener — top 15 picks
python run_screener.py

# Market screener — fast mode
python run_screener.py --fast --top 10
```

### Step 5 — Open reports
```
reports/portfolio_report.html   ← Portfolio analysis
reports/screener_report.html    ← Market screener
```

To save as PDF: Chrome → `Ctrl+P` → Save as PDF

---

## Customising Goals

Edit `config.py`:

```python
GOALS = {
    "target_corpus":       6_00_00_000,  # ₹6 Crores
    "time_horizon_years":  15,
    "expected_cagr":       0.15,         # 15%
    "risk_tolerance":      "moderate",
    "investment_style":    "long_term",
}
```

The SIP simulator in the HTML report also lets you override these values live in the browser without re-running.

---

## Data Sources

| Data | Source | Cost |
|------|--------|------|
| Stock prices & fundamentals | Yahoo Finance via `yfinance` | Free |
| MF NAV history | [mfapi.in](https://api.mfapi.in) | Free, no auth |
| NSE screener price data | Yahoo Finance via `yfinance` | Free |
| Holdings data | Your Zerodha export | Your own data |

---

## Refresh Schedule

| Frequency | What to do |
|-----------|-----------|
| Daily / Weekly | Run `python run_screener.py` for fresh trade setups |
| Monthly | Drop latest Zerodha CSV, run `python main.py` |
| Quarterly | After quarterly results — rerun to see if scores changed |
| Annually | Update `config.py` if your goals or horizon changed |

---

## Limitations

- **yfinance data quality**: Occasionally returns incomplete fundamentals for PSUs and banking stocks. Tool defaults to neutral scores when data is missing.
- **Screener probabilities**: Statistical estimates based on historical patterns. 72–92% is the range for selected setups — not a guarantee of future returns.
- **MF returns**: Calculated from NAV history only. Does not account for exit loads or tax.
- **Not financial advice**: For personal tracking and decision-support only. Always consult a SEBI-registered advisor before acting.

---

*Built for a 15-year wealth creation journey. Run it regularly, trust the process.*
