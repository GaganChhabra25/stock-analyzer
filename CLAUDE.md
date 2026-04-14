# Stock Analyzer — Claude Reference

## Project Purpose
Python CLI tool to analyze Zerodha direct equity + mutual fund holdings against a personal investment goal of **₹6 Crore in 15 years at 15% CAGR**.

## User Profile
- Long-term investor, moderate risk tolerance
- Holds direct NSE/BSE stocks + ETFs via Zerodha, plus Mutual Funds
- Wants actionable recommendations: HOLD / ADD MORE / EXIT / CONTINUE SIP / STOP SIP
- Interested in SIP optimization and goal projection against ₹6 Cr target

## How to Run
```bash
python main.py                  # Full analysis (stocks + MFs)
python main.py --stocks-only    # Only direct equity + ETFs
python main.py --mf-only        # Only mutual funds
python main.py --no-report      # Skip writing report files
python find_scheme.py           # Helper to search MF scheme codes on mfapi.in
```

## Project Structure
```
stock-analyzer/
├── main.py                        # CLI entry point
├── config.py                      # Goals, thresholds, file paths (user editable)
├── find_scheme.py                 # MF scheme code search helper
├── run_screener.py                # Stock universe screener
├── data/
│   ├── zerodha_holdings.csv       # Direct equity + ETF holdings (Zerodha export format)
│   ├── mf_holdings.csv            # Mutual fund holdings (manual)
│   └── active_trades.json         # Active trade tracking
├── analyzers/
│   ├── stock_analyzer.py          # yfinance-based stock scoring (Fundamentals + Valuation + Momentum)
│   ├── mf_analyzer.py             # mfapi.in-based MF analysis + return calculations
│   └── portfolio_analyzer.py      # Portfolio aggregation, projection, allocation warnings
├── screener/
│   ├── universe.py                # Stock universe definition
│   └── analyzer.py                # Screener analysis logic
├── utils/
│   ├── corpus_calculator.py       # FV/SIP/CAGR math for ₹6 Cr goal
│   ├── data_loader.py             # CSV loaders for holdings files
│   ├── report_generator.py        # Colored console + text file report
│   └── html_report.py             # HTML report generator
└── reports/
    ├── analysis_report.txt        # Text report output
    ├── portfolio_report.html      # HTML report output
    └── screener_report.html       # Screener HTML output
```

## Data Files

### zerodha_holdings.csv
Exported from Zerodha console. Format:
```
"Instrument","Qty.","Avg. cost","LTP","Invested","Cur. val","P&L","Net chg.","Day chg.",""
```
- ETFs are auto-detected by symbol (NIFTYBEES, GOLDBEES, BANKBEES, etc.)
- Direct stocks go through yfinance for fundamentals (`.NS` suffix added automatically)

### mf_holdings.csv
Manual file. Format:
```
Fund_Name,Scheme_Code,Monthly_SIP,Total_Units,Avg_Purchase_NAV,Expense_Ratio,Notes
```
- `Scheme_Code` is mfapi.in's numeric scheme code — use `find_scheme.py` to look up
- Currently empty; add MF holdings here to enable MF analysis

## Key Technical Notes

### Python Environment
- **Python 3.9** on Windows
- Avoid `X | None` type hints (Python 3.10+ syntax) — use `Optional[X]`
- UTF-8 stdout reconfiguration in `main.py` needed for ₹ symbol on Windows

### Stock Analysis (StockAnalyzer)
- Uses **yfinance** for NSE fundamentals
- Scoring model: `Fundamentals (50%) + Valuation (30%) + Momentum (20%)`
- Thresholds in `config.py`: min ROE 12%, max PE 60, max D/E 1.5, min current ratio 1.0
- ETFs analyzed separately (no yfinance fundamentals needed)

### MF Analysis (MFAnalyzer)
- Uses **mfapi.in** (free, no auth) for NAV history
- Calculates 1yr / 3yr / 5yr returns from NAV history
- Min return thresholds: 1yr 10%, 3yr 12%, 5yr 12%
- Preferred categories: Large Cap, Flexi Cap, Multi Cap, Large & Mid Cap, Index, BAF, Aggressive Hybrid

### Scoring & Recommendations
- Stocks: score 0–100, thresholds decide ADD MORE / HOLD / EXIT
- MFs: return + expense ratio + AUM + category alignment → CONTINUE SIP / ADD MORE / STOP SIP / EXIT
- Portfolio: allocation warnings if overweight single sector/stock

### Goal Projection
- `corpus_calculator.py` computes required monthly SIP + projected corpus at 15% CAGR
- `PortfolioAnalyzer.projection()` shows current trajectory vs ₹6 Cr goal

## Current Holdings Summary (as of data file)
- ~55 instruments: mix of large-cap stocks, ETFs (NIFTYBEES, GOLDBEES, BANKBEES, ITBEES, etc.)
- Notable positions: NIFTYBEES, TITAN, MARUTI, PIDILITIND, INFY, HCLTECH, TCS
- MF holdings: currently empty in CSV

## Dependencies
```
yfinance>=0.2.36
pandas>=2.0.0
numpy>=1.24.0
numpy_financial>=1.0.0
requests>=2.31.0
tabulate>=0.9.0
colorama>=0.4.6
matplotlib>=3.7.0
python-dateutil>=2.8.2
```
Install: `pip install -r requirements.txt`

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
