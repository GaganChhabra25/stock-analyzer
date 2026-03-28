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

# Add a stock to persistent active trade tracking
python run_screener.py --track MCX
python run_screener.py --track INFY --price 1450 --direction UP

# Stop tracking a stock
python run_screener.py --untrack MCX
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
- **Seasonal history** — year-by-year returns for the last 5 years
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

## Screener Report — Interactive Features

### Clickable Stock Navigation

Every stock symbol in the **summary table**, **current month table**, and **next month table** is a clickable link. Clicking it smoothly scrolls to that stock's detailed analysis card and briefly highlights it in blue — no manual searching needed.

---

### Active Trades Panel

A persistent trades tracker built into every screener report. Trades are stored in your browser's localStorage and survive page reloads and new scans.

**Add a screener pick to Active Trades:**
Check the **Take Trade** checkbox in the rightmost column of the summary table. The stock is immediately added to the Active Trades panel at the top.

**What the panel shows for each trade:**

| Column | What it shows |
|--------|--------------|
| Stock | Symbol, name, MANUAL badge if manually added |
| Sector | Stock's sector |
| CMP / P&L | Live CMP from current scan · Entry price · % gain/loss since entry |
| Direction | ▲ UP or ▼ DOWN at time of entry |
| Probability | Live probability from current scan (with ±delta vs entry if changed) |
| Trade Taken At | Timestamp when you added the trade |
| Action | ✓ Mark Complete — removes from tracking |

Trades persist across scans. When you run the screener again, the panel updates with fresh CMP and recalculated P&L for all active trades.

---

### Track Any Stock (Not Just Top Picks)

A **"Track Another Stock"** form is always visible just above the summary table. Use it to track any NSE stock — even one that didn't make the top-15 cut.

**How to use:**
1. **Search** — type a company name or symbol (e.g. "MCX" or "multi comm") in the search box. A dropdown shows matching stocks with their live CMP, probability, and direction from the current scan.
2. **Select** a stock from the dropdown — entry price and direction auto-fill from scan data.
3. Optionally override the **entry price** and **direction**.
4. Click **+ Track Stock**.

The stock is instantly added to the Active Trades panel with real probability and CMP data.

**What the search covers:** All stocks that passed the liquidity filter in the current scan (~120 in full mode, ~60 in fast mode) — not just the top 15. This means you can track virtually any liquid NSE stock.

---

### Persistent Tracking via CLI

For stocks you always want tracked (across multiple scans), use the `--track` flag instead of the in-browser form:

```bash
# Add MCX to persistent tracking with your entry price
python run_screener.py --track MCX --price 5800 --direction UP

# Auto-detect direction from current technical analysis
python run_screener.py --track INFY

# Stop tracking
python run_screener.py --untrack MCX
```

When a symbol is tracked via CLI:
- It is saved to `data/active_trades.json`
- On **every subsequent run**, it is downloaded and fully analyzed — even if it doesn't make the top-N cut
- It appears in a **"Your Tracked Stocks"** section at the bottom of the report with a full detail card (blue border, 📌 TRACKED badge)
- Entry price, direction, and probability at time of first `--track` are saved as the baseline for change detection

---

### Change Alerts

Every time the report opens, it compares the **current analysis** of your active trades against the **snapshot taken when you entered the trade**.

Two types of alerts are shown in a banner at the top of the page:

| Alert | Trigger | Color |
|-------|---------|-------|
| Direction flip | Stock changed from UP → DOWN or DOWN → UP | 🔴 Red |
| Probability shift | Probability moved ≥ 5% from entry baseline | 🟡 Yellow |

The banner lists every affected stock with the before/after values. No alert = no changes.

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
│   ├── mf_holdings.csv          # Your MF holdings (fill manually)
│   └── active_trades.json       # Tracked stocks — auto-managed by screener
│
├── analyzers/
│   ├── stock_analyzer.py        # Scores direct equity stocks via yfinance
│   ├── mf_analyzer.py           # Analyzes MFs via mfapi.in
│   └── portfolio_analyzer.py    # Aggregates results, runs projections
│
├── screener/
│   ├── universe.py              # NSE stock universe with name/sector metadata
│   ├── analyzer.py              # Screening engine: technicals + month predictions
│   └── report.py                # Dark-theme self-contained HTML screener report
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

# Track a stock persistently
python run_screener.py --track SYMBOL --price PRICE --direction UP
```

### Step 5 — Open reports
```
reports/portfolio_report.html   ← Portfolio analysis
reports/screener_report.html    ← Market screener
```

To save as PDF: Chrome → `Ctrl+P` → Save as PDF

---

## Web App — Running Locally

The project includes a Flask web dashboard that lets you run analysis and view reports from a browser, protected by Google OAuth login.

### Prerequisites

- Python 3.9+
- A Google Cloud OAuth 2.0 Client ID (free)

### Step 1 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 2 — Create a Google OAuth Client ID

1. Go to [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)
2. Click **Create Credentials → OAuth 2.0 Client ID**
3. Application type: **Web application**
4. Under **Authorized redirect URIs**, add:
   ```
   http://127.0.0.1:5000/auth/callback
   http://localhost:5000/auth/callback
   ```
5. Copy the **Client ID** and **Client Secret**

### Step 3 — Create your `.env` file

Copy the example and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```env
FLASK_SECRET_KEY=any-long-random-string-here
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
ALLOWED_EMAILS=youremail@gmail.com
```

> Only emails listed in `ALLOWED_EMAILS` can log in. Separate multiple with commas.

### Step 4 — Run the app

```bash
python app.py
```

Open **http://127.0.0.1:5000** in your browser. Sign in with your Google account.

### What the dashboard does

| Button | What it runs | Output |
|--------|-------------|--------|
| **Run Analysis** | `python main.py` | `reports/portfolio_report.html` |
| **Run Screener** | `python run_screener.py --no-open` | `reports/screener_report.html` |

Jobs run in the background — the dashboard polls for status and shows a live log. Click **Open Report** when done.

### Notes

- The `.env` file is gitignored — never commit it
- On Windows, make sure your terminal supports UTF-8 (the app handles this automatically via `python-dotenv`)
- For production deployment, use gunicorn behind nginx — see `deploy/` folder

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
- **Active trades storage**: Trades added via the in-browser form are stored in browser localStorage — they persist across page reloads but are browser-specific. Use `--track` CLI for device-independent persistence.
- **Not financial advice**: For personal tracking and decision-support only. Always consult a SEBI-registered advisor before acting.

---

*Built for a 15-year wealth creation journey. Run it regularly, trust the process.*
