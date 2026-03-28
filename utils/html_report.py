"""
Generates a beautiful, self-contained HTML report — no external dependencies.
"""

import os
from datetime import datetime


# ── Helpers ──────────────────────────────────────────────────────────────────

def _cr(val, plain=False) -> str:
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if plain:
        if val >= 1e7:   return f"₹{val/1e7:.2f} Cr"
        if val >= 1e5:   return f"₹{val/1e5:.2f} L"
        return f"₹{val:,.0f}"
    if val >= 1e7:   return f"₹{val/1e7:.2f}&nbsp;Cr"
    if val >= 1e5:   return f"₹{val/1e5:.2f}&nbsp;L"
    return f"₹{val:,.0f}"


def _pct(val) -> str:
    if val is None: return "—"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _action_badge(action: str) -> str:
    a = action.upper().strip()
    if a == "EXIT":
        return '<span class="badge sell">✕ EXIT</span>'
    elif a == "ADD MORE":
        return '<span class="badge add">↑ ADD MORE</span>'
    else:
        return '<span class="badge hold">● HOLD</span>'


def _score_ring(score, is_etf=False) -> str:
    if score is None or is_etf:
        return '<div class="score-ring etf"><span>ETF</span></div>'
    s = float(score)
    if s >= 72:   cls = "great"
    elif s >= 58: cls = "good"
    elif s >= 42: cls = "warn"
    elif s >= 28: cls = "bad"
    else:         cls = "ugly"
    return f'<div class="score-ring {cls}"><span>{s:.0f}</span></div>'


def _pnl_cell(val) -> str:
    if val is None: return "<td>—</td>"
    cls = "pos" if val >= 0 else "neg"
    sign = "+" if val >= 0 else ""
    return f'<td class="{cls}">{sign}{val:.1f}%</td>'


# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  background: #f0f4f8;
  color: #1a202c;
  font-size: 13px;
}
a { color: inherit; text-decoration: none; }

/* ── Header ── */
.header {
  background: linear-gradient(135deg, #1a365d 0%, #2d6a4f 100%);
  color: white;
  padding: 32px 40px 24px;
}
.header h1 { font-size: 24px; font-weight: 700; letter-spacing: 0.5px; }
.header .subtitle { opacity: 0.75; font-size: 12px; margin-top: 4px; }

/* ── Stat cards ── */
.cards {
  display: flex; gap: 16px; flex-wrap: wrap;
  padding: 24px 40px;
}
.card {
  background: white;
  border-radius: 12px;
  padding: 18px 24px;
  flex: 1; min-width: 170px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.07);
  border-left: 4px solid #4a90d9;
}
.card.green  { border-left-color: #38a169; }
.card.red    { border-left-color: #e53e3e; }
.card.gold   { border-left-color: #d69e2e; }
.card.purple { border-left-color: #805ad5; }
.card.teal   { border-left-color: #319795; }
.card .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px; color: #718096; }
.card .value { font-size: 22px; font-weight: 700; margin-top: 4px; color: #1a202c; }
.card .sub   { font-size: 11px; color: #718096; margin-top: 3px; }

/* ── Sections ── */
.section {
  margin: 0 40px 32px;
  background: white;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.07);
  overflow: hidden;
}
.section-title {
  background: #2d3748;
  color: white;
  padding: 14px 24px;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.5px;
  display: flex; align-items: center; gap: 10px;
}
.section-title .icon { font-size: 16px; }

/* ── Tables ── */
table {
  width: 100%; border-collapse: collapse;
}
thead tr {
  background: #edf2f7;
}
thead th {
  padding: 10px 12px;
  text-align: left;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  color: #4a5568;
  font-weight: 600;
  white-space: nowrap;
}
tbody tr {
  border-bottom: 1px solid #f0f4f8;
  transition: background 0.15s;
}
tbody tr:hover { background: #f7fafc; }
td {
  padding: 10px 12px;
  vertical-align: middle;
}
.pos { color: #276749; font-weight: 600; }
.neg { color: #c53030; font-weight: 600; }

/* ── Score rings ── */
.score-ring {
  width: 42px; height: 42px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 13px; color: white;
  border: 3px solid transparent;
}
.score-ring.great { background: #276749; border-color: #38a169; }
.score-ring.good  { background: #2b6cb0; border-color: #4a90d9; }
.score-ring.warn  { background: #b7791f; border-color: #d69e2e; }
.score-ring.bad   { background: #c05621; border-color: #ed8936; }
.score-ring.ugly  { background: #9b2c2c; border-color: #e53e3e; }
.score-ring.etf   { background: #553c9a; border-color: #805ad5; font-size: 10px; }

/* ── Action badges ── */
.badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.badge.add    { background: #c6f6d5; color: #22543d; }
.badge.hold   { background: #bee3f8; color: #2a4365; }
.badge.review { background: #fefcbf; color: #744210; }
.badge.reduce { background: #feebc8; color: #7b341e; }
.badge.sell   { background: #fed7d7; color: #742a2a; }
.badge.na     { background: #e2e8f0; color: #4a5568; }

/* ── Metrics chips ── */
.metrics { display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  background: #edf2f7; border-radius: 4px;
  padding: 2px 7px; font-size: 10px; color: #4a5568;
}

/* ── Goal section ── */
.goal-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 24px;
  padding: 24px;
}
.goal-box { }
.goal-box h3 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.8px; color: #718096; margin-bottom: 12px; }
.goal-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #f0f4f8; font-size: 13px; }
.goal-row .lbl { color: #4a5568; }
.goal-row .val { font-weight: 600; }
.progress-wrap { margin-top: 16px; }
.progress-label { display: flex; justify-content: space-between; font-size: 11px; color: #718096; margin-bottom: 6px; }
.progress-bar { background: #e2e8f0; border-radius: 99px; height: 14px; overflow: hidden; }
.progress-fill {
  height: 100%; border-radius: 99px;
  background: linear-gradient(90deg, #38a169, #68d391);
  transition: width 0.6s ease;
}
.on-track-pill {
  display: inline-block;
  margin-top: 12px;
  padding: 6px 16px;
  border-radius: 99px;
  font-size: 12px;
  font-weight: 700;
}
.on-track-pill.yes { background: #c6f6d5; color: #22543d; }
.on-track-pill.no  { background: #fed7d7; color: #742a2a; }

/* ── Scenario cards ── */
.scenarios { display: flex; gap: 12px; padding: 0 24px 24px; }
.sc-card {
  flex: 1; border-radius: 8px; padding: 14px 16px;
  text-align: center;
}
.sc-card.cons { background: #fff5f5; border: 1px solid #fed7d7; }
.sc-card.base { background: #ebf8ff; border: 1px solid #bee3f8; }
.sc-card.opti { background: #f0fff4; border: 1px solid #c6f6d5; }
.sc-card .sc-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: #718096; }
.sc-card .sc-value { font-size: 20px; font-weight: 700; margin-top: 4px; }
.sc-card.cons .sc-value { color: #c53030; }
.sc-card.base .sc-value { color: #2b6cb0; }
.sc-card.opti .sc-value { color: #276749; }
.sc-card .sc-vs {
  font-size: 10px; margin-top: 4px;
  color: #718096;
}

/* ── Warnings ── */
.warnings { padding: 16px 24px; }
.warn-item {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 10px 14px; border-radius: 8px;
  background: #fffbeb; border: 1px solid #f6e05e;
  margin-bottom: 8px; font-size: 12px; color: #744210;
}
.warn-item .wi { font-size: 14px; }

/* ── Legend ── */
.legend {
  display: flex; flex-wrap: wrap; gap: 10px;
  padding: 16px 40px 32px;
}
.leg-item { display: flex; align-items: center; gap: 6px; font-size: 11px; color: #4a5568; }
.leg-dot { width: 12px; height: 12px; border-radius: 3px; }

/* ── Footer ── */
.footer {
  text-align: center;
  padding: 20px;
  font-size: 11px;
  color: #a0aec0;
  border-top: 1px solid #e2e8f0;
  margin: 0 40px 40px;
}

/* ── SIP Simulator Panel ── */
.sim-panel {
  padding: 20px 24px;
  background: linear-gradient(135deg, #f0f9ff 0%, #f7fafc 100%);
  border-bottom: 1px solid #e2e8f0;
}
.sim-title {
  font-size: 13px; font-weight: 700; color: #1a365d;
  margin-bottom: 14px;
  display: flex; align-items: center; gap: 8px;
}
.sim-inputs { display: flex; gap: 16px; align-items: flex-end; flex-wrap: wrap; }
.sim-field { display: flex; flex-direction: column; gap: 4px; }
.sim-field label {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px;
  color: #718096; font-weight: 600;
}
.sim-input-wrap {
  display: flex; align-items: center; gap: 4px;
  border: 1.5px solid #cbd5e0; border-radius: 6px;
  background: white; padding: 6px 10px;
  transition: border-color 0.15s;
}
.sim-input-wrap:focus-within { border-color: #4a90d9; }
.sim-input-wrap input {
  border: none; outline: none; font-size: 14px; font-weight: 600;
  color: #1a202c; background: transparent;
}
.sim-input-wrap input.w-goal { width: 120px; }
.sim-input-wrap input.w-short { width: 55px; }
.sim-input-wrap span { font-size: 13px; color: #4a5568; font-weight: 600; }
.sim-btn {
  padding: 8px 20px; background: #2d3748; color: white;
  border: none; border-radius: 6px; font-size: 12px; font-weight: 600;
  cursor: pointer; letter-spacing: 0.4px; white-space: nowrap;
  transition: background 0.15s;
}
.sim-btn:hover { background: #1a202c; }
.sim-results { margin-top: 16px; }
.sim-result-row { display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 10px; }
.sim-result-item { }
.sim-result-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px; color: #718096; }
.sim-result-value { font-size: 18px; font-weight: 700; color: #1a202c; margin-top: 2px; }
.sim-progress-wrap { margin-top: 8px; }
.sim-pbar { background: #e2e8f0; border-radius: 99px; height: 10px; overflow: hidden; }
.sim-pfill { height: 100%; border-radius: 99px; background: linear-gradient(90deg, #38a169, #68d391); transition: width 0.4s ease; }
.sim-plabels { display: flex; justify-content: space-between; align-items: center; font-size: 11px; color: #718096; margin-top: 5px; }

/* ── Editable SIP input in MF table ── */
.sip-input {
  width: 88px; padding: 4px 8px; text-align: right;
  border: 1.5px solid #cbd5e0; border-radius: 4px;
  font-size: 13px; font-weight: 600; color: #1a202c; outline: none;
}
.sip-input:focus { border-color: #4a90d9; background: #ebf8ff; }
.proj-td { min-width: 120px; line-height: 1.5; }

/* ── Print ── */
@media print {
  body { background: white; font-size: 11px; }
  .cards { page-break-inside: avoid; }
  .section { page-break-inside: avoid; box-shadow: none; border: 1px solid #e2e8f0; }
  .header { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  .sim-btn { display: none; }
}
"""

# ── Build HTML ────────────────────────────────────────────────────────────────

def _stock_rows(results: list) -> str:
    rows = []
    for r in results:
        km = r.get("key_metrics", "")
        # Parse metrics into chips
        chips_html = ""
        if km and r.get("pe") is not None:
            parts = [
                f'<span class="chip">PE {r["pe"]}</span>',
                f'<span class="chip">ROE {r["roe"]}%</span>',
                f'<span class="chip">D/E {r["debt_equity"]}</span>',
            ]
            chips_html = f'<div class="metrics">{"".join(parts)}</div>'

        reason_short = r.get("reason", "")[:90]

        rows.append(f"""
        <tr>
          <td>{_score_ring(r.get("total_score"), r.get("is_etf", False))}</td>
          <td><strong>{r['symbol']}</strong></td>
          <td>{r['quantity']}</td>
          <td>{_cr(r['avg_cost'])}</td>
          <td>{_cr(r.get('current_price'))}</td>
          <td>{_cr(r.get('current_value'))}</td>
          {_pnl_cell(r.get('gain_loss_pct'))}
          <td>{_action_badge(r['action'])}</td>
          <td>{chips_html}<div style="font-size:10px;color:#718096;margin-top:3px">{reason_short}</div></td>
        </tr>""")
    return "\n".join(rows)


def _etf_rows(results: list) -> str:
    rows = []
    for r in results:
        rows.append(f"""
        <tr>
          <td>{_score_ring(None, True)}</td>
          <td><strong>{r['symbol']}</strong></td>
          <td style="font-size:11px;color:#4a5568">{r.get('name','')}</td>
          <td>{r['quantity']}</td>
          <td>{_cr(r.get('current_price'))}</td>
          <td>{_cr(r.get('current_value'))}</td>
          {_pnl_cell(r.get('gain_loss_pct'))}
          <td><span style="font-size:11px;color:#553c9a;background:#faf5ff;padding:2px 8px;border-radius:4px">{r.get('etf_category','')}</span></td>
          <td>{_action_badge(r['action'])}</td>
        </tr>""")
    return "\n".join(rows)


def _mf_rows(results: list) -> str:
    if not results:
        return '<tr><td colspan="10" style="text-align:center;padding:20px;color:#718096">No Mutual Fund data loaded. Add your MF holdings to data/mf_holdings.csv</td></tr>'
    rows = []
    for r in results:
        r1 = f"{r['return_1yr']:+.1f}%" if r.get("return_1yr") is not None else "—"
        r3 = f"{r['return_3yr']:+.1f}%" if r.get("return_3yr") is not None else "—"
        r5 = f"{r['return_5yr']:+.1f}%" if r.get("return_5yr") is not None else "—"
        cur_val = r.get('current_value') or 0
        sip_val = int(r.get('monthly_sip') or 0)
        rows.append(f"""
        <tr data-curval="{cur_val}">
          <td><strong style="font-size:12px">{r['fund_name'][:45]}</strong>
              <div style="font-size:10px;color:#718096;margin-top:2px">{r.get('category','')}</div></td>
          <td>{_cr(r.get('invested'))}</td>
          <td>{_cr(r.get('current_value'))}</td>
          {_pnl_cell(r.get('gain_loss_pct'))}
          <td class="{'pos' if r.get('return_1yr') and r['return_1yr']>=12 else 'neg' if r.get('return_1yr') and r['return_1yr']<8 else ''}">{r1}</td>
          <td class="{'pos' if r.get('return_3yr') and r['return_3yr']>=12 else 'neg' if r.get('return_3yr') and r['return_3yr']<8 else ''}">{r3}</td>
          <td class="{'pos' if r.get('return_5yr') and r['return_5yr']>=12 else 'neg' if r.get('return_5yr') and r['return_5yr']<8 else ''}">{r5}</td>
          <td><input class="sip-input" type="number" value="{sip_val}" min="0" step="500" title="Edit monthly SIP for this fund"></td>
          <td class="proj-td">—</td>
          <td>{_action_badge(r['action'])}</td>
        </tr>""")
    return "\n".join(rows)


def generate_html_report(stock_results: list, etf_results: list, mf_results: list,
                         summary: dict, projection: dict, warnings: list,
                         output_file: str):

    ts       = datetime.now().strftime("%d %b %Y, %I:%M %p")
    total    = summary["total_value"]
    invested = summary["total_invested"]
    pnl      = total - invested
    pnl_pct  = (pnl / invested * 100) if invested else 0
    mf_sip   = summary.get("monthly_sip", 0)

    stocks_invested = summary.get("stocks_invested", 0)
    stocks_value    = summary.get("stocks_value", 0)
    stocks_pl       = summary.get("stocks_pl", 0)
    stocks_pl_pct   = summary.get("stocks_pl_pct", 0)
    mf_invested     = summary.get("mf_invested", 0)
    mf_value        = summary.get("mf_value", 0)
    mf_pl           = summary.get("mf_pl", 0)
    mf_pl_pct       = summary.get("mf_pl_pct", 0)

    prog_pct = min(100, projection["total_projected"] / projection["target_corpus"] * 100)

    # Count actions
    exit_count = sum(1 for r in stock_results if r["action"] == "EXIT")
    add_count  = sum(1 for r in stock_results if r["action"] == "ADD MORE")
    hold_count = sum(1 for r in stock_results if r["action"] == "HOLD")

    warnings_html = "".join(
        f'<div class="warn-item"><span class="wi">⚠</span><span>{w}</span></div>'
        for w in warnings
    ) if warnings else '<div style="padding:12px 24px;color:#276749;font-size:12px">✓ No critical warnings</div>'

    sc = projection["scenarios"]
    sc_vals = list(sc.values())

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Portfolio Analysis Report — {ts}</title>
<style>{CSS}</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <h1>📊 Portfolio Analysis Report</h1>
  <div class="subtitle">Generated on {ts} &nbsp;|&nbsp; Goal: ₹6 Crore in 15 Years @ 15% CAGR &nbsp;|&nbsp; NSE / BSE Holdings</div>
  <div style="margin-top:12px">
    <a href="screener_report.html" target="_blank" style="
      display:inline-flex;align-items:center;gap:7px;
      background:rgba(255,255,255,0.12);border:1px solid rgba(255,255,255,0.25);
      color:white;padding:7px 16px;border-radius:99px;font-size:12px;font-weight:600;
      text-decoration:none;transition:background 0.15s;letter-spacing:0.3px">
      🔭 Open Market Screener — NSE High-Probability Setups
    </a>
  </div>
</div>

<!-- Portfolio Breakdown -->
<div style="padding: 20px 40px 0; display:flex; gap:12px; align-items:center;">
  <span style="font-size:11px;text-transform:uppercase;letter-spacing:0.8px;color:#718096;font-weight:600">Portfolio Breakdown</span>
  <span style="font-size:11px;color:#a0aec0">{len(stock_results)} stocks · {len(etf_results)} ETFs · {len(mf_results)} MFs</span>
</div>

<!-- Stocks Row -->
<div style="padding: 8px 40px 0;">
  <div style="font-size:11px;font-weight:700;color:#2d6a4f;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px">📈 Direct Stocks &amp; ETFs</div>
</div>
<div class="cards" style="padding-top:0">
  <div class="card">
    <div class="label">Invested</div>
    <div class="value">{_cr(stocks_invested, True)}</div>
    <div class="sub">{len(stock_results)} stocks + {len(etf_results)} ETFs</div>
  </div>
  <div class="card teal">
    <div class="label">Current Value</div>
    <div class="value">{_cr(stocks_value, True)}</div>
    <div class="sub">{summary.get('stocks_pct', 0):.1f}% of portfolio</div>
  </div>
  <div class="card {'green' if stocks_pl >= 0 else 'red'}">
    <div class="label">P&amp;L</div>
    <div class="value">{_cr(stocks_pl, True)}</div>
    <div class="sub">{_pct(stocks_pl_pct)} overall return</div>
  </div>
  <div class="card">
    <div class="label">Actions</div>
    <div class="value" style="font-size:14px;display:flex;gap:8px;align-items:center;margin-top:6px">
      <span style="color:#276749">▲ {add_count}</span>
      <span style="color:#2b6cb0">● {hold_count}</span>
      <span style="color:#c53030">✕ {exit_count}</span>
    </div>
    <div class="sub">Add · Hold · Exit</div>
  </div>
</div>

<!-- MF Row -->
<div style="padding: 4px 40px 0;">
  <div style="font-size:11px;font-weight:700;color:#2b6cb0;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px">🏦 Mutual Funds</div>
</div>
<div class="cards" style="padding-top:0">
  <div class="card">
    <div class="label">Invested</div>
    <div class="value">{_cr(mf_invested, True)}</div>
    <div class="sub">{len(mf_results)} funds</div>
  </div>
  <div class="card teal">
    <div class="label">Current Value</div>
    <div class="value">{_cr(mf_value, True)}</div>
    <div class="sub">{summary.get('mf_pct', 0):.1f}% of portfolio</div>
  </div>
  <div class="card {'green' if mf_pl >= 0 else 'red'}">
    <div class="label">P&amp;L</div>
    <div class="value">{_cr(mf_pl, True)}</div>
    <div class="sub">{_pct(mf_pl_pct)} overall return</div>
  </div>
  <div class="card purple">
    <div class="label">Monthly SIP</div>
    <div class="value">{_cr(mf_sip, True)}</div>
    <div class="sub">active SIP/month</div>
  </div>
</div>

<!-- Summary Row -->
<div style="padding: 4px 40px 0;">
  <div style="font-size:11px;font-weight:700;color:#744210;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px">🎯 Goal Summary</div>
</div>
<div class="cards" style="padding-top:0;padding-bottom:24px">
  <div class="card teal">
    <div class="label">Total Portfolio Value</div>
    <div class="value">{_cr(total, True)}</div>
    <div class="sub">Invested: {_cr(invested, True)} · {_pct(pnl_pct)}</div>
  </div>
  <div class="card gold">
    <div class="label">Corpus Projected</div>
    <div class="value">{_cr(projection['total_projected'], True)}</div>
    <div class="sub">Target: {_cr(projection['target_corpus'], True)}</div>
  </div>
  <div class="card purple">
    <div class="label">SIP Needed / Month</div>
    <div class="value">{_cr(projection['required_sip'], True)}</div>
    <div class="sub">to hit target @ 15% CAGR</div>
  </div>
</div>

<!-- Warnings -->
<div class="section">
  <div class="section-title"><span class="icon">⚠</span> Portfolio Warnings & Action Items</div>
  <div class="warnings">{warnings_html}</div>
</div>

<!-- Direct Stocks -->
<div class="section">
  <div class="section-title"><span class="icon">📈</span> Direct Equity Holdings — Fundamental Analysis</div>
  <table>
    <thead>
      <tr>
        <th style="width:50px">Score</th>
        <th>Symbol</th>
        <th>Qty</th>
        <th>Avg Cost</th>
        <th>CMP</th>
        <th>Cur Value</th>
        <th>P&amp;L %</th>
        <th>Action</th>
        <th>Metrics &amp; Reason</th>
      </tr>
    </thead>
    <tbody>
      {_stock_rows(stock_results)}
    </tbody>
  </table>
</div>

<!-- ETFs -->
<div class="section">
  <div class="section-title"><span class="icon">🗂</span> ETF &amp; Index Fund Holdings</div>
  <table>
    <thead>
      <tr>
        <th style="width:50px">Type</th>
        <th>Symbol</th>
        <th>ETF Name</th>
        <th>Qty</th>
        <th>CMP</th>
        <th>Cur Value</th>
        <th>P&amp;L %</th>
        <th>Category</th>
        <th>Action</th>
      </tr>
    </thead>
    <tbody>
      {_etf_rows(etf_results)}
    </tbody>
  </table>
</div>

<!-- Mutual Funds -->
<div class="section">
  <div class="section-title"><span class="icon">🏦</span> Mutual Fund Holdings — SIP Simulator</div>

  <!-- SIP Simulator Panel -->
  <div class="sim-panel">
    <div class="sim-title">🎯 SIP Goal Simulator — Enter your target &amp; SIP amounts, see projections instantly</div>
    <div class="sim-inputs">
      <div class="sim-field">
        <label>Target Goal</label>
        <div class="sim-input-wrap"><span>₹</span><input id="sim-goal" class="w-goal" type="number" value="{int(projection['target_corpus'])}" min="100000" step="100000"></div>
      </div>
      <div class="sim-field">
        <label>Time Horizon</label>
        <div class="sim-input-wrap"><input id="sim-years" class="w-short" type="number" value="{projection['years']}" min="1" max="40"><span>yrs</span></div>
      </div>
      <div class="sim-field">
        <label>Expected CAGR</label>
        <div class="sim-input-wrap"><input id="sim-cagr" class="w-short" type="number" value="{int(projection['annual_rate']*100)}" min="1" max="40" step="0.5"><span>%</span></div>
      </div>
      <button class="sim-btn" onclick="updateProjections()">⟳ Recalculate</button>
    </div>
    <div class="sim-results" id="sim-results"><!-- filled by JS on load --></div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Fund Name &amp; Category</th>
        <th>Invested</th>
        <th>Cur Value</th>
        <th>P&amp;L %</th>
        <th>1Y CAGR</th>
        <th>3Y CAGR</th>
        <th>5Y CAGR</th>
        <th>Monthly SIP ✎</th>
        <th>Projected Corpus</th>
        <th>Action</th>
      </tr>
    </thead>
    <tbody id="mf-tbody">
      {_mf_rows(mf_results)}
    </tbody>
  </table>
</div>

<!-- Goal Projection -->
<div class="section">
  <div class="section-title"><span class="icon">🎯</span> Corpus Goal Projection</div>
  <div class="goal-grid">
    <div class="goal-box">
      <h3>Projection Details @ 15% CAGR</h3>
      <div class="goal-row"><span class="lbl">Current Portfolio Value</span><span class="val">{_cr(projection['current_portfolio'], True)}</span></div>
      <div class="goal-row"><span class="lbl">FV of Current Portfolio</span><span class="val">{_cr(projection['fv_portfolio'], True)}</span></div>
      <div class="goal-row"><span class="lbl">Monthly SIP</span><span class="val">{_cr(projection['monthly_sip'], True)}/mo</span></div>
      <div class="goal-row"><span class="lbl">FV of Future SIPs</span><span class="val">{_cr(projection['fv_sip'], True)}</span></div>
      <div class="goal-row" style="border:none"><span class="lbl"><strong>Projected Corpus</strong></span><span class="val" style="font-size:16px;color:#2b6cb0">{_cr(projection['total_projected'], True)}</span></div>
    </div>
    <div class="goal-box">
      <h3>Progress to ₹6 Crore Target</h3>
      <div class="progress-wrap">
        <div class="progress-label">
          <span>₹0</span>
          <span>Projected: {_cr(projection['total_projected'], True)}</span>
          <span>Target: {_cr(projection['target_corpus'], True)}</span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" style="width:{prog_pct:.1f}%"></div>
        </div>
        <div style="font-size:11px;color:#718096;margin-top:6px;text-align:right">{prog_pct:.1f}% of target achieved</div>
      </div>
      <div class="goal-row" style="margin-top:12px"><span class="lbl">Gap to Target</span><span class="val" style="color:#c53030">{_cr(projection['gap'], True)}</span></div>
      <div class="goal-row" style="border:none"><span class="lbl">Required SIP to close gap</span><span class="val" style="color:#d69e2e">{_cr(projection['required_sip'], True)}/mo</span></div>
      <div>
        <span class="on-track-pill {'yes' if projection['on_track'] else 'no'}">
          {'✓ ON TRACK' if projection['on_track'] else '✗ NOT ON TRACK — Start SIP Immediately'}
        </span>
      </div>
    </div>
  </div>
  <div class="scenarios">
    <div class="sc-card cons">
      <div class="sc-label">Conservative 12%</div>
      <div class="sc-value">{_cr(sc_vals[0], True)}</div>
      <div class="sc-vs">vs target {_cr(projection['target_corpus'], True)}</div>
    </div>
    <div class="sc-card base">
      <div class="sc-label">Base Case 15%</div>
      <div class="sc-value">{_cr(sc_vals[1], True)}</div>
      <div class="sc-vs">vs target {_cr(projection['target_corpus'], True)}</div>
    </div>
    <div class="sc-card opti">
      <div class="sc-label">Optimistic 18%</div>
      <div class="sc-value">{_cr(sc_vals[2], True)}</div>
      <div class="sc-vs">vs target {_cr(projection['target_corpus'], True)}</div>
    </div>
  </div>
</div>

<!-- Legend -->
<div class="legend">
  <div class="leg-item"><div class="leg-dot" style="background:#276749"></div> Score ≥72 — ADD MORE</div>
  <div class="leg-item"><div class="leg-dot" style="background:#2b6cb0"></div> Score 42–71 — HOLD</div>
  <div class="leg-item"><div class="leg-dot" style="background:#9b2c2c"></div> Score &lt;42 — EXIT</div>
  <div class="leg-item"><div class="leg-dot" style="background:#553c9a"></div> ETF — category-based advice</div>
</div>

<!-- Footer -->
<div class="footer">
  This report is for informational purposes only. Not financial advice. Always consult a SEBI-registered advisor before making investment decisions. &nbsp;|&nbsp; Generated {ts}
</div>

<script>
function fmtINR(v) {{
  v = Math.round(v);
  if (v >= 1e7) return '\u20B9' + (v/1e7).toFixed(2) + '\u00A0Cr';
  if (v >= 1e5) return '\u20B9' + (v/1e5).toFixed(2) + '\u00A0L';
  return '\u20B9' + v.toLocaleString('en-IN');
}}

function sipFV(sip, annualRate, years) {{
  if (sip <= 0 || annualRate <= 0 || years <= 0) return 0;
  var r = annualRate / 12, n = years * 12;
  return sip * ((Math.pow(1 + r, n) - 1) / r) * (1 + r);
}}

function lumpsumFV(pv, annualRate, years) {{
  if (pv <= 0 || years <= 0) return 0;
  return pv * Math.pow(1 + annualRate, years);
}}

function updateProjections() {{
  var goal  = parseFloat(document.getElementById('sim-goal').value)  || 60000000;
  var years = parseFloat(document.getElementById('sim-years').value) || 15;
  var cagr  = parseFloat(document.getElementById('sim-cagr').value) / 100 || 0.15;

  var rows = document.querySelectorAll('#mf-tbody tr[data-curval]');
  var totalSIP = 0, totalProjected = 0;

  rows.forEach(function(row) {{
    var curVal   = parseFloat(row.dataset.curval) || 0;
    var sipInput = row.querySelector('.sip-input');
    var projTd   = row.querySelector('.proj-td');
    var sip = sipInput ? (parseFloat(sipInput.value) || 0) : 0;
    totalSIP += sip;

    var fvLump  = lumpsumFV(curVal, cagr, years);
    var fvSip   = sipFV(sip, cagr, years);
    var total   = fvLump + fvSip;
    totalProjected += total;

    if (projTd) {{
      var sipPart = sip > 0
        ? '<span style="font-size:10px;color:#4a5568">+\u00A0SIP\u00A0' + fmtINR(fvSip) + '</span><br>'
        : '';
      projTd.innerHTML =
        '<span style="font-size:10px;color:#718096">Lump:\u00A0' + fmtINR(fvLump) + '</span><br>' +
        sipPart +
        '<strong style="color:#2b6cb0;font-size:13px">' + fmtINR(total) + '</strong>';
    }}
  }});

  var gap    = goal - totalProjected;
  var onTrack = totalProjected >= goal;
  var pct    = Math.min(100, (totalProjected / goal * 100));
  var fillColor = onTrack ? 'linear-gradient(90deg,#38a169,#68d391)' : 'linear-gradient(90deg,#e53e3e,#fc8181)';

  document.getElementById('sim-results').innerHTML =
    '<div class="sim-result-row">' +
      '<div class="sim-result-item"><div class="sim-result-label">Total Monthly SIP</div>' +
        '<div class="sim-result-value">' + fmtINR(totalSIP) + '/mo</div></div>' +
      '<div class="sim-result-item"><div class="sim-result-label">Projected Corpus (' + years + 'Y\u00A0@\u00A0' + (cagr*100).toFixed(1) + '%)</div>' +
        '<div class="sim-result-value" style="color:#2b6cb0">' + fmtINR(totalProjected) + '</div></div>' +
      '<div class="sim-result-item"><div class="sim-result-label">Target Goal</div>' +
        '<div class="sim-result-value">' + fmtINR(goal) + '</div></div>' +
      '<div class="sim-result-item"><div class="sim-result-label">' + (onTrack ? 'Surplus' : 'Gap to Goal') + '</div>' +
        '<div class="sim-result-value" style="color:' + (onTrack ? '#276749' : '#c53030') + '">' + fmtINR(Math.abs(gap)) + '</div></div>' +
    '</div>' +
    '<div class="sim-progress-wrap">' +
      '<div class="sim-pbar"><div class="sim-pfill" style="width:' + pct.toFixed(1) + '%;background:' + fillColor + '"></div></div>' +
      '<div class="sim-plabels">' +
        '<span>' + pct.toFixed(1) + '% of target achieved</span>' +
        '<span class="on-track-pill ' + (onTrack ? 'yes' : 'no') + '">' +
          (onTrack ? '\u2713 ON TRACK' : '\u2717 NOT ON TRACK') +
        '</span>' +
      '</div>' +
    '</div>';
}}

document.addEventListener('DOMContentLoaded', function() {{
  updateProjections();
  ['sim-goal','sim-years','sim-cagr'].forEach(function(id) {{
    document.getElementById(id).addEventListener('input', updateProjections);
  }});
  document.querySelectorAll('.sip-input').forEach(function(inp) {{
    inp.addEventListener('input', updateProjections);
  }});
}});
</script>

</body>
</html>"""

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)
    return output_file
