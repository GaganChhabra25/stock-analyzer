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

/* ── Key levels & options selling panel ── */
.levels-panel {
  margin: 14px 0; padding: 14px 16px;
  background: #0d1117; border: 1px solid #30363d;
  border-radius: 10px;
}
.levels-title {
  font-size: 11px; font-weight: 700; color: #8b949e;
  text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;
}
.swing-zones {
  display: flex; gap: 16px; flex-wrap: wrap;
  margin: 10px 0; font-size: 11px;
}
.sz-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.sz-label { font-weight: 600; min-width: 170px; }
.sz-val   { color: #e6edf3; }
.opt-rec {
  margin-top: 12px; padding: 10px 14px;
  border: 1px solid #30363d; border-radius: 8px;
  font-size: 11px; line-height: 1.6;
}
.opt-rec-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 6px;
}
.opt-strategy  { font-size: 13px; font-weight: 700; }
.opt-confidence{ font-size: 10px; font-weight: 600; letter-spacing: 0.3px; }
.opt-levels    { margin-bottom: 4px; font-size: 12px; }
.opt-logic     { color: #8b949e; font-size: 10px; }

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
@media (max-width: 1100px) { .mkt-grid { grid-template-columns: 1fr; } }
.mkt-card { background: #161b22; border: 1px solid #30363d; border-radius: 14px; overflow: hidden; }
.mkt-card-head {
  padding: 12px 16px 10px; border-bottom: 1px solid #21262d;
  display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;
}
.mkt-card-title { font-size: 14px; font-weight: 800; color: #e6edf3; }
.mkt-card-note  { font-size: 10px; color: #8b949e; margin-top: 2px; }
.mkt-cmp        { font-size: 22px; font-weight: 700; color: #58a6ff; white-space: nowrap; }
/* Monthly summary strip */
.mkt-monthly {
  padding: 10px 16px 6px; border-bottom: 1px solid #21262d;
  background: rgba(13,17,23,0.5);
}
.mkt-monthly-row {
  display: flex; justify-content: space-between; align-items: center;
  flex-wrap: wrap; gap: 6px;
}
.mkt-sec-label   { font-size: 9px; font-weight: 700; letter-spacing: 0.8px; color: #d29922; }
.mkt-expiry-info { font-size: 10px; color: #8b949e; }
.mkt-range-row   { margin-top: 4px; font-size: 10px; color: #8b949e; }
/* Weekly table */
.mkt-week-tbl { width: 100%; border-collapse: collapse; font-size: 11px; }
.mkt-week-tbl th {
  padding: 5px 8px; color: #8b949e; font-weight: 600; font-size: 10px;
  border-bottom: 1px solid #21262d; text-align: left; background: #0d1117;
}
.mkt-week-tbl td { padding: 6px 8px; border-bottom: 1px solid rgba(48,54,61,0.4); vertical-align: middle; }
.mkt-week-tbl tr:last-child td { border-bottom: none; }
.mkt-current-row { background: rgba(88,166,255,0.06) !important; }
.mkt-past-row    { opacity: 0.55; }
.mkt-monthly-badge {
  font-size: 8px; font-weight: 700; padding: 1px 5px; border-radius: 4px;
  background: rgba(210,153,34,0.15); color: #d29922;
  border: 1px solid rgba(210,153,34,0.3); margin-left: 4px; vertical-align: middle;
}
.mkt-week-label { font-size: 11px; font-weight: 700; color: #e6edf3; }
.mkt-date-range { font-size: 10px; color: #8b949e; margin-top: 1px; }
.mkt-prob-bar {
  width: 50px; height: 3px; background: #21262d; border-radius: 99px;
  margin-top: 3px; overflow: hidden;
}
.mkt-dte-ok   { color: #56d364; }
.mkt-dte-warn { color: #d29922; }
.mkt-dte-hot  { color: #f85149; }
.mkt-signals {
  border-top: 1px solid #21262d;
  margin-top: 10px;
  padding-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
}
.mkt-sig-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 3px 8px;
  font-size: 10px;
  white-space: nowrap;
}
.mkt-sig-pill .sname { color: #8b949e; }
.mkt-sig-pill .sval  { font-weight: 700; }
.mkt-sig-pill.bull   { border-color: rgba(86,211,100,0.30); }
.mkt-sig-pill.bear   { border-color: rgba(248,81,73,0.30); }
.mkt-sig-pill.neut   { border-color: #30363d; }
.mkt-meta-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  font-size: 11px;
}
.mkt-meta-bar .mlabel { color: #8b949e; margin-right: 3px; }
.mkt-meta-bar .mval   { font-weight: 700; }

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


# ── Key levels + options block ────────────────────────────────────────────────

def _levels_block(c: dict) -> str:
    """Render weekly/monthly pivot levels and options selling recommendation."""
    lvl = c.get("levels")
    if not lvl:
        return ""

    w   = lvl.get("weekly")  or {}
    m   = lvl.get("monthly") or {}
    sw  = lvl.get("swings")  or {}
    opt = lvl.get("options_rec") or {}

    def _p(val):
        return f"₹{val:,.2f}" if val else "—"

    # Weekly pivot row
    w_rows = ""
    if w:
        w_rows = f"""
        <tr>
          <td style="color:#58a6ff;font-weight:700">Weekly ({w.get('label','')})</td>
          <td style="color:#f85149">{_p(w.get('s3'))} / {_p(w.get('s2'))} / {_p(w.get('s1'))}</td>
          <td style="color:#8b949e">{_p(w.get('bc'))} – {_p(w.get('pp'))} – {_p(w.get('tc'))}</td>
          <td style="color:#56d364">{_p(w.get('r1'))} / {_p(w.get('r2'))} / {_p(w.get('r3'))}</td>
          <td style="color:#d29922">{w.get('cpr_width',0):.2f}%</td>
        </tr>"""

    # Monthly pivot row
    m_rows = ""
    if m:
        m_rows = f"""
        <tr>
          <td style="color:#d29922;font-weight:700">Monthly ({m.get('label','')})</td>
          <td style="color:#f85149">{_p(m.get('s3'))} / {_p(m.get('s2'))} / {_p(m.get('s1'))}</td>
          <td style="color:#8b949e">{_p(m.get('bc'))} – {_p(m.get('pp'))} – {_p(m.get('tc'))}</td>
          <td style="color:#56d364">{_p(m.get('r1'))} / {_p(m.get('r2'))} / {_p(m.get('r3'))}</td>
          <td style="color:#d29922">{m.get('cpr_width',0):.2f}%</td>
        </tr>"""

    # Swing zones
    res_zones = " | ".join(
        f"₹{z['level']:,.2f} ({z['touches']}T)" for z in sw.get("resistance", [])
    ) or "—"
    sup_zones = " | ".join(
        f"₹{z['level']:,.2f} ({z['touches']}T)" for z in sw.get("support", [])
    ) or "—"

    # Options recommendation
    strategy   = opt.get("strategy", "—")
    confidence = opt.get("confidence", "—")
    logic      = opt.get("logic", "")
    opt_color  = opt.get("color", "#8b949e")
    conf_color = {"HIGH": "#56d364", "MEDIUM": "#d29922", "LOW": "#f85149"}.get(confidence, "#8b949e")

    if strategy == "Sell PUT":
        opt_detail = (
            f"<span style='color:#56d364'>Sell PUT ≤ ₹{opt.get('monthly_put_sell',0):,.2f}</span>"
            f" &nbsp;|&nbsp; SL if breaks ₹{opt.get('monthly_s2',0):,.2f}"
            f"<br><small style='color:#8b949e'>Weekly: sell PUT ≤ ₹{opt.get('weekly_put_sell',0):,.2f}</small>"
        )
    elif strategy == "Sell CALL":
        opt_detail = (
            f"<span style='color:#f85149'>Sell CALL ≥ ₹{opt.get('monthly_call_sell',0):,.2f}</span>"
            f" &nbsp;|&nbsp; SL if breaks ₹{opt.get('monthly_r2',0):,.2f}"
            f"<br><small style='color:#8b949e'>Weekly: sell CALL ≥ ₹{opt.get('weekly_call_sell',0):,.2f}</small>"
        )
    else:
        opt_detail = (
            f"<span style='color:#f85149'>Sell CALL ≥ ₹{opt.get('monthly_r2',0):,.2f}</span>"
            f" &nbsp;+&nbsp; "
            f"<span style='color:#56d364'>Sell PUT ≤ ₹{opt.get('monthly_s2',0):,.2f}</span>"
        )

    return f"""
    <div class="levels-panel">
      <div class="levels-title">📐 Key Levels &amp; Options Selling Zones</div>

      <!-- Pivot table -->
      <div class="bt-scroll">
        <table class="mini-table">
          <thead><tr>
            <th>Timeframe</th>
            <th style="color:#f85149">Support (S3/S2/S1)</th>
            <th>CPR (BC – PP – TC)</th>
            <th style="color:#56d364">Resistance (R1/R2/R3)</th>
            <th>CPR Width</th>
          </tr></thead>
          <tbody>{w_rows}{m_rows}</tbody>
        </table>
      </div>

      <!-- Swing zones -->
      <div class="swing-zones">
        <div class="sz-row">
          <span class="sz-label" style="color:#f85149">▼ Swing Support zones</span>
          <span class="sz-val">{sup_zones}</span>
        </div>
        <div class="sz-row">
          <span class="sz-label" style="color:#56d364">▲ Swing Resistance zones</span>
          <span class="sz-val">{res_zones}</span>
        </div>
      </div>

      <!-- Options recommendation -->
      <div class="opt-rec" style="border-color:{opt_color}30;background:{opt_color}0d">
        <div class="opt-rec-header">
          <span class="opt-strategy" style="color:{opt_color}">⚡ {strategy}</span>
          <span class="opt-confidence" style="color:{conf_color}">Confidence: {confidence}</span>
        </div>
        <div class="opt-levels">{opt_detail}</div>
        <div class="opt-logic">{logic}</div>
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

    <!-- Key levels & options selling -->
    {_levels_block(c)}

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
    """Build the Market Overview panel: weekly schedule + monthly prediction per instrument."""
    if not data:
        return ""

    def _p(val, unit="\u20b9"):
        """Format price — no decimal for large values, 2dp for small."""
        if val is None:
            return "\u2014"
        if val >= 10000:
            return f"{unit}{val:,.0f}"
        if val >= 100:
            return f"{unit}{val:,.1f}"
        return f"{unit}{val:.2f}"

    def _dte_cls(days):
        if days <= 5:  return "mkt-dte-hot"
        if days <= 10: return "mkt-dte-warn"
        return "mkt-dte-ok"

    def _card(inst: dict) -> str:
        if inst.get("error"):
            return f"""<div class="mkt-card" style="padding:20px;color:#8b949e">
  <strong>{inst['name']}</strong><br>
  <span style="font-size:11px">Data unavailable</span></div>"""

        unit     = inst.get("unit", "\u20b9")
        schedule = inst.get("weekly_schedule", [])
        monthly  = inst.get("monthly", {})

        # ── Monthly summary strip ──────────────────────────────────────────────
        m_dir  = monthly.get("direction", "\u2014")
        m_prob = monthly.get("probability", 0)
        m_exp  = monthly.get("expected_price")
        m_dte  = monthly.get("days_to_expiry", 0)
        m_date = monthly.get("expiry_date", "\u2014")
        m_col  = "#56d364" if m_dir == "UP" else "#f85149"
        m_arr  = "\u25b2" if m_dir == "UP" else "\u25bc"
        m_bull = monthly.get("bull_target")
        m_bear = monthly.get("bear_target")
        m_hwr  = monthly.get("hist_wr", 0)

        monthly_html = f"""<div class="mkt-monthly">
  <div class="mkt-monthly-row">
    <div>
      <span class="mkt-sec-label">MONTHLY OUTLOOK</span>
      <span class="mkt-expiry-info"> &nbsp;&middot;&nbsp; Expiry {m_date}
        <span class="{_dte_cls(m_dte)}" style="font-weight:700"> ({m_dte}d)</span>
      </span>
    </div>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <span style="color:{m_col};font-weight:800;font-size:13px">{m_arr} {m_dir}</span>
      <strong style="color:{m_col};font-size:13px">{m_prob:.0f}%</strong>
      <span style="color:#8b949e;font-size:11px">Target:
        <strong style="color:{m_col}">{_p(m_exp, unit)}</strong>
      </span>
    </div>
  </div>
  <div class="mkt-range-row">
    Bull: <strong style="color:#56d364">{_p(m_bull, unit)}</strong>
    &nbsp;&nbsp;
    Bear: <strong style="color:#f85149">{_p(m_bear, unit)}</strong>
    &nbsp;&nbsp;
    Hist win-rate: <strong>{m_hwr:.0f}%</strong>
  </div>
</div>"""

        # ── Weekly schedule table ──────────────────────────────────────────────
        week_rows = ""
        for w in schedule:
            is_past    = w.get("is_past", False)
            is_current = w.get("is_current", False)
            is_monthly = w.get("is_monthly_expiry", False)
            row_cls    = "mkt-past-row" if is_past else ("mkt-current-row" if is_current else "")

            monthly_badge = (' <span class="mkt-monthly-badge">Monthly</span>'
                             if is_monthly else "")

            if is_past:
                a_dir  = w.get("actual_dir", "\u2014")
                a_pct  = w.get("actual_pct", 0) or 0
                a_cl   = w.get("actual_close")
                a_col  = "#56d364" if a_dir == "UP" else ("#f85149" if a_dir == "DOWN" else "#8b949e")
                a_arr  = "\u25b2" if a_dir == "UP" else ("\u25bc" if a_dir == "DOWN" else "\u2014")
                sign   = "+" if a_pct >= 0 else ""
                dir_td = f'<span style="color:{a_col};font-weight:700">{a_arr} {a_dir}</span>'
                pb_td  = f'<span style="color:#8b949e;font-size:10px">{sign}{a_pct:.1f}%</span>'
                tgt_td = f'<span style="color:{a_col}">{_p(a_cl, unit)}</span>'
            else:
                d_col  = "#56d364" if w["direction"] == "UP" else "#f85149"
                d_arr  = "\u25b2" if w["direction"] == "UP" else "\u25bc"
                prob   = w["probability"]
                fw     = "800" if is_current else "700"
                dir_td = f'<span style="color:{d_col};font-weight:{fw}">{d_arr} {w["direction"]}</span>'
                pb_td  = (f'<strong style="color:{d_col}">{prob:.0f}%</strong>'
                          f'<div class="mkt-prob-bar">'
                          f'<div style="width:{prob}%;height:100%;background:{d_col};border-radius:99px"></div>'
                          f'</div>')
                tgt_td = f'<strong style="color:{d_col}">{_p(w["target"], unit)}</strong>'

            wr_td = f'<span style="color:#8b949e;font-size:10px">{w["hist_wr"]:.0f}%</span>'

            week_rows += f"""<tr class="{row_cls}">
        <td>
          <div class="mkt-week-label">{w['label']}{monthly_badge}</div>
          <div class="mkt-date-range">{w['date_from']} \u2013 {w['date_to']}</div>
        </td>
        <td style="font-size:10px;color:#8b949e">{w['expiry_date']}</td>
        <td>{dir_td}</td>
        <td>{pb_td}</td>
        <td>{tgt_td}</td>
        <td>{wr_td}</td>
      </tr>"""

        table_html = ""
        if week_rows:
            table_html = f"""<table class="mkt-week-tbl">
  <thead><tr>
    <th>Week</th><th>Expiry</th><th>Direction</th>
    <th>Probability</th><th>Expected Price</th><th>Hist WR</th>
  </tr></thead>
  <tbody>{week_rows}</tbody>
</table>"""

        # ── Signal pills ──────────────────────────────────────────────────────
        sigs  = inst.get("signals", {})
        pills = ""

        def _sig_pill(label: str, value, fmt_fn=None, bull_thresh=0.0, bear_thresh=0.0,
                      invert=False, unit_str=""):
            if value is None:
                return ""
            display = fmt_fn(value) if fmt_fn else str(value)
            if isinstance(value, float):
                if (value >= bull_thresh and not invert) or (value <= bear_thresh and invert):
                    cls = "bull"
                elif (value <= bear_thresh and not invert) or (value >= bull_thresh and invert):
                    cls = "bear"
                else:
                    cls = "neut"
            else:
                cls = "neut"
            return (f'<div class="mkt-sig-pill {cls}">'
                    f'<span class="sname">{label}</span>'
                    f'<span class="sval">{display}{unit_str}</span></div>')

        # Tech signal
        tech_val = sigs.get("tech")
        if tech_val is not None:
            tech_dir = "Bullish" if tech_val > 0.1 else ("Bearish" if tech_val < -0.1 else "Neutral")
            pills += _sig_pill("Tech", float(tech_val),
                                fmt_fn=lambda v: f"{v:+.2f} {tech_dir}",
                                bull_thresh=0.1, bear_thresh=-0.1)

        # VIX signal
        vix_val = sigs.get("vix")
        if vix_val is not None:
            vix_dir = "Bullish" if vix_val > 0.1 else ("Bearish" if vix_val < -0.1 else "Neutral")
            pills += _sig_pill("VIX signal", float(vix_val),
                                fmt_fn=lambda v: f"{v:+.2f} {vix_dir}",
                                bull_thresh=0.1, bear_thresh=-0.1)

        # PCR value
        pcr_val = sigs.get("pcr")
        if pcr_val is not None:
            pcr_cls = "bull" if pcr_val > 1.1 else ("bear" if pcr_val < 0.9 else "neut")
            pills += (f'<div class="mkt-sig-pill {pcr_cls}">'
                      f'<span class="sname">PCR</span>'
                      f'<span class="sval">{pcr_val:.2f}</span></div>')

        # Max Pain level
        mp_val = sigs.get("max_pain")
        cmp_v  = inst.get("cmp", 0) or 0
        if mp_val is not None and cmp_v > 0:
            diff_pct = (mp_val - cmp_v) / cmp_v * 100
            mp_cls   = "bull" if diff_pct > 0.5 else ("bear" if diff_pct < -0.5 else "neut")
            pills += (f'<div class="mkt-sig-pill {mp_cls}">'
                      f'<span class="sname">Max Pain</span>'
                      f'<span class="sval">{_p(mp_val, unit)} '
                      f'({diff_pct:+.1f}%)</span></div>')

        # ATM IV
        iv_val = sigs.get("atm_iv")
        if iv_val is not None:
            pills += (f'<div class="mkt-sig-pill neut">'
                      f'<span class="sname">ATM IV</span>'
                      f'<span class="sval">{iv_val:.1f}%</span></div>')

        # Support / Resistance from options OI
        sup_val = sigs.get("support")
        res_val = sigs.get("resistance")
        if sup_val is not None:
            pills += (f'<div class="mkt-sig-pill bull">'
                      f'<span class="sname">Put OI Wall</span>'
                      f'<span class="sval">{_p(sup_val, unit)}</span></div>')
        if res_val is not None:
            pills += (f'<div class="mkt-sig-pill bear">'
                      f'<span class="sname">Call OI Wall</span>'
                      f'<span class="sval">{_p(res_val, unit)}</span></div>')

        # Global + FII
        gl_val  = sigs.get("global")
        fii_val = sigs.get("fii")
        if gl_val is not None:
            gl_dir = "↑" if gl_val > 0.05 else ("↓" if gl_val < -0.05 else "→")
            pills += _sig_pill("Global", float(gl_val),
                                fmt_fn=lambda v: f"{v:+.2f} {gl_dir}",
                                bull_thresh=0.05, bear_thresh=-0.05)
        if fii_val is not None and fii_val != 0.0:
            fii_dir = "Buying" if fii_val > 0 else "Selling"
            pills += (f'<div class="mkt-sig-pill {"bull" if fii_val > 0 else "bear"}">'
                      f'<span class="sname">FII</span>'
                      f'<span class="sval">{fii_dir} {abs(fii_val):.2f}</span></div>')

        # Seasonal
        seas_val = sigs.get("seasonal")
        if seas_val is not None:
            seas_dir = "Peak season" if seas_val > 0.2 else ("Off season" if seas_val < -0.2 else "Shoulder")
            pills += (f'<div class="mkt-sig-pill {"bull" if seas_val > 0.1 else ("bear" if seas_val < -0.1 else "neut")}">'
                      f'<span class="sname">Seasonal</span>'
                      f'<span class="sval">{seas_dir}</span></div>')

        signals_html = f'<div class="mkt-signals">{pills}</div>' if pills else ""

        # ── Pivot levels for this instrument ──────────────────────────────────
        inst_levels_html = _levels_block({
            "levels":    inst.get("levels", {}),
            "cmp":       inst.get("cmp", 0),
            "direction": inst.get("monthly", {}).get("direction", "UP"),
            "probability": inst.get("monthly", {}).get("probability", 65),
            "monthly_vol": 0,
        })

        return f"""<div class="mkt-card">
  <div class="mkt-card-head">
    <div>
      <div class="mkt-card-title">{inst['name']}</div>
      <div class="mkt-card-note">{inst.get('note', '')}</div>
    </div>
    <div class="mkt-cmp">{_p(inst.get('cmp'), unit)}</div>
  </div>
  {monthly_html}
  {table_html}
  {signals_html}
  {inst_levels_html}
</div>"""

    # ── Meta-signals banner (shared signals for all instruments) ──────────────
    meta = data.get("meta_signals", {})
    meta_pills = ""
    if meta:
        def _mp(label, val, fmt=""):
            if val is None: return ""
            return f'<span><span class="mlabel">{label}</span><span class="mval">{val}{fmt}</span></span>'
        vix_s  = meta.get("india_vix")
        glo_s  = meta.get("global")
        fii_s  = meta.get("fii")
        pcr_s  = meta.get("pcr")
        mp_s   = meta.get("max_pain")
        iv_s   = meta.get("atm_iv")
        sup_s  = meta.get("support")
        res_s  = meta.get("resistance")
        if vix_s  is not None: meta_pills += _mp("VIX signal:", f"{vix_s:+.2f}")
        if glo_s  is not None: meta_pills += _mp("Global:", f"{glo_s:+.2f}")
        if fii_s  is not None: meta_pills += _mp("FII:", f"{fii_s:+.2f}")
        if pcr_s  is not None: meta_pills += _mp("Nifty PCR:", f"{pcr_s:.2f}")
        if mp_s   is not None: meta_pills += _mp("Max Pain:", f"₹{mp_s:,.0f}")
        if iv_s   is not None: meta_pills += _mp("ATM IV:", f"{iv_s:.1f}%")
        if sup_s  is not None: meta_pills += _mp("PE wall:", f"₹{sup_s:,.0f}")
        if res_s  is not None: meta_pills += _mp("CE wall:", f"₹{res_s:,.0f}")

    meta_bar = (f'<div class="mkt-meta-bar">{meta_pills}</div>'
                if meta_pills else "")

    cards = "".join(_card(data[k]) for k in ("nifty", "crude_oil", "nat_gas") if k in data)
    return f"""
  <!-- Market Overview: Nifty 50 / MCX Crude Oil / MCX Natural Gas -->
  <div class="sec-title">\U0001f4c8 Market Overview \u2014 Weekly &amp; Monthly Outlook</div>
  <div class="mkt-overview">
    {meta_bar}
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
