"""
Generates a beautiful, self-contained HTML screener report.
No external CSS/JS dependencies — works 100% offline.
"""

import json
import os
from datetime import datetime


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pct(val, plus=True):
    if val is None: return "—"
    sign = "+" if (val >= 0 and plus) else ""
    return f"{sign}{val:.1f}%"


def _dir_badge(direction):
    if direction == "UP":
        return '<span class="dir-badge up">▲ BULLISH</span>'
    return '<span class="dir-badge dn">▼ BEARISH</span>'


def _score_gauge(score):
    if score >= 70: cls = "sg-great"
    elif score >= 55: cls = "sg-good"
    elif score >= 40: cls = "sg-warn"
    else: cls = "sg-bad"
    return f'<div class="score-gauge {cls}">{score:.0f}</div>'


def _ret_cell(ret):
    if ret is None: return "<td>—</td>"
    cls = "pos" if ret >= 0 else "neg"
    sign = "+" if ret >= 0 else ""
    return f'<td class="{cls}">{sign}{ret:.1f}%</td>'


# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  background: #0d1117;
  color: #e6edf3;
  font-size: 13px;
  min-height: 100vh;
}

/* ── Header ── */
.header {
  background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #1c2333 100%);
  border-bottom: 1px solid #30363d;
  padding: 28px 48px 20px;
  position: relative;
  overflow: hidden;
}
.header::before {
  content: '';
  position: absolute; top: -60px; right: -60px;
  width: 300px; height: 300px; border-radius: 50%;
  background: radial-gradient(circle, rgba(56,161,105,0.12) 0%, transparent 70%);
}
.header::after {
  content: '';
  position: absolute; bottom: -40px; left: 20%;
  width: 200px; height: 200px; border-radius: 50%;
  background: radial-gradient(circle, rgba(88,166,255,0.07) 0%, transparent 70%);
}
.header h1 {
  font-size: 24px; font-weight: 800; letter-spacing: 0.5px;
  background: linear-gradient(90deg, #58a6ff, #56d364);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
}
.header .subtitle { color: #8b949e; font-size: 12px; margin-top: 5px; }
.header .method-note {
  margin-top: 12px; padding: 9px 14px;
  background: rgba(56,161,105,0.08); border: 1px solid rgba(56,161,105,0.2);
  border-radius: 8px; font-size: 11px; color: #7ee787; max-width: 820px;
  line-height: 1.6;
}

/* ── Main layout ── */
.main { padding: 20px 48px 48px; }

/* ── Section title ── */
.sec-title {
  font-size: 13px; font-weight: 700; letter-spacing: 0.5px;
  color: #e6edf3; margin: 24px 0 12px;
  display: flex; align-items: center; gap: 10px;
}
.sec-title::after {
  content: ''; flex: 1; height: 1px; background: #30363d;
}

/* ── Summary table ── */
.summary-table-wrap {
  background: #161b22; border: 1px solid #30363d;
  border-radius: 12px; overflow: hidden; margin-bottom: 28px;
}
table { width: 100%; border-collapse: collapse; }
thead tr { background: #1c2333; }
thead th {
  padding: 10px 13px; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.7px;
  color: #8b949e; font-weight: 600; text-align: left;
  white-space: nowrap;
}
tbody tr {
  border-top: 1px solid #21262d;
  transition: background 0.15s; cursor: default;
}
tbody tr:hover { background: #1c2333; }
td { padding: 10px 13px; vertical-align: middle; }
.pos { color: #56d364; font-weight: 600; }
.neg { color: #f85149; font-weight: 600; }
.flat { color: #d29922; font-weight: 600; }

/* ── Direction badges ── */
.dir-badge {
  display: inline-block; padding: 3px 10px; border-radius: 99px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.3px;
  white-space: nowrap;
}
.dir-badge.up { background: rgba(86,211,100,0.15); color: #56d364; border: 1px solid rgba(86,211,100,0.3); }
.dir-badge.dn { background: rgba(248,81,73,0.15);  color: #f85149; border: 1px solid rgba(248,81,73,0.3); }

/* ── Score gauge ── */
.score-gauge {
  width: 38px; height: 38px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700; border: 2px solid transparent;
  flex-shrink: 0;
}
.sg-great { background: #1a3a2a; border-color: #56d364; color: #56d364; }
.sg-good  { background: #1a2d45; border-color: #58a6ff; color: #58a6ff; }
.sg-warn  { background: #3a2e10; border-color: #d29922; color: #d29922; }
.sg-bad   { background: #3a1a1a; border-color: #f85149; color: #f85149; }

/* ── Candidate cards ── */
.cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(500px, 1fr));
  gap: 18px;
  margin-bottom: 32px;
}
.ccard {
  background: #161b22; border: 1px solid #30363d;
  border-radius: 14px; overflow: hidden;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.ccard:hover { border-color: #58a6ff; box-shadow: 0 0 0 1px #58a6ff22; }
.ccard.up-card { border-left: 3px solid #56d364; }
.ccard.dn-card { border-left: 3px solid #f85149; }

.ccard-header {
  padding: 14px 18px 11px;
  display: flex; align-items: flex-start; justify-content: space-between;
  border-bottom: 1px solid #21262d;
  background: linear-gradient(135deg, #161b22, #1c2333);
}
.ccard-symbol { font-size: 17px; font-weight: 800; color: #e6edf3; }
.ccard-name   { font-size: 11px; color: #8b949e; margin-top: 2px; }
.ccard-sector {
  font-size: 10px; padding: 2px 7px; border-radius: 4px;
  background: #1c2333; color: #58a6ff; margin-top: 4px; display: inline-block;
  border: 1px solid rgba(88,166,255,0.2);
}
.ccard-right { text-align: right; }
.ccard-price { font-size: 19px; font-weight: 700; color: #e6edf3; }
.ccard-metrics { display: flex; gap: 5px; margin-top: 4px; flex-wrap: wrap; justify-content: flex-end; }
.metric-chip {
  font-size: 10px; padding: 2px 7px; border-radius: 4px;
  background: #21262d; color: #8b949e;
  border: 1px solid #30363d;
}

.ccard-body { padding: 14px 18px; }

/* ── Probability big display ── */
.big-prob {
  display: flex; align-items: center; gap: 14px; margin-bottom: 14px;
}
.big-prob-num {
  font-size: 40px; font-weight: 900; line-height: 1;
}
.big-prob-num.up-color { color: #56d364; }
.big-prob-num.dn-color { color: #f85149; }
.big-prob-detail { flex: 1; }
.big-prob-label { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.7px; }
.big-prob-bar-track {
  height: 8px; background: #21262d; border-radius: 99px;
  overflow: hidden; margin-top: 6px;
}

/* ── Month predictions panel ── */
.month-panel {
  display: grid;
  grid-template-columns: 1fr 1px 1fr;
  background: #1c2333;
  border-radius: 10px;
  overflow: hidden;
  margin: 12px 0;
  border: 1px solid #30363d;
}
.month-col {
  padding: 12px 14px;
}
.month-divider {
  background: #30363d;
}
.mpanel-label {
  font-size: 10px; color: #8b949e;
  text-transform: uppercase; letter-spacing: 0.5px;
  margin-bottom: 5px;
  display: flex; align-items: center; gap: 5px; flex-wrap: wrap;
}
.mpanel-prob {
  font-size: 30px; font-weight: 900; line-height: 1.1;
}
.mpanel-dir {
  font-size: 12px; font-weight: 700; margin-bottom: 8px; margin-top: 1px;
}
.mpanel-stats {
  display: flex; flex-direction: column; gap: 3px;
}
.mstat {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 11px;
}
.mstat-label { color: #8b949e; }
.mstat-val { font-weight: 600; color: #e6edf3; }
.lock-badge {
  font-size: 9px; padding: 1px 5px; border-radius: 3px;
  background: rgba(86,211,100,0.15); color: #56d364;
  font-weight: 700; letter-spacing: 0.3px;
}
.align-badge {
  font-size: 9px; padding: 1px 6px; border-radius: 4px; font-weight: 600;
}
.align-yes { background: rgba(86,211,100,0.15); color: #56d364; }
.align-no  { background: rgba(248,81,73,0.15);  color: #f85149; }

/* ── Top summary bar ── */
.top-bar {
  display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 22px;
}
.top-bar-item {
  background: #161b22; border: 1px solid #30363d;
  border-radius: 10px; padding: 13px 18px; flex: 1; min-width: 130px;
  position: relative; overflow: hidden;
}
.top-bar-item::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, #58a6ff, #56d364);
  opacity: 0.4;
}
.top-bar-item .tbi-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.6px; color: #8b949e; }
.top-bar-item .tbi-val   { font-size: 22px; font-weight: 700; margin-top: 4px; color: #e6edf3; }
.top-bar-item .tbi-sub   { font-size: 10px; color: #8b949e; margin-top: 2px; }

/* ── Month prediction tables ── */
.month-pred-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 14px;
  margin-bottom: 28px;
}
.month-pred-table-wrap {
  background: #161b22; border: 1px solid #30363d;
  border-radius: 12px; overflow: hidden;
}
.month-pred-header {
  padding: 11px 15px; border-bottom: 1px solid #21262d;
  display: flex; align-items: center; justify-content: space-between;
  background: #1c2333;
}
.month-pred-header .mph-title {
  font-size: 13px; font-weight: 700; color: #e6edf3;
}
.month-pred-header .mph-sub {
  font-size: 11px; color: #8b949e;
}

/* ── Collapsible detail ── */
.detail-toggle {
  background: none; border: 1px solid #30363d; color: #8b949e;
  font-size: 11px; padding: 5px 12px; border-radius: 6px;
  cursor: pointer; width: 100%; margin-top: 10px;
  transition: border-color 0.15s, color 0.15s;
}
.detail-toggle:hover { border-color: #58a6ff; color: #58a6ff; }
.detail-panel {
  display: none; margin-top: 12px;
  border-top: 1px solid #21262d; padding-top: 12px;
}

/* ── Mini tables inside cards ── */
.mini-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.mini-table th {
  color: #8b949e; text-transform: uppercase; font-size: 10px;
  letter-spacing: 0.5px; padding: 4px 8px; text-align: left;
  border-bottom: 1px solid #21262d;
}
.mini-table td { padding: 5px 8px; border-bottom: 1px solid #1c2333; }
.mini-table .win-cell  { color: #56d364; }
.mini-table .loss-cell { color: #f85149; }
.tick { color: #56d364; font-size: 12px; }
.cross { color: #f85149; font-size: 12px; }

/* ── Scrollable backtest ── */
.bt-scroll { max-height: 200px; overflow-y: auto; }
.bt-scroll::-webkit-scrollbar { width: 4px; }
.bt-scroll::-webkit-scrollbar-track { background: #1c2333; }
.bt-scroll::-webkit-scrollbar-thumb { background: #30363d; border-radius: 4px; }

/* ── Week colors ── */
.week-up   { color: #56d364; }
.week-down { color: #f85149; }

/* ── Disclaimer ── */
.disclaimer {
  margin-top: 40px; padding: 13px 18px;
  background: rgba(210,153,34,0.08); border: 1px solid rgba(210,153,34,0.25);
  border-radius: 8px; font-size: 11px; color: #d29922; line-height: 1.6;
}

/* ── Stock links (jump to detail card) ── */
.stock-link {
  color: #e6edf3; text-decoration: none;
  border-bottom: 1px dashed rgba(88,166,255,0.35);
  transition: color 0.15s, border-color 0.15s;
}
.stock-link:hover { color: #58a6ff; border-bottom-color: #58a6ff; }

/* ── Card highlight on anchor jump ── */
.ccard:target {
  border-color: #58a6ff;
  box-shadow: 0 0 0 2px rgba(88,166,255,0.35);
  animation: card-flash 1.2s ease-out;
}
@keyframes card-flash {
  0%   { box-shadow: 0 0 0 4px rgba(88,166,255,0.55); }
  100% { box-shadow: 0 0 0 1px rgba(88,166,255,0.2); }
}

/* ── Take Trade checkbox ── */
.take-trade-cb {
  width: 17px; height: 17px; cursor: pointer; accent-color: #56d364;
  vertical-align: middle;
}
.trade-taken-label {
  font-size: 10px; color: #56d364; font-weight: 700;
  letter-spacing: 0.4px; display: block; margin-top: 2px;
}

/* ── Track a Stock form ── */
.track-stock-panel {
  background: #161b22; border: 1px solid #30363d;
  border-radius: 14px; padding: 16px 20px; margin-bottom: 28px;
}
.track-stock-panel .ts-label {
  font-size: 11px; color: #8b949e; text-transform: uppercase;
  letter-spacing: 0.6px; margin-bottom: 5px;
}
.track-stock-panel input[type=number],
.track-stock-panel select {
  background: #0d1117; border: 1px solid #30363d; color: #e6edf3;
  border-radius: 7px; padding: 7px 10px; font-size: 12px;
  outline: none; transition: border-color 0.15s;
}
.track-stock-panel input[type=number]:focus,
.track-stock-panel select:focus { border-color: #58a6ff; }
.track-stock-panel input[type=number]{ width: 120px; }
.track-stock-panel select            { width: 140px; }
.track-stock-btn {
  background: rgba(86,211,100,0.12); border: 1px solid rgba(86,211,100,0.4);
  color: #56d364; font-size: 12px; font-weight: 700;
  padding: 7px 18px; border-radius: 7px; cursor: pointer;
  transition: background 0.15s; white-space: nowrap;
}
.track-stock-btn:hover { background: rgba(86,211,100,0.22); }
.manual-badge {
  font-size: 9px; padding: 1px 5px; border-radius: 3px;
  background: rgba(88,166,255,0.15); color: #58a6ff;
  font-weight: 700; letter-spacing: 0.3px; margin-left: 4px;
}

/* ── Stock search autocomplete ── */
.stock-search-wrap {
  position: relative; display: inline-block;
}
.stock-search-input {
  background: #0d1117; border: 1px solid #30363d; color: #e6edf3;
  border-radius: 7px; padding: 7px 32px 7px 10px; font-size: 12px;
  outline: none; transition: border-color 0.15s; width: 260px;
}
.stock-search-input:focus { border-color: #58a6ff; }
.stock-search-input.has-selection {
  border-color: rgba(86,211,100,0.5); color: #56d364; font-weight: 600;
}
.stock-search-clear {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  background: none; border: none; color: #8b949e; font-size: 14px;
  cursor: pointer; padding: 0; display: none; line-height: 1;
}
.stock-search-clear:hover { color: #f85149; }
.stock-search-dropdown {
  position: absolute; top: calc(100% + 4px); left: 0; right: 0;
  background: #1c2333; border: 1px solid #30363d; border-radius: 9px;
  overflow: hidden; z-index: 999; display: none;
  box-shadow: 0 8px 24px rgba(0,0,0,0.5);
  max-height: 320px; overflow-y: auto;
}
.stock-search-dropdown::-webkit-scrollbar { width: 4px; }
.stock-search-dropdown::-webkit-scrollbar-track { background: #1c2333; }
.stock-search-dropdown::-webkit-scrollbar-thumb { background: #30363d; border-radius: 4px; }
.ssd-item {
  padding: 9px 13px; cursor: pointer; border-bottom: 1px solid #21262d;
  display: flex; align-items: center; justify-content: space-between;
  transition: background 0.1s;
}
.ssd-item:last-child { border-bottom: none; }
.ssd-item:hover, .ssd-item.active { background: #262d3a; }
.ssd-left { display: flex; flex-direction: column; gap: 2px; }
.ssd-sym  { font-size: 13px; font-weight: 800; color: #e6edf3; }
.ssd-name { font-size: 10px; color: #8b949e; }
.ssd-right { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.ssd-cmp  { font-size: 12px; font-weight: 600; color: #e6edf3; }
.ssd-prob { font-size: 10px; font-weight: 700; }
.ssd-sector { font-size: 9px; color: #58a6ff; background: rgba(88,166,255,0.1);
  padding: 1px 5px; border-radius: 3px; }
.ssd-empty { padding: 14px; text-align: center; color: #8b949e; font-size: 12px; }

/* ── Active Trades panel ── */
.active-trades-panel {
  background: #0f2318; border: 1px solid rgba(86,211,100,0.35);
  border-radius: 14px; overflow: hidden; margin-bottom: 28px;
}
.active-trades-header {
  padding: 14px 20px; background: rgba(86,211,100,0.1);
  border-bottom: 1px solid rgba(86,211,100,0.25);
  display: flex; align-items: center; justify-content: space-between;
}
.active-trades-header .ath-title {
  font-size: 14px; font-weight: 800; color: #56d364;
  display: flex; align-items: center; gap: 8px;
}
.active-trades-header .ath-sub {
  font-size: 11px; color: #7ee787; opacity: 0.8;
}
.at-badge {
  background: #56d364; color: #0d1117; font-size: 11px;
  font-weight: 800; padding: 2px 8px; border-radius: 99px;
}
.complete-btn {
  background: rgba(248,81,73,0.12); border: 1px solid rgba(248,81,73,0.35);
  color: #f85149; font-size: 10px; padding: 4px 10px;
  border-radius: 6px; cursor: pointer; font-weight: 700;
  transition: background 0.15s;
}
.complete-btn:hover { background: rgba(248,81,73,0.25); }
.at-trade-taken-at {
  font-size: 10px; color: #7ee787; opacity: 0.7;
}

/* ── Change alert banner ── */
.change-alert-banner {
  border-radius: 12px; padding: 14px 20px; margin-bottom: 22px;
  border: 1px solid rgba(210,153,34,0.5);
  background: rgba(210,153,34,0.08);
}
.change-alert-banner .cab-title {
  font-size: 13px; font-weight: 800; color: #d29922;
  display: flex; align-items: center; gap: 8px; margin-bottom: 10px;
}
.alert-item {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 8px 0; border-top: 1px solid rgba(210,153,34,0.2);
  font-size: 12px;
}
.alert-item .ai-sym {
  font-weight: 800; font-size: 13px; color: #e6edf3; min-width: 90px;
}
.alert-flip  { color: #f85149; font-weight: 700; }
.alert-up    { color: #56d364; font-weight: 700; }
.alert-down  { color: #f85149; font-weight: 700; }
.alert-neutral { color: #d29922; }

/* ── Tracked stock card (not in top-N) ── */
.ccard.tracked-card {
  border-left-color: #58a6ff !important;
  opacity: 0.92;
}
.tracked-badge {
  font-size: 9px; padding: 2px 6px; border-radius: 4px;
  background: rgba(88,166,255,0.15); color: #58a6ff;
  border: 1px solid rgba(88,166,255,0.3); font-weight: 700;
  letter-spacing: 0.3px; display: inline-block; margin-top: 3px;
}

/* ── Market Overview (Nifty / Crude / NatGas) ── */
.mkt-overview { margin-bottom: 28px; }
.mkt-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 16px; }
@media (max-width: 900px) { .mkt-grid { grid-template-columns: 1fr; } }
.mkt-card { background: #161b22; border: 1px solid #30363d; border-radius: 14px; overflow: hidden; }
.mkt-card-head {
  padding: 12px 16px 10px; border-bottom: 1px solid #21262d;
  display: flex; justify-content: space-between; align-items: flex-start;
}
.mkt-card-title { font-size: 13px; font-weight: 800; color: #e6edf3; }
.mkt-card-note  { font-size: 10px; color: #8b949e; margin-top: 2px; }
.mkt-cmp        { font-size: 20px; font-weight: 700; color: #58a6ff; }
.mkt-expiry-wrap { text-align: right; }
.mkt-expiry-date { font-size: 11px; font-weight: 700; color: #e6edf3; }
.mkt-expiry-dte  { font-size: 10px; margin-top: 2px; font-weight: 700; border-radius: 6px; padding: 2px 7px; display: inline-block; }
.mkt-dte-ok   { background: rgba(86,211,100,0.12); color: #56d364; }
.mkt-dte-warn { background: rgba(210,153,34,0.12); color: #d29922; }
.mkt-dte-hot  { background: rgba(248,81,73,0.12);  color: #f85149; }
.mkt-levels { display: flex; gap: 8px; padding: 8px 16px 4px; }
.mkt-lvl { flex: 1; text-align: center; background: #0d1117; border-radius: 8px; padding: 6px 4px; }
.mkt-lvl-label { font-size: 9px; color: #8b949e; font-weight: 600; letter-spacing: 0.5px; }
.mkt-lvl-val   { font-size: 12px; font-weight: 700; margin-top: 2px; }
.mkt-tbl { width: 100%; border-collapse: collapse; font-size: 11px; margin: 4px 0 0; }
.mkt-tbl th { padding: 4px 8px; color: #8b949e; font-weight: 600; text-align: right; border-bottom: 1px solid #21262d; }
.mkt-tbl th:first-child { text-align: left; }
.mkt-tbl td { padding: 4px 8px; text-align: right; border-bottom: 1px solid rgba(48,54,61,0.5); }
.mkt-tbl td:first-child { text-align: left; color: #8b949e; }
.mkt-bull { color: #56d364; }
.mkt-bear { color: #f85149; }
.mkt-hi   { color: #56d364; font-weight: 700; }
.mkt-lo   { color: #f85149; font-weight: 700; }

/* ── Print ── */
@media print {
  body { background: white; color: black; }
  .ccard { break-inside: avoid; }
  .detail-toggle { display: none; }
  .detail-panel { display: block !important; }
}
"""

# ── JS ────────────────────────────────────────────────────────────────────────

JS = """
function toggleDetail(id) {
  var el = document.getElementById(id);
  var btn = document.getElementById('btn_' + id);
  if (el.style.display === 'block') {
    el.style.display = 'none';
    btn.textContent = '\\u25be Show Backtest & Weekly Data';
  } else {
    el.style.display = 'block';
    btn.textContent = '\\u25b4 Hide Details';
  }
}

// ── Active Trades (localStorage) ──────────────────────────────────────────────
var STORAGE_KEY = 'screener_active_trades';

function getActiveTrades() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
  catch(e) { return {}; }
}

function saveActiveTrades(trades) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(trades));
  // Sync to server so all devices stay in sync
  fetch('/api/ui-trades', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(trades)
  }).catch(function() {});  // fire-and-forget; offline = no-op
}

function takeTrade(symbol, cb) {
  var trades = getActiveTrades();
  if (cb.checked) {
    var data = window.SCREENER_DATA && window.SCREENER_DATA[symbol];
    trades[symbol] = {
      symbol:      symbol,
      name:        data ? data.name      : symbol,
      sector:      data ? data.sector    : '',
      cmp:         data ? data.cmp       : 0,
      direction:   data ? data.direction : '',
      probability: data ? data.probability : 0,
      takenAt: new Date().toLocaleString('en-IN', {day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'})
    };
  } else {
    delete trades[symbol];
  }
  saveActiveTrades(trades);
  renderActiveTrades();
}

// ── Stock search autocomplete ──────────────────────────────────────────────────
var _searchSelected = null;   // { symbol, name, cmp, direction, probability, sector }
var _searchActiveIdx = -1;

function initStockSearch() {
  var inp  = document.getElementById('stock-search-input');
  var drop = document.getElementById('stock-search-dropdown');
  var clr  = document.getElementById('stock-search-clear');
  if (!inp) return;

  inp.addEventListener('input', function() {
    _searchSelected = null;
    inp.classList.remove('has-selection');
    clr.style.display = inp.value ? 'block' : 'none';
    var q = inp.value.trim();
    if (!q) { drop.style.display = 'none'; return; }
    renderSearchDropdown(q);
  });

  inp.addEventListener('keydown', function(e) {
    var items = drop.querySelectorAll('.ssd-item');
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _searchActiveIdx = Math.min(_searchActiveIdx + 1, items.length - 1);
      updateSearchActive(items);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      _searchActiveIdx = Math.max(_searchActiveIdx - 1, 0);
      updateSearchActive(items);
    } else if (e.key === 'Enter') {
      if (_searchActiveIdx >= 0 && items[_searchActiveIdx]) {
        items[_searchActiveIdx].click();
      } else if (items.length === 1) {
        items[0].click();
      } else {
        addCustomTrade();
      }
    } else if (e.key === 'Escape') {
      drop.style.display = 'none';
    }
  });

  inp.addEventListener('blur', function() {
    setTimeout(function() { drop.style.display = 'none'; }, 180);
  });

  clr.addEventListener('click', function() {
    inp.value = '';
    clr.style.display = 'none';
    inp.classList.remove('has-selection');
    _searchSelected = null;
    drop.style.display = 'none';
    inp.focus();
  });
}

function updateSearchActive(items) {
  items.forEach(function(el, i) {
    el.classList.toggle('active', i === _searchActiveIdx);
  });
}

function renderSearchDropdown(q) {
  var drop = document.getElementById('stock-search-dropdown');
  var results = searchStocks(q);
  _searchActiveIdx = -1;

  if (results.length === 0) {
    drop.innerHTML = '<div class="ssd-empty">No stocks found for "' + q + '"</div>';
    drop.style.display = 'block';
    return;
  }

  drop.innerHTML = results.map(function(r) {
    var dirColor = r.direction === 'UP' ? '#56d364' : '#f85149';
    var dirArrow = r.direction === 'UP' ? '\\u25b2' : '\\u25bc';
    var cmpStr   = r.cmp > 0 ? '\\u20b9' + r.cmp.toLocaleString('en-IN', {minimumFractionDigits:2,maximumFractionDigits:2}) : '';
    var probStr  = r.probability > 0 ? '<span class="ssd-prob" style="color:' + dirColor + '">' + dirArrow + ' ' + r.probability + '%</span>' : '';
    return '<div class="ssd-item" data-sym="' + r.symbol + '">'
      + '<div class="ssd-left">'
      + '<span class="ssd-sym">' + r.symbol + '</span>'
      + '<span class="ssd-name">' + (r.name || '') + '</span>'
      + '</div>'
      + '<div class="ssd-right">'
      + (cmpStr ? '<span class="ssd-cmp">' + cmpStr + '</span>' : '')
      + probStr
      + '<span class="ssd-sector">' + (r.sector || '') + '</span>'
      + '</div>'
      + '</div>';
  }).join('');

  drop.querySelectorAll('.ssd-item').forEach(function(el) {
    el.addEventListener('mousedown', function(e) {
      e.preventDefault();
      var sym = el.getAttribute('data-sym');
      selectStock(sym);
    });
  });

  drop.style.display = 'block';
}

function selectStock(sym) {
  var data = window.SCREENER_DATA && window.SCREENER_DATA[sym];
  _searchSelected = data ? {
    symbol:      sym,
    name:        data.name,
    sector:      data.sector,
    cmp:         data.cmp,
    direction:   data.direction,
    probability: data.probability,
  } : { symbol: sym, name: sym, sector: '', cmp: 0, direction: '', probability: 0 };

  var inp  = document.getElementById('stock-search-input');
  var drop = document.getElementById('stock-search-dropdown');
  var clr  = document.getElementById('stock-search-clear');
  inp.value = sym + (data ? '  —  ' + data.name : '');
  inp.classList.add('has-selection');
  clr.style.display = 'block';
  drop.style.display = 'none';

  // Pre-fill direction from screener data
  if (data && data.direction) {
    document.getElementById('custom-direction').value = data.direction;
  }

  // Auto-fill CMP if no price entered
  var prxEl = document.getElementById('custom-price');
  if (!prxEl.value && data && data.cmp > 0) {
    prxEl.value = data.cmp;
  }
}

function searchStocks(q) {
  var ql  = q.toLowerCase();
  var data = window.SCREENER_DATA || {};
  var results = [];
  Object.keys(data).forEach(function(sym) {
    var d = data[sym];
    var nameMatch = d.name && d.name.toLowerCase().includes(ql);
    var symMatch  = sym.toLowerCase().includes(ql);
    if (symMatch || nameMatch) {
      results.push({
        symbol:      sym,
        name:        d.name || sym,
        sector:      d.sector || '',
        cmp:         d.cmp || 0,
        direction:   d.direction || '',
        probability: d.probability || 0,
        _symExact:   sym.toLowerCase() === ql,
        _symStarts:  sym.toLowerCase().startsWith(ql),
      });
    }
  });
  results.sort(function(a, b) {
    if (a._symExact  && !b._symExact)  return -1;
    if (!a._symExact && b._symExact)   return 1;
    if (a._symStarts && !b._symStarts) return -1;
    if (!a._symStarts && b._symStarts) return 1;
    return a.symbol.localeCompare(b.symbol);
  });
  return results.slice(0, 10);
}

function addCustomTrade() {
  var prxEl  = document.getElementById('custom-price');
  var dirEl  = document.getElementById('custom-direction');
  var errEl  = document.getElementById('custom-error');

  var prc = parseFloat(prxEl.value) || 0;
  var dir = dirEl.value;

  if (!_searchSelected) { errEl.style.color='#f85149'; errEl.textContent = 'Search and select a stock first.'; return; }
  errEl.textContent = '';

  var sym  = _searchSelected.symbol;
  var data = window.SCREENER_DATA && window.SCREENER_DATA[sym];
  var trades = getActiveTrades();
  trades[sym] = {
    symbol:      sym,
    name:        _searchSelected.name   || (data ? data.name   : sym),
    sector:      _searchSelected.sector || (data ? data.sector : 'Manual'),
    cmp:         prc > 0 ? prc : (_searchSelected.cmp || (data ? data.cmp : 0)),
    direction:   dir,
    probability: _searchSelected.probability || (data ? data.probability : 0),
    manual:      true,
    takenAt: new Date().toLocaleString('en-IN', {day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'})
  };
  saveActiveTrades(trades);
  renderActiveTrades();

  // Sync checkbox if stock is in top picks
  var cb  = document.getElementById('cb_' + sym);
  var lbl = document.getElementById('tl_' + sym);
  if (cb)  cb.checked = true;
  if (lbl) lbl.style.display = 'block';

  errEl.style.color = '#56d364';
  errEl.textContent = sym + ' added to Active Trades!';
  setTimeout(function() { errEl.textContent = ''; }, 3000);

  // Reset form
  var inp = document.getElementById('stock-search-input');
  var clr = document.getElementById('stock-search-clear');
  if (inp) { inp.value = ''; inp.classList.remove('has-selection'); }
  if (clr) clr.style.display = 'none';
  _searchSelected = null;
  prxEl.value = '';
  dirEl.value = 'UP';
}

function completeTrade(symbol) {
  var trades = getActiveTrades();
  delete trades[symbol];
  saveActiveTrades(trades);
  var cb = document.getElementById('cb_' + symbol);
  if (cb) cb.checked = false;
  var lbl = document.getElementById('tl_' + symbol);
  if (lbl) lbl.style.display = 'none';
  renderActiveTrades();
}

function goToCard(sym) {
  var card = document.getElementById('card_' + sym);
  if (!card) return;
  card.scrollIntoView({behavior: 'smooth', block: 'start'});
  // Briefly highlight
  card.style.borderColor = '#58a6ff';
  card.style.boxShadow   = '0 0 0 3px rgba(88,166,255,0.4)';
  setTimeout(function() {
    card.style.borderColor = '';
    card.style.boxShadow   = '';
  }, 1800);
}

function renderActiveTrades() {
  var trades = getActiveTrades();
  var panel  = document.getElementById('active-trades-panel');
  var badge  = document.getElementById('active-trades-badge');
  var tbody  = document.getElementById('active-trades-tbody');
  var keys   = Object.keys(trades);

  badge.textContent = keys.length;

  if (keys.length === 0) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';

  var rows = '';
  keys.forEach(function(sym) {
    var t = trades[sym];
    var dirColor = t.direction === 'UP' ? '#56d364' : (t.direction === 'DOWN' ? '#f85149' : '#8b949e');
    var dirArrow = t.direction === 'UP' ? '\\u25b2' : (t.direction === 'DOWN' ? '\\u25bc' : '—');
    var latest   = window.SCREENER_DATA && window.SCREENER_DATA[sym];
    var curCmp   = latest ? latest.cmp : 0;
    var cmpDisplay = curCmp > 0
      ? ('\\u20b9' + curCmp.toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2}))
      : (t.cmp > 0
          ? ('\\u20b9' + t.cmp.toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2}) + ' <span style="font-size:9px;color:#8b949e">(entry price)</span>')
          : '<span style="color:#8b949e">—</span>');
    var pnlHtml = '';
    var entryPrc = t.cmp || 0;  // entry CMP stored when trade taken
    // For Python-tracked trades, use meta entry_price if available
    var meta = window.ACTIVE_TRADES_META && window.ACTIVE_TRADES_META[sym];
    if (meta && meta.entry_price > 0) entryPrc = meta.entry_price;
    if (curCmp > 0 && entryPrc > 0) {
      var pnl = ((curCmp - entryPrc) / entryPrc) * 100;
      var pnlColor = pnl >= 0 ? '#56d364' : '#f85149';
      pnlHtml = '<div style="font-size:10px;color:' + pnlColor + ';font-weight:700">' + (pnl>=0?'+':'') + pnl.toFixed(1) + '% since entry</div>';
    }
    // Show current probability from live scan
    var liveProbHtml = '';
    if (latest && latest.probability > 0) {
      var pColor = latest.direction === 'UP' ? '#56d364' : '#f85149';
      liveProbHtml = '<strong style="color:' + pColor + '">' + latest.probability + '%</strong>';
      // Flag change vs entry
      var entryProb = (meta && meta.entry_probability) || t.probability || 0;
      if (entryProb > 0 && Math.abs(latest.probability - entryProb) >= 5) {
        var pdiff = latest.probability - entryProb;
        var pc = pdiff > 0 ? '#56d364' : '#f85149';
        liveProbHtml += '<span style="font-size:9px;color:' + pc + ';margin-left:3px">('
          + (pdiff>0?'+':'') + pdiff.toFixed(1) + '%)</span>';
      }
    } else if (t.probability > 0) {
      // Not in current scan — show probability stored at time of entry
      var pColor = t.direction === 'UP' ? '#56d364' : '#f85149';
      liveProbHtml = '<strong style="color:' + pColor + '">' + t.probability + '%</strong>'
        + '<div style="font-size:9px;color:#8b949e">at entry</div>';
    } else {
      liveProbHtml = '<span style="color:#8b949e;font-size:10px">—</span>';
    }
    var manualTag = t.manual ? '<span class="manual-badge">MANUAL</span>' : (t.fromPython ? '' : '');
    // Card link (if card exists in this report)
    var cardEl = document.getElementById('card_' + sym);
    var symHtml = cardEl
      ? '<a href="#card_' + sym + '" class="stock-link" style="font-size:13px;font-weight:700">' + sym + '</a>'
      : '<strong style="font-size:13px">' + sym + '</strong>';
    rows += '<tr style="border-top:1px solid rgba(86,211,100,0.15)">'
      + '<td>' + symHtml + manualTag
      + '<div style="font-size:10px;color:#8b949e">' + t.name + '</div></td>'
      + '<td style="color:#8b949e;font-size:11px">' + t.sector + '</td>'
      + '<td>' + cmpDisplay + pnlHtml
      + (t.cmp > 0 ? '<div style="font-size:9px;color:#8b949e">Entry: \\u20b9' + t.cmp.toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2}) + '</div>' : '')
      + '</td>'
      + '<td><span style="color:' + dirColor + ';font-weight:700">' + dirArrow + ' ' + (t.direction||'—') + '</span></td>'
      + '<td>' + liveProbHtml + '</td>'
      + '<td><span class="at-trade-taken-at">' + t.takenAt + '</span></td>'
      + '<td><button class="complete-btn" onclick="completeTrade(\\'' + sym + '\\')">\\u2713 Mark Complete</button></td>'
      + '</tr>';
  });
  tbody.innerHTML = rows;
}

// ── Change alert detection ─────────────────────────────────────────────────────
function detectAndShowAlerts() {
  var meta    = window.ACTIVE_TRADES_META || {};
  var data    = window.SCREENER_DATA || {};
  var lsTrades = getActiveTrades();
  var alerts  = [];

  // Check all symbols that are either in Python meta or localStorage
  var allSyms = {};
  Object.keys(meta).forEach(function(s) { allSyms[s] = true; });
  Object.keys(lsTrades).forEach(function(s) { allSyms[s] = true; });

  Object.keys(allSyms).forEach(function(sym) {
    var current = data[sym];
    if (!current) return;  // not in this scan's data

    var entryDir  = null;
    var entryProb = 0;
    var entryName = sym;

    // Prefer Python meta (more reliable — set at time of --track)
    if (meta[sym]) {
      entryDir  = meta[sym].entry_direction  || null;
      entryProb = meta[sym].entry_probability || 0;
      entryName = meta[sym].name || sym;
    } else if (lsTrades[sym]) {
      entryDir  = lsTrades[sym].direction   || null;
      entryProb = lsTrades[sym].probability || 0;
      entryName = lsTrades[sym].name || sym;
    }

    // Direction flip
    if (entryDir && entryDir !== current.direction) {
      alerts.push({
        type: 'flip',
        sym:  sym,
        name: entryName,
        from: entryDir,
        to:   current.direction,
        prob: current.probability,
      });
    }

    // Probability swing ≥5%
    if (entryProb > 0) {
      var diff = current.probability - entryProb;
      if (Math.abs(diff) >= 5) {
        alerts.push({
          type: 'prob',
          sym:  sym,
          name: entryName,
          from: entryProb,
          to:   current.probability,
          diff: diff,
          dir:  current.direction,
        });
      }
    }
  });

  if (alerts.length === 0) return;

  var rows = alerts.map(function(a) {
    if (a.type === 'flip') {
      var fromCol = a.from === 'UP' ? 'alert-up' : 'alert-down';
      var toCol   = a.to   === 'UP' ? 'alert-up' : 'alert-down';
      var fromArrow = a.from === 'UP' ? '\\u25b2' : '\\u25bc';
      var toArrow   = a.to   === 'UP' ? '\\u25b2' : '\\u25bc';
      return '<div class="alert-item">'
        + '<span class="ai-sym">' + a.sym + '</span>'
        + '<span>Direction flipped &nbsp;<span class="' + fromCol + '">' + fromArrow + ' ' + a.from + '</span>'
        + ' &rarr; <span class="' + toCol + '">' + toArrow + ' ' + a.to + '</span>'
        + ' &nbsp;<span style="color:#8b949e">( now ' + a.prob + '% probability )</span>'
        + '</span></div>';
    } else {
      var sign    = a.diff >= 0 ? '+' : '';
      var diffCol = a.diff >= 0 ? '#56d364' : '#f85149';
      return '<div class="alert-item">'
        + '<span class="ai-sym">' + a.sym + '</span>'
        + '<span class="alert-neutral">Probability changed &nbsp;'
        + '<strong>' + a.from.toFixed(1) + '%</strong> &rarr; '
        + '<strong style="color:' + diffCol + '">' + a.to.toFixed(1) + '%</strong>'
        + ' &nbsp;<span style="color:' + diffCol + ';font-weight:700">(' + sign + a.diff.toFixed(1) + '%)</span>'
        + '</span></div>';
    }
  }).join('');

  var banner = document.createElement('div');
  banner.className = 'change-alert-banner';
  banner.innerHTML = '<div class="cab-title">\\u26a0\\ufe0f Active Trade Alerts (' + alerts.length + ')'
    + '<span style="font-size:11px;font-weight:400;color:#d29922;margin-left:4px">— changes since your entry</span></div>'
    + rows;

  var mainEl = document.querySelector('.main');
  if (mainEl) mainEl.insertBefore(banner, mainEl.firstChild);
}

// ── Sync Python-tracked trades into localStorage ───────────────────────────────
function syncPythonTrades() {
  var meta   = window.ACTIVE_TRADES_META || {};
  var data   = window.SCREENER_DATA || {};
  var trades = getActiveTrades();
  var changed = false;

  Object.keys(meta).forEach(function(sym) {
    if (!trades[sym]) {
      var m       = meta[sym];
      var current = data[sym] || {};
      trades[sym] = {
        symbol:      sym,
        name:        m.name || current.name || sym,
        sector:      current.sector || 'Tracked',
        cmp:         m.entry_price  || current.cmp || 0,
        direction:   m.entry_direction || current.direction || '',
        probability: current.probability || m.entry_probability || 0,
        takenAt:     m.added_at || '',
        fromPython:  true,
      };
      changed = true;
    }
  });
  if (changed) saveActiveTrades(trades);
}

function initPage() {
  syncPythonTrades();

  var trades = getActiveTrades();
  Object.keys(trades).forEach(function(sym) {
    var cb  = document.getElementById('cb_' + sym);
    var lbl = document.getElementById('tl_' + sym);
    if (cb)  cb.checked = true;
    if (lbl) lbl.style.display = 'block';
  });
  renderActiveTrades();
  detectAndShowAlerts();
  initStockSearch();

  document.getElementById('custom-price').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') addCustomTrade();
  });
}

// Init on page load — bidirectional sync with server for cross-device consistency
window.addEventListener('DOMContentLoaded', function() {
  fetch('/api/ui-trades')
    .then(function(r) { return r.ok ? r.json() : {}; })
    .catch(function() { return {}; })
    .then(function(serverTrades) {
      var local  = getActiveTrades();
      // Merge: server wins on conflict (newer device's save wins over stale local)
      var merged = Object.assign({}, local, serverTrades);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
      // Always push merged state back so whichever device had more data wins
      if (Object.keys(merged).length > 0) {
        fetch('/api/ui-trades', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(merged)
        }).catch(function() {});
      }
      initPage();
    });
});
"""


# ── Month prediction block inside each card ───────────────────────────────────

def _month_pred_block(c: dict) -> str:
    cm = c.get("cur_month", {})
    nm = c.get("next_month", {})
    if not cm or not nm:
        return ""

    # Current month values
    cm_dir    = cm.get("eom_direction", "UP")
    cm_prob   = cm.get("eom_prob", 50)
    cm_color  = "#56d364" if cm_dir == "UP" else "#f85149"
    cm_mtd    = cm.get("mtd_return", 0)
    cm_days   = cm.get("trading_days_left", 5)
    cm_open   = cm.get("month_open", 0)
    cm_up_t   = cm.get("eom_up_target", 0)
    cm_dn_t   = cm.get("eom_down_target", 0)
    cm_locked = cm.get("locked_in", False)
    cm_1sd    = cm.get("remaining_move_1sd", 0)

    # Next month values
    nm_dir    = nm.get("direction", "UP")
    nm_prob   = nm.get("probability", 50)
    nm_color  = "#56d364" if nm_dir == "UP" else "#f85149"
    nm_rtgt   = nm.get("base_target", c["cmp"])
    nm_rhi    = nm.get("range_high", c["cmp"])
    nm_rlo    = nm.get("range_low", c["cmp"])
    nm_align  = nm.get("aligned", False)

    locked_html = ' <span class="lock-badge">LOCKED</span>' if cm_locked else ""
    mtd_color   = "#56d364" if cm_mtd >= 0 else "#f85149"
    mtd_sign    = "+" if cm_mtd >= 0 else ""

    return f"""
    <div class="month-panel">
      <div class="month-col">
        <div class="mpanel-label">📅 {cm.get("label","Current Month")}{locked_html}</div>
        <div class="mpanel-prob" style="color:{cm_color}">{cm_prob:.0f}%</div>
        <div class="mpanel-dir" style="color:{cm_color}">{'▲' if cm_dir == 'UP' else '▼'} Close {cm_dir}</div>
        <div class="mpanel-stats">
          <div class="mstat">
            <span class="mstat-label">MTD so far</span>
            <span class="mstat-val" style="color:{mtd_color}">{mtd_sign}{cm_mtd:.1f}%</span>
          </div>
          <div class="mstat">
            <span class="mstat-label">Days remaining</span>
            <span class="mstat-val">{cm_days} trading days</span>
          </div>
          <div class="mstat">
            <span class="mstat-label">Month open</span>
            <span class="mstat-val">₹{cm_open:,.2f}</span>
          </div>
          <div class="mstat">
            <span class="mstat-label">Expected move</span>
            <span class="mstat-val">±{cm_1sd:.1f}%</span>
          </div>
          <div class="mstat">
            <span class="mstat-label">EOM targets</span>
            <span class="mstat-val">
              <span style="color:#56d364">▲₹{cm_up_t:,.2f}</span>
              &nbsp;/&nbsp;
              <span style="color:#f85149">▼₹{cm_dn_t:,.2f}</span>
            </span>
          </div>
        </div>
      </div>
      <div class="month-divider"></div>
      <div class="month-col">
        <div class="mpanel-label">🔮 {nm.get("label","Next Month")} &nbsp;<span class="align-badge {'align-yes' if nm_align else 'align-no'}">{'✓ Aligned' if nm_align else '⚡ Conflict'}</span></div>
        <div class="mpanel-prob" style="color:{nm_color}">{nm_prob:.0f}%</div>
        <div class="mpanel-dir" style="color:{nm_color}">{'▲' if nm_dir == 'UP' else '▼'} {nm_dir}</div>
        <div class="mpanel-stats">
          <div class="mstat">
            <span class="mstat-label">Expected range</span>
            <span class="mstat-val">
              <span style="color:#56d364">₹{nm_rhi:,.2f}</span>
              &nbsp;–&nbsp;
              <span style="color:#f85149">₹{nm_rlo:,.2f}</span>
            </span>
          </div>
          <div class="mstat">
            <span class="mstat-label">Price target</span>
            <span class="mstat-val" style="color:{nm_color}">₹{nm_rtgt:,.2f}</span>
          </div>
          <div class="mstat">
            <span class="mstat-label">Momentum</span>
            <span class="mstat-val" style="color:{'#56d364' if nm.get('momentum_dir')=='UP' else '#f85149'}">
              {'▲' if nm.get('momentum_dir')=='UP' else '▼'} {nm.get('momentum_dir','')}
            </span>
          </div>
          <div class="mstat">
            <span class="mstat-label">Monthly vol</span>
            <span class="mstat-val">±{c['monthly_vol']:.1f}%</span>
          </div>
        </div>
      </div>
    </div>"""


# ── Card builder ──────────────────────────────────────────────────────────────

def _build_card(c: dict, idx: int, tracked: bool = False) -> str:
    direction = c["direction"]
    prob      = c["probability"]
    bd        = c["breakdown"]
    up        = direction == "UP"

    dir_cls    = ("up-card" if up else "dn-card") + (" tracked-card" if tracked else "")
    prob_color = "#56d364" if up else "#f85149"
    prob_cls   = "up-color" if up else "dn-color"

    rsi_color = "#f85149" if c["rsi"] > 70 else ("#56d364" if c["rsi"] < 35 else "#8b949e")
    chips_html = (
        f'<span class="metric-chip" style="color:{rsi_color}">RSI {c["rsi"]}</span>'
        f'<span class="metric-chip">Vol {c["vol_ratio"]}x</span>'
        f'<span class="metric-chip">ATR {c["atr_pct"]}%</span>'
        f'<span class="metric-chip">{c["daily_val_cr"]:.0f} Cr/d</span>'
    )

    # Monthly backtest table
    bt_rows = ""
    for bt in c["backtest"][-12:]:
        win_cls = "tick" if bt["win"] else "cross"
        win_sym = "✓" if bt["win"] else "✗"
        ret_cls = "pos" if bt["ret"] >= 0 else "neg"
        sign    = "+" if bt["ret"] >= 0 else ""
        bt_rows += f"""<tr>
          <td>{bt['label']}</td>
          <td class="{ret_cls}">{sign}{bt['ret']:.1f}%</td>
          <td>{bt['direction']}</td>
          <td>{bt['predicted']}</td>
          <td class="{win_cls}">{win_sym}</td>
        </tr>"""

    # Weekly table
    wk_rows = ""
    for wk in c["weekly_hist"]:
        pct = wk["pct"]
        pct_cls  = "week-up" if pct >= 0 else "week-down"
        sign = "+" if pct >= 0 else ""
        wk_rows += f"""<tr>
          <td>{wk['label']}</td>
          <td>₹{wk['mon_open']:,.2f}</td>
          <td>₹{wk['fri_close']:,.2f}</td>
          <td>₹{wk['high']:,.2f}</td>
          <td>₹{wk['low']:,.2f}</td>
          <td class="{pct_cls}">{sign}{pct:.2f}%</td>
        </tr>"""

    detail_id = f"detail_{idx}"

    tracked_badge = '<div class="tracked-badge">📌 TRACKED</div>' if tracked else ""
    return f"""
<div class="ccard {dir_cls}" id="card_{c['symbol']}">
  <div class="ccard-header">
    <div>
      <div class="ccard-symbol">{c['symbol']}</div>
      <div class="ccard-name">{c['name']}</div>
      <div class="ccard-sector">{c['sector']}</div>
      {tracked_badge}
    </div>
    <div class="ccard-right">
      <div class="ccard-price">₹{c['cmp']:,.2f}</div>
      <div class="ccard-metrics">{chips_html}</div>
      <div style="margin-top:7px">{_dir_badge(direction)}</div>
    </div>
  </div>
  <div class="ccard-body">

    <!-- Probability display -->
    <div class="big-prob">
      <div class="big-prob-num {prob_cls}">{prob:.0f}%</div>
      <div class="big-prob-detail">
        <div class="big-prob-label">Probability of moving {direction} by EOM</div>
        <div class="big-prob-bar-track">
          <div style="width:{prob}%;height:8px;background:{prob_color};border-radius:99px"></div>
        </div>
        <div style="font-size:10px;color:#8b949e;margin-top:4px">
          Tech score: {c['tech_score']:.0f}/100 &nbsp;·&nbsp; 52W: {c['vs_52h']:.1f}% from high
        </div>
      </div>
      {_score_gauge(c['tech_score'])}
    </div>

    <!-- Month predictions -->
    {_month_pred_block(c)}

    <!-- Toggle for details -->
    <button class="detail-toggle" id="btn_{detail_id}" onclick="toggleDetail('{detail_id}')">
      ▾ Show Backtest &amp; Weekly Data
    </button>

    <div class="detail-panel" id="{detail_id}">

      <!-- Monthly backtest -->
      <div style="font-size:11px;font-weight:700;color:#8b949e;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">
        Monthly Backtest — Predicted: {direction}
      </div>
      <div class="bt-scroll">
        <table class="mini-table">
          <thead><tr>
            <th>Month</th><th>Return</th><th>Actual</th><th>Predicted</th><th>Result</th>
          </tr></thead>
          <tbody>{bt_rows}</tbody>
        </table>
      </div>

      <!-- Weekly data -->
      <div style="font-size:11px;font-weight:700;color:#8b949e;text-transform:uppercase;letter-spacing:0.5px;margin:12px 0 6px">
        Weekly Price Data (Mon Open → Fri Close)
      </div>
      <table class="mini-table">
        <thead><tr>
          <th>Week</th><th>Mon Open</th><th>Fri Close</th><th>High</th><th>Low</th><th>Move %</th>
        </tr></thead>
        <tbody>{wk_rows}</tbody>
      </table>

    </div>
  </div>
</div>"""


# ── Summary table row ─────────────────────────────────────────────────────────

def _summary_row(c: dict, rank: int) -> str:
    prob      = c["probability"]
    prob_color = "#56d364" if c["direction"] == "UP" else "#f85149"

    cm = c.get("cur_month", {})
    nm = c.get("next_month", {})
    cm_mtd   = cm.get("mtd_return", 0)
    cm_prob  = cm.get("eom_prob", 50)
    nm_dir   = nm.get("direction", "UP")
    nm_prob  = nm.get("probability", 50)

    mtd_color = "#56d364" if cm_mtd >= 0 else "#f85149"
    nm_color  = "#56d364" if nm_dir == "UP" else "#f85149"

    sym = c['symbol']
    return f"""<tr>
      <td style="font-size:13px;font-weight:700;color:#8b949e">#{rank}</td>
      <td>
        <a href="#card_{sym}" class="stock-link" style="font-size:13px;font-weight:700">{sym}</a>
        <div style="font-size:10px;color:#8b949e">{c['name']}</div>
      </td>
      <td style="color:#8b949e;font-size:11px">{c['sector']}</td>
      <td><strong>₹{c['cmp']:,.2f}</strong></td>
      <td>{_dir_badge(c['direction'])}</td>
      <td>
        <div style="display:flex;align-items:center;gap:8px">
          <div style="flex:1;height:6px;background:#21262d;border-radius:99px;min-width:60px;overflow:hidden">
            <div style="width:{prob}%;height:100%;background:{prob_color};border-radius:99px"></div>
          </div>
          <strong style="color:{prob_color}">{prob:.0f}%</strong>
        </div>
      </td>
      <td>
        <span style="color:{mtd_color};font-weight:700">{'+'if cm_mtd>=0 else ''}{cm_mtd:.1f}%</span>
        <div style="font-size:10px;color:#8b949e;margin-top:1px">EOM: <strong style="color:{mtd_color}">{cm_prob:.0f}%</strong></div>
      </td>
      <td>
        <span class="dir-badge {'up' if nm_dir=='UP' else 'dn'}">{'▲' if nm_dir=='UP' else '▼'} {nm_dir}</span>
        <div style="font-size:10px;margin-top:3px"><strong style="color:{nm_color}">{nm_prob:.0f}%</strong></div>
      </td>
      <td style="text-align:center">
        <input type="checkbox" class="take-trade-cb"
          id="cb_{sym}"
          onchange="takeTrade('{sym}', this)"
          title="Mark this trade as taken — will be monitored until complete">
        <span id="tl_{sym}" class="trade-taken-label" style="display:none">✓ In Trade</span>
      </td>
    </tr>"""


# ── Month overview table rows ─────────────────────────────────────────────────

def _cur_month_table_rows(candidates: list) -> str:
    rows = []
    for c in candidates:
        cm = c.get("cur_month", {})
        if not cm:
            continue
        mtd      = cm.get("mtd_return", 0)
        edir     = cm.get("eom_direction", "UP")
        prob     = cm.get("eom_prob", 50)
        days     = cm.get("trading_days_left", 5)
        up_tgt   = cm.get("eom_up_target", 0)
        dn_tgt   = cm.get("eom_down_target", 0)
        color    = "#56d364" if edir == "UP" else "#f85149"
        mtd_cls  = "pos" if mtd >= 0 else "neg"
        mtd_sign = "+" if mtd >= 0 else ""
        rows.append(f"""<tr>
          <td><a href="#card_{c['symbol']}" class="stock-link"><strong>{c['symbol']}</strong></a><div style="font-size:10px;color:#8b949e">{c['sector']}</div></td>
          <td><strong>₹{c['cmp']:,.2f}</strong></td>
          <td class="{mtd_cls}">{mtd_sign}{mtd:.1f}%<div style="font-size:9px;color:#8b949e">from ₹{cm.get("month_open",0):,.0f}</div></td>
          <td style="color:#d29922">{days}d</td>
          <td><span class="dir-badge {'up' if edir=='UP' else 'dn'}">{'▲' if edir=='UP' else '▼'} {edir}</span></td>
          <td><strong style="color:{color}">{prob:.0f}%</strong>
            <div style="width:60px;height:4px;background:#21262d;border-radius:99px;margin-top:3px;overflow:hidden">
              <div style="width:{prob}%;height:100%;background:{color};border-radius:99px"></div>
            </div>
          </td>
          <td style="font-size:11px">
            <span style="color:#56d364">▲₹{up_tgt:,.0f}</span> &nbsp;/&nbsp;
            <span style="color:#f85149">▼₹{dn_tgt:,.0f}</span>
          </td>
        </tr>""")
    return "\n".join(rows)


def _next_month_table_rows(candidates: list) -> str:
    rows = []
    for c in candidates:
        nm = c.get("next_month", {})
        if not nm:
            continue
        ndir    = nm.get("direction", "UP")
        prob    = nm.get("probability", 50)
        aligned = nm.get("aligned", False)
        color   = "#56d364" if ndir == "UP" else "#f85149"
        rhi     = nm.get("range_high", c["cmp"])
        rlo     = nm.get("range_low",  c["cmp"])
        rtgt    = nm.get("base_target", c["cmp"])
        rows.append(f"""<tr>
          <td><a href="#card_{c['symbol']}" class="stock-link"><strong>{c['symbol']}</strong></a><div style="font-size:10px;color:#8b949e">{c['sector']}</div></td>
          <td><strong>₹{c['cmp']:,.2f}</strong></td>
          <td><span class="dir-badge {'up' if ndir=='UP' else 'dn'}">{'▲' if ndir=='UP' else '▼'} {ndir}</span></td>
          <td><strong style="color:{color}">{prob:.0f}%</strong>
            <div style="width:60px;height:4px;background:#21262d;border-radius:99px;margin-top:3px;overflow:hidden">
              <div style="width:{prob}%;height:100%;background:{color};border-radius:99px"></div>
            </div>
          </td>
          <td>
            <span class="align-badge {'align-yes' if aligned else 'align-no'}">{'✓ Aligned' if aligned else '⚡ Conflict'}</span>
          </td>
          <td style="font-size:11px">
            <span style="color:#56d364">₹{rhi:,.0f}</span> –
            <span style="color:#f85149">₹{rlo:,.0f}</span>
            <div style="font-size:9px;color:#8b949e">Target: <strong style="color:{color}">₹{rtgt:,.2f}</strong></div>
          </td>
        </tr>""")
    return "\n".join(rows)


# ── Market Overview section ───────────────────────────────────────────────────

def _market_overview_html(data: dict) -> str:
    """Build the Market Overview HTML panel (Nifty 50, Crude Oil, Nat Gas)."""
    if not data:
        return ""

    def _dte_class(days):
        if days <= 5:  return "mkt-dte-hot"
        if days <= 10: return "mkt-dte-warn"
        return "mkt-dte-ok"

    def _fmt(val, unit="₹"):
        if val is None:
            return "—"
        if val >= 1000:
            return f"{unit}{val:,.2f}"
        return f"{unit}{val:.2f}"

    def _card(inst: dict) -> str:
        weekly = inst.get("weekly", [])
        unit   = inst.get("unit", "₹")
        dte    = inst["days_to_expiry"]

        # Key levels from all weekly data
        all_highs = [w["high"] for w in weekly if w.get("high")]
        all_lows  = [w["low"]  for w in weekly if w.get("low")]
        resistance = max(all_highs) if all_highs else None
        support    = min(all_lows)  if all_lows  else None

        levels_html = ""
        if resistance or support:
            levels_html = f"""
    <div class="mkt-levels">
      <div class="mkt-lvl">
        <div class="mkt-lvl-label">5-WK RESISTANCE</div>
        <div class="mkt-lvl-val mkt-hi">{_fmt(resistance, unit)}</div>
      </div>
      <div class="mkt-lvl">
        <div class="mkt-lvl-label">5-WK SUPPORT</div>
        <div class="mkt-lvl-val mkt-lo">{_fmt(support, unit)}</div>
      </div>
    </div>"""

        # Weekly candle table rows
        table_rows = ""
        for w in weekly:
            bull     = w.get("bull", True)
            pct      = w.get("pct", 0)
            pct_cls  = "mkt-bull" if bull else "mkt-bear"
            pct_sign = "+" if pct >= 0 else ""
            is_hi    = all_highs and w["high"] == resistance
            is_lo    = all_lows  and w["low"]  == support
            hi_cls   = "mkt-hi" if is_hi else ""
            lo_cls   = "mkt-lo" if is_lo else ""
            table_rows += f"""<tr>
          <td>{w['week']}</td>
          <td>{_fmt(w['open'], unit)}</td>
          <td class="{hi_cls}">{_fmt(w['high'], unit)}</td>
          <td class="{lo_cls}">{_fmt(w['low'],  unit)}</td>
          <td style="font-weight:700">{_fmt(w['close'], unit)}</td>
          <td class="{pct_cls}" style="font-weight:700">{pct_sign}{pct:.1f}%</td>
        </tr>"""

        table_html = ""
        if table_rows:
            table_html = f"""
    <table class="mkt-tbl">
      <thead><tr>
        <th>Week</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>%</th>
      </tr></thead>
      <tbody>{table_rows}</tbody>
    </table>"""

        dte_label = f"{dte}d to expiry" if dte > 0 else "Expiry today!"
        return f"""<div class="mkt-card">
  <div class="mkt-card-head">
    <div>
      <div class="mkt-card-title">{inst['name']}</div>
      <div class="mkt-card-note">{inst.get('note','')}</div>
      <div class="mkt-cmp">{_fmt(inst.get('cmp'), unit)}</div>
    </div>
    <div class="mkt-expiry-wrap">
      <div style="font-size:9px;color:#8b949e;margin-bottom:3px">CURRENT EXPIRY</div>
      <div class="mkt-expiry-date">{inst['expiry']}</div>
      <div class="mkt-expiry-dte {_dte_class(dte)}">{dte_label}</div>
    </div>
  </div>
  {levels_html}
  {table_html}
</div>"""

    cards = "".join(_card(data[k]) for k in ("nifty", "crude_oil", "nat_gas") if k in data)
    return f"""
  <!-- Market Overview: Nifty 50 / MCX Crude Oil / MCX Natural Gas -->
  <div class="sec-title">📈 Market Overview — Weekly Levels &amp; Expiry</div>
  <div class="mkt-overview">
    <div class="mkt-grid">{cards}</div>
  </div>"""


# ── Main generator ────────────────────────────────────────────────────────────

def generate_screener_report(candidates: list, output_file: str,
                              active_trades_meta: dict = None,
                              active_trades_data: dict = None,
                              all_stocks: list = None,
                              market_overview: dict = None) -> str:
    ts     = datetime.now().strftime("%d %b %Y, %I:%M %p")
    n_up   = sum(1 for c in candidates if c["direction"] == "UP")
    n_dn   = len(candidates) - n_up
    avg_pr = round(sum(c["probability"] for c in candidates) / len(candidates), 1) if candidates else 0

    cur_month_label  = candidates[0]["cur_month"]["label"]  if candidates and candidates[0].get("cur_month")  else "Current Month"
    next_month_label = candidates[0]["next_month"]["label"] if candidates and candidates[0].get("next_month") else "Next Month"
    cur_month_rows   = _cur_month_table_rows(candidates)
    next_month_rows  = _next_month_table_rows(candidates)

    summary_rows        = "\n".join(_summary_row(c, i+1) for i, c in enumerate(candidates))
    cards_html          = "\n".join(_build_card(c, i) for i, c in enumerate(candidates))
    market_overview_html = _market_overview_html(market_overview or {})

    # Cards for tracked symbols not in top picks
    tracked_cards_html = ""
    if active_trades_data:
        tracked_list = list(active_trades_data.values())
        tracked_cards_html = "\n".join(
            _build_card(c, 9000 + i, tracked=True)
            for i, c in enumerate(tracked_list)
        )

    def _compact(c, **extra):
        return {
            "name":        c["name"],
            "sector":      c["sector"],
            "cmp":         c["cmp"],
            "direction":   c["direction"],
            "probability": round(c["probability"], 1),
            "tech_score":  round(c.get("tech_score", 0), 1),
            "rsi":         round(c.get("rsi", 0), 1),
            "win_rate":    round(c.get("win_rate", 0), 1),
            "monthly_vol": round(c.get("monthly_vol", 0), 1),
            "atr_pct":     round(c.get("atr_pct", 0), 2),
            "vs_52h":      round(c.get("vs_52h", 0), 1),
            "daily_val_cr":round(c.get("daily_val_cr", 0), 1),
            **extra,
        }

    # Start with ALL analyzed stocks (lightweight) so any NSE stock
    # added to Active Trades has live probability/CMP available in JS
    screener_data = {c["symbol"]: _compact(c) for c in (all_stocks or candidates)}

    # Overlay/add extra tracked symbols (force-analyzed even outside top-N)
    for sym, c in (active_trades_data or {}).items():
        screener_data[sym] = _compact(c, tracked=True)

    screener_data_js      = json.dumps(screener_data, ensure_ascii=False)
    active_trades_meta_js = json.dumps(active_trades_meta or {}, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NSE Market Screener — {ts}</title>
<style>{CSS}</style>
</head>
<body>

<div class="header">
  <h1>🔭 NSE High-Probability Screener</h1>
  <div class="subtitle">
    Generated {ts} &nbsp;·&nbsp; Universe: Nifty 500 + Liquid Mid/Small Caps
    &nbsp;·&nbsp; Filter: Avg daily vol &gt; ₹5 Cr
  </div>
  <div class="method-note">
    <strong>Methodology:</strong> Each stock is scored across 5 technical dimensions — RSI extremes, MACD crossover,
    price vs 20/50/200 SMA, Bollinger Band position, and volume surge. Final probability blends
    current technical signal strength (45%) + historical monthly win rate (45%) + volume confirmation (10%).
    &nbsp;<strong>Not financial advice.</strong>
  </div>
</div>

<div class="main">

  <!-- Top bar summary -->
  <div class="top-bar">
    <div class="top-bar-item">
      <div class="tbi-label">Top Picks</div>
      <div class="tbi-val">{len(candidates)}</div>
      <div class="tbi-sub">Highest confidence setups</div>
    </div>
    <div class="top-bar-item">
      <div class="tbi-label">Bullish / Bearish</div>
      <div class="tbi-val">
        <span style="color:#56d364">{n_up} ▲</span>
        &nbsp;/&nbsp;
        <span style="color:#f85149">{n_dn} ▼</span>
      </div>
      <div class="tbi-sub">Direction split</div>
    </div>
    <div class="top-bar-item">
      <div class="tbi-label">Avg Probability</div>
      <div class="tbi-val" style="color:{'#56d364' if avg_pr >= 80 else '#58a6ff'}">{avg_pr}%</div>
      <div class="tbi-sub">Composite EOM signal</div>
    </div>
    <div class="top-bar-item">
      <div class="tbi-label">Current Month</div>
      <div class="tbi-val" style="color:#58a6ff">{cur_month_label}</div>
      <div class="tbi-sub">MTD progress tracked</div>
    </div>
    <div class="top-bar-item">
      <div class="tbi-label">Next Month</div>
      <div class="tbi-val" style="color:#d29922">{next_month_label}</div>
      <div class="tbi-sub">Forward outlook</div>
    </div>
  </div>

  {market_overview_html}

  <!-- Active Trades Panel (shown when trades exist) -->
  <div id="active-trades-panel" class="active-trades-panel" style="display:none">
    <div class="active-trades-header">
      <div class="ath-title">
        🟢 Active Trades &nbsp;<span class="at-badge" id="active-trades-badge">0</span>
        <span style="font-size:11px;font-weight:400;color:#7ee787;margin-left:4px">— monitored until marked complete</span>
      </div>
      <div class="ath-sub">Entry price · live P&amp;L · mark complete to stop tracking</div>
    </div>
    <table>
      <thead><tr style="background:#0a1a10">
        <th>Stock</th><th>Sector</th><th>CMP / P&L</th>
        <th>Direction</th><th>Probability</th><th>Trade Taken At</th><th>Action</th>
      </tr></thead>
      <tbody id="active-trades-tbody"></tbody>
    </table>
  </div>

  <!-- Track a Stock (manual active trade) -->
  <div class="track-stock-panel">
    <div class="ts-label">Track Another Stock in Active Trades</div>
    <div style="font-size:11px;color:#8b949e;margin-bottom:12px">
      Search by name or symbol — tracked with live P&amp;L until marked complete
    </div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end">
      <div>
        <div class="ts-label">Search Stock</div>
        <div class="stock-search-wrap">
          <input type="text" id="stock-search-input" class="stock-search-input"
            placeholder="Type name or symbol…" autocomplete="off">
          <button class="stock-search-clear" id="stock-search-clear" title="Clear">✕</button>
          <div class="stock-search-dropdown" id="stock-search-dropdown"></div>
        </div>
      </div>
      <div>
        <div class="ts-label">Entry Price (₹)</div>
        <input type="number" id="custom-price" placeholder="optional (auto-fills)" min="0" step="0.05">
      </div>
      <div>
        <div class="ts-label">Direction</div>
        <select id="custom-direction">
          <option value="UP">▲ LONG / UP</option>
          <option value="DOWN">▼ SHORT / DOWN</option>
        </select>
      </div>
      <div>
        <button class="track-stock-btn" onclick="addCustomTrade()">+ Track Stock</button>
      </div>
      <div id="custom-error" style="font-size:11px;align-self:center;max-width:300px"></div>
    </div>
  </div>

  <!-- Summary table -->
  <div class="sec-title">📊 Ranked Picks — Quick View</div>
  <div class="summary-table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th><th>Stock</th><th>Sector</th><th>CMP</th>
          <th>Direction</th><th>Avg Probability</th>
          <th>{cur_month_label} (MTD / EOM)</th><th>{next_month_label}</th>
          <th style="text-align:center">Take Trade</th>
        </tr>
      </thead>
      <tbody>{summary_rows}</tbody>
    </table>
  </div>

  <!-- Month predictions overview -->
  <div class="sec-title">📅 Month-by-Month Predictions — {cur_month_label} &amp; {next_month_label}</div>
  <div class="month-pred-grid">
    <div class="month-pred-table-wrap">
      <div class="month-pred-header">
        <span class="mph-title">📅 {cur_month_label} — EOM Direction</span>
        <span class="mph-sub">MTD progress + days remaining</span>
      </div>
      <table>
        <thead><tr>
          <th>Stock</th><th>CMP</th><th>MTD Return</th><th>Days Left</th>
          <th>EOM Direction</th><th>Probability</th><th>EOM Targets</th>
        </tr></thead>
        <tbody>{cur_month_rows}</tbody>
      </table>
    </div>
    <div class="month-pred-table-wrap">
      <div class="month-pred-header">
        <span class="mph-title">🔮 {next_month_label} — Forward Outlook</span>
        <span class="mph-sub">Seasonal + momentum alignment</span>
      </div>
      <table>
        <thead><tr>
          <th>Stock</th><th>CMP</th><th>Direction</th>
          <th>Probability</th><th>Signals</th><th>Expected Range / Target</th>
        </tr></thead>
        <tbody>{next_month_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- Detailed cards -->
  <div class="sec-title">🃏 Detailed Analysis — {cur_month_label} &amp; {next_month_label} Predictions</div>
  <div class="cards-grid">
    {cards_html}
  </div>

  <!-- Tracked active trade cards (not in top picks) -->
  {f'''<div class="sec-title">📌 Your Tracked Stocks — Full Analysis</div>
  <div class="cards-grid">{tracked_cards_html}</div>''' if tracked_cards_html else ''}

  <div class="disclaimer">
    ⚠️ <strong>Disclaimer:</strong> This screener is for educational and informational purposes only.
    Probabilities are statistical estimates based on historical patterns — past performance does not
    guarantee future results. This is NOT financial advice. Always consult a SEBI-registered research
    analyst before making any trading or investment decisions. Use at your own risk.
  </div>

</div>

<script>
window.SCREENER_DATA      = {screener_data_js};
window.ACTIVE_TRADES_META = {active_trades_meta_js};
{JS}
</script>
</body>
</html>"""

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)
    return output_file
