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

/* ── Take Trade checkbox ── */
.take-trade-cb {
  width: 17px; height: 17px; cursor: pointer; accent-color: #56d364;
  vertical-align: middle;
}
.trade-taken-label {
  font-size: 10px; color: #56d364; font-weight: 700;
  letter-spacing: 0.4px; display: block; margin-top: 2px;
}

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
}

function takeTrade(symbol, cb) {
  var trades = getActiveTrades();
  if (cb.checked) {
    // Get stock data from embedded JSON
    var data = window.SCREENER_DATA && window.SCREENER_DATA[symbol];
    trades[symbol] = {
      symbol: symbol,
      name:   data ? data.name   : symbol,
      sector: data ? data.sector : '',
      cmp:    data ? data.cmp    : 0,
      direction: data ? data.direction : '',
      probability: data ? data.probability : 0,
      takenAt: new Date().toLocaleString('en-IN', {day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'})
    };
  } else {
    delete trades[symbol];
  }
  saveActiveTrades(trades);
  renderActiveTrades();
}

function completeTrade(symbol) {
  var trades = getActiveTrades();
  delete trades[symbol];
  saveActiveTrades(trades);
  // Uncheck the checkbox if stock is in current scan
  var cb = document.getElementById('cb_' + symbol);
  if (cb) cb.checked = false;
  var lbl = document.getElementById('tl_' + symbol);
  if (lbl) lbl.style.display = 'none';
  renderActiveTrades();
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
    var dirColor = t.direction === 'UP' ? '#56d364' : '#f85149';
    var dirArrow = t.direction === 'UP' ? '\\u25b2' : '\\u25bc';
    // Check if stock is in current scan and get latest CMP
    var latest = window.SCREENER_DATA && window.SCREENER_DATA[sym];
    var cmpDisplay = latest
      ? ('\\u20b9' + latest.cmp.toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2}))
      : ('\\u20b9' + t.cmp.toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2}) + ' <span style="font-size:9px;color:#8b949e">(prev scan)</span>');
    var pnlHtml = '';
    if (latest && t.cmp > 0) {
      var pnl = ((latest.cmp - t.cmp) / t.cmp) * 100;
      var pnlColor = pnl >= 0 ? '#56d364' : '#f85149';
      var pnlSign  = pnl >= 0 ? '+' : '';
      pnlHtml = '<div style="font-size:10px;color:' + pnlColor + ';font-weight:700">' + pnlSign + pnl.toFixed(1) + '% since entry</div>';
    }
    rows += '<tr style="border-top:1px solid rgba(86,211,100,0.15)">'
      + '<td><strong style="font-size:13px">' + sym + '</strong>'
      + '<div style="font-size:10px;color:#8b949e">' + t.name + '</div></td>'
      + '<td style="color:#8b949e;font-size:11px">' + t.sector + '</td>'
      + '<td>' + cmpDisplay + pnlHtml + '</td>'
      + '<td><span style="color:' + dirColor + ';font-weight:700">' + dirArrow + ' ' + t.direction + '</span></td>'
      + '<td><strong style="color:' + dirColor + '">' + t.probability + '%</strong></td>'
      + '<td><span class="at-trade-taken-at">' + t.takenAt + '</span></td>'
      + '<td><button class="complete-btn" onclick="completeTrade(\\'' + sym + '\\')">\\u2713 Mark Complete</button></td>'
      + '</tr>';
  });
  tbody.innerHTML = rows;
}

// Init on page load
window.addEventListener('DOMContentLoaded', function() {
  var trades = getActiveTrades();
  // Restore checkboxes for stocks in current scan
  Object.keys(trades).forEach(function(sym) {
    var cb  = document.getElementById('cb_' + sym);
    var lbl = document.getElementById('tl_' + sym);
    if (cb)  cb.checked = true;
    if (lbl) lbl.style.display = 'block';
  });
  renderActiveTrades();
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

def _build_card(c: dict, idx: int) -> str:
    direction = c["direction"]
    prob      = c["probability"]
    bd        = c["breakdown"]
    up        = direction == "UP"

    dir_cls    = "up-card" if up else "dn-card"
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

    return f"""
<div class="ccard {dir_cls}">
  <div class="ccard-header">
    <div>
      <div class="ccard-symbol">{c['symbol']}</div>
      <div class="ccard-name">{c['name']}</div>
      <div class="ccard-sector">{c['sector']}</div>
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
        <strong style="font-size:13px">{sym}</strong>
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
          <td><strong>{c['symbol']}</strong><div style="font-size:10px;color:#8b949e">{c['sector']}</div></td>
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
          <td><strong>{c['symbol']}</strong><div style="font-size:10px;color:#8b949e">{c['sector']}</div></td>
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


# ── Main generator ────────────────────────────────────────────────────────────

def generate_screener_report(candidates: list, output_file: str) -> str:
    ts     = datetime.now().strftime("%d %b %Y, %I:%M %p")
    n_up   = sum(1 for c in candidates if c["direction"] == "UP")
    n_dn   = len(candidates) - n_up
    avg_pr = round(sum(c["probability"] for c in candidates) / len(candidates), 1) if candidates else 0

    cur_month_label  = candidates[0]["cur_month"]["label"]  if candidates and candidates[0].get("cur_month")  else "Current Month"
    next_month_label = candidates[0]["next_month"]["label"] if candidates and candidates[0].get("next_month") else "Next Month"
    cur_month_rows   = _cur_month_table_rows(candidates)
    next_month_rows  = _next_month_table_rows(candidates)

    summary_rows = "\n".join(_summary_row(c, i+1) for i, c in enumerate(candidates))
    cards_html   = "\n".join(_build_card(c, i) for i, c in enumerate(candidates))

    # Build compact stock data dict for JS (for Active Trades panel)
    screener_data = {
        c["symbol"]: {
            "name":        c["name"],
            "sector":      c["sector"],
            "cmp":         c["cmp"],
            "direction":   c["direction"],
            "probability": round(c["probability"], 1),
        }
        for c in candidates
    }
    screener_data_js = json.dumps(screener_data, ensure_ascii=False)

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

  <div class="disclaimer">
    ⚠️ <strong>Disclaimer:</strong> This screener is for educational and informational purposes only.
    Probabilities are statistical estimates based on historical patterns — past performance does not
    guarantee future results. This is NOT financial advice. Always consult a SEBI-registered research
    analyst before making any trading or investment decisions. Use at your own risk.
  </div>

</div>

<script>
window.SCREENER_DATA = {screener_data_js};
{JS}
</script>
</body>
</html>"""

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)
    return output_file
