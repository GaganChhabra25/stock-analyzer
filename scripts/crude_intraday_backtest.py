"""
CRUDEOIL Intraday Backtest — Full Session (9 AM to 11:30 PM IST)
Strategy: Supertrend + EMA 9/21 + VWAP + RSI
- Indicators computed across ALL data (no daily warm-up lag)
- VWAP and trade state reset each day
- No overnight positions — hard exit at 11:15 PM
"""
import os, sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, time as dtime
from colorama import init as colorama_init, Fore, Style
colorama_init(autoreset=True)

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

import psycopg2

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", 5433))
DB_NAME = os.environ.get("POSTGRES_DB", "stock_analyzer")
DB_USER = os.environ.get("POSTGRES_USER", "stockanalyzer")
DB_PASS = os.environ.get("POSTGRES_PASSWORD", "")

# ── Strategy params ───────────────────────────────────────────────────────────
ATR_PERIOD   = 7
ST_MULT      = 2.0
EMA_FAST     = 9
EMA_SLOW     = 21
RSI_PERIOD   = 14
SL_ATR_MULT  = 1.5
TP_ATR_MULT  = 2.5       # 1:1.67 RR — more realistic TP
MAX_TRADES   = 5
LOT_SIZE     = 100
BROKERAGE    = 200
EOD_HOUR     = 23
EOD_MIN      = 15        # Hard exit at 11:15 PM IST

# ── Indicators ────────────────────────────────────────────────────────────────

def calc_ema(prices, period):
    k = 2 / (period + 1)
    out = [None] * len(prices)
    for i, p in enumerate(prices):
        if i < period - 1:
            continue
        out[i] = sum(prices[:period]) / period if i == period - 1 else p * k + out[i-1] * (1 - k)
    return out


def calc_rsi(prices, period=14):
    out = [None] * len(prices)
    if len(prices) < period + 1:
        return out
    gains  = [max(prices[i] - prices[i-1], 0) for i in range(1, period + 1)]
    losses = [max(prices[i-1] - prices[i], 0) for i in range(1, period + 1)]
    ag, al = sum(gains) / period, sum(losses) / period
    for i in range(period, len(prices)):
        if i > period:
            d = prices[i] - prices[i-1]
            ag = (ag * (period - 1) + max(d, 0)) / period
            al = (al * (period - 1) + max(-d, 0)) / period
        rs = ag / al if al != 0 else 100
        out[i] = 100 - (100 / (1 + rs))
    return out


def calc_atr(highs, lows, closes, period=7):
    trs = []
    for i in range(len(closes)):
        if i == 0:
            trs.append(highs[i] - lows[i])
        else:
            trs.append(max(highs[i] - lows[i],
                           abs(highs[i] - closes[i-1]),
                           abs(lows[i] - closes[i-1])))
    out = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        out[i] = sum(trs[i-period+1:i+1]) / period
    return out


def calc_supertrend(highs, lows, closes, period=7, mult=2.0):
    atr_vals = calc_atr(highs, lows, closes, period)
    n = len(closes)
    direction = [None] * n
    upper_band = [None] * n
    lower_band = [None] * n

    for i in range(n):
        if atr_vals[i] is None:
            continue
        hl2 = (highs[i] + lows[i]) / 2
        ub = hl2 + mult * atr_vals[i]
        lb = hl2 - mult * atr_vals[i]
        if i == 0 or upper_band[i-1] is None:
            upper_band[i] = ub
            lower_band[i] = lb
        else:
            upper_band[i] = ub if (ub < upper_band[i-1] or closes[i-1] > upper_band[i-1]) else upper_band[i-1]
            lower_band[i] = lb if (lb > lower_band[i-1] or closes[i-1] < lower_band[i-1]) else lower_band[i-1]

        if direction[i-1] is None:
            direction[i] = 1 if closes[i] > upper_band[i] else -1
        elif direction[i-1] == -1 and closes[i] > upper_band[i]:
            direction[i] = 1
        elif direction[i-1] == 1 and closes[i] < lower_band[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]

    return direction, atr_vals


# ── Load data from DB ─────────────────────────────────────────────────────────

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                        user=DB_USER, password=DB_PASS)
cur = conn.cursor()

# Stored 15-min data
cur.execute("""
    SELECT
        (ts AT TIME ZONE 'Asia/Kolkata') AS ts_ist,
        open, high, low, close, COALESCE(volume, 0) AS vol
    FROM mcx_ohlc
    WHERE instrument='CRUDEOIL' AND interval='15minute'
    ORDER BY ts
""")
rows_15 = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[5])) for r in cur.fetchall()]

# Minute data aggregated to 15-min (for dates not in 15-min table)
cur.execute("""
    WITH src AS (
        SELECT
            ts AT TIME ZONE 'Asia/Kolkata' AS ts_ist,
            open, high, low, close, COALESCE(volume,0) AS vol
        FROM mcx_ohlc
        WHERE instrument='CRUDEOIL' AND interval='minute'
          AND (ts AT TIME ZONE 'Asia/Kolkata')::date NOT IN (
              SELECT DISTINCT (ts AT TIME ZONE 'Asia/Kolkata')::date
              FROM mcx_ohlc WHERE instrument='CRUDEOIL' AND interval='15minute'
          )
    ),
    bucketed AS (
        SELECT
            date_trunc('hour', ts_ist) +
              (FLOOR(EXTRACT(MINUTE FROM ts_ist) / 15) * INTERVAL '15 min') AS bucket,
            open, high, low, close, vol, ts_ist
        FROM src
    )
    SELECT
        bucket,
        (array_agg(open  ORDER BY ts_ist))[1],
        MAX(high),
        MIN(low),
        (array_agg(close ORDER BY ts_ist DESC))[1],
        SUM(vol)
    FROM bucketed
    GROUP BY bucket
    ORDER BY bucket
""")
rows_min = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[5])) for r in cur.fetchall()]
cur.close(); conn.close()

# Merge and sort all candles
all_candles = sorted(rows_15 + rows_min, key=lambda x: x[0])

if not all_candles:
    print("No data found!")
    sys.exit(1)

# ── Compute indicators on full dataset ───────────────────────────────────────
closes = [r[4] for r in all_candles]
highs  = [r[2] for r in all_candles]
lows   = [r[3] for r in all_candles]

ema9_arr  = calc_ema(closes, EMA_FAST)
ema21_arr = calc_ema(closes, EMA_SLOW)
rsi_arr   = calc_rsi(closes, RSI_PERIOD)
st_dir, atr_vals = calc_supertrend(highs, lows, closes, ATR_PERIOD, ST_MULT)

# ── Run intraday backtest ─────────────────────────────────────────────────────
all_trades = []
cur_date   = None
in_trade   = False
direction_t = entry_price = sl = tp_price = None
day_trades  = []
day_trade_count = 0

# Intraday VWAP vars (reset each day)
cum_tpv = cum_v = 0.0

# minimum index for reliable indicators
min_idx = max(ATR_PERIOD, RSI_PERIOD, EMA_SLOW)

for i in range(min_idx, len(all_candles)):
    ts, o, h, l, c, vol = all_candles[i]
    ts_date = ts.date()
    ts_time = ts.time()

    # ── Day change — reset VWAP and trade state ──────────────────────────────
    if ts_date != cur_date:
        if in_trade and cur_date is not None:
            # EOD forced exit from previous day
            pnl_pts = (closes[i-1] - entry_price) if direction_t == "LONG" else (entry_price - closes[i-1])
            pnl = pnl_pts * LOT_SIZE - BROKERAGE
            day_trades.append(("WIN" if pnl > 0 else "LOSS", cur_date, all_candles[i-1][0].time(),
                                direction_t, entry_price, closes[i-1],
                                round(pnl_pts, 2), round(pnl, 2), "EOD"))
            in_trade = False

        all_trades.extend(day_trades)
        day_trades = []
        day_trade_count = 0
        cur_date = ts_date
        cum_tpv = cum_v = 0.0

    # VWAP accumulate
    tp_price_vwap = (h + l + c) / 3
    cum_tpv += tp_price_vwap * vol
    cum_v   += vol
    vwap = cum_tpv / cum_v if cum_v > 0 else c

    # ── EOD forced exit ──────────────────────────────────────────────────────
    if ts_time >= dtime(EOD_HOUR, EOD_MIN):
        if in_trade:
            pnl_pts = (c - entry_price) if direction_t == "LONG" else (entry_price - c)
            pnl = pnl_pts * LOT_SIZE - BROKERAGE
            day_trades.append(("WIN" if pnl > 0 else "LOSS", ts_date, ts_time, direction_t, entry_price, c,
                                round(pnl_pts, 2), round(pnl, 2), "EOD"))
            in_trade = False
        continue

    # ── Manage open trade ────────────────────────────────────────────────────
    if in_trade:
        hit_tp = hit_sl = False
        exit_p = c
        if direction_t == "LONG":
            if h >= tp_price:   hit_tp = True; exit_p = tp_price
            elif l <= sl:       hit_sl = True; exit_p = sl
        else:
            if l <= tp_price:   hit_tp = True; exit_p = tp_price
            elif h >= sl:       hit_sl = True; exit_p = sl

        if hit_tp:
            pnl_pts = abs(exit_p - entry_price)
            pnl = pnl_pts * LOT_SIZE - BROKERAGE
            day_trades.append(("WIN", ts_date, ts_time, direction_t, entry_price, exit_p,
                                round(pnl_pts, 2), round(pnl, 2), "TARGET"))
            in_trade = False
        elif hit_sl:
            pnl_pts = -abs(exit_p - entry_price)
            pnl = pnl_pts * LOT_SIZE - BROKERAGE
            day_trades.append(("LOSS", ts_date, ts_time, direction_t, entry_price, exit_p,
                                round(pnl_pts, 2), round(pnl, 2), "SL"))
            in_trade = False
        continue

    if day_trade_count >= MAX_TRADES:
        continue

    # ── Signal logic ─────────────────────────────────────────────────────────
    e9, e21 = ema9_arr[i], ema21_arr[i]
    r        = rsi_arr[i]
    sd       = st_dir[i]
    at       = atr_vals[i]
    prev_sd  = st_dir[i-1]
    prev_e9  = ema9_arr[i-1]
    prev_e21 = ema21_arr[i-1]

    if None in (e9, e21, r, sd, at, prev_e9, prev_e21):
        continue

    st_flip_bull = sd == 1 and prev_sd == -1
    st_flip_bear = sd == -1 and prev_sd == 1
    ema_cross_bull = (e9 > e21) and (prev_e9 <= prev_e21)
    ema_cross_bear = (e9 < e21) and (prev_e9 >= prev_e21)

    # BUY signals (two types)
    # Type A: Supertrend flips bullish (strong reversal signal)
    # Type B: EMA crosses bullish while Supertrend already bullish (trend continuation)
    buy = ((st_flip_bull and 30 < r < 72) or
           (ema_cross_bull and sd == 1 and 35 < r < 68))

    # SELL signals
    sell = ((st_flip_bear and 28 < r < 70) or
            (ema_cross_bear and sd == -1 and 32 < r < 65))

    if buy and not in_trade:
        entry_price = c
        direction_t = "LONG"
        sl       = round(entry_price - SL_ATR_MULT * at, 2)
        tp_price = round(entry_price + TP_ATR_MULT * at, 2)
        in_trade = True
        day_trade_count += 1

    elif sell and not in_trade:
        entry_price = c
        direction_t = "SHORT"
        sl       = round(entry_price + SL_ATR_MULT * at, 2)
        tp_price = round(entry_price - TP_ATR_MULT * at, 2)
        in_trade = True
        day_trade_count += 1

# flush last day
if day_trades:
    all_trades.extend(day_trades)

# ── Results ───────────────────────────────────────────────────────────────────
if not all_trades:
    print("No trades generated.")
    sys.exit(0)

days_w_trades = sorted(set(t[1] for t in all_trades))
wins   = [t for t in all_trades if t[0] == "WIN"]
losses = [t for t in all_trades if t[0] == "LOSS"]
eods   = [t for t in all_trades if t[8] == "EOD"]   # time-based exits (counted in W/L)
total_pnl = sum(t[7] for t in all_trades)

day_pnls  = defaultdict(float)
for t in all_trades:
    day_pnls[t[1]] += t[7]
win_days  = sum(1 for v in day_pnls.values() if v > 0)

pf_win  = sum(t[7] for t in all_trades if t[7] > 0)
pf_loss = abs(sum(t[7] for t in all_trades if t[7] < 0))

all_dates = sorted(set(r[0].date() for r in all_candles))
print(f"\nData : {len(all_dates)} days ({all_dates[0]} to {all_dates[-1]})")
print(f"Strategy : Supertrend({ATR_PERIOD},{ST_MULT}) + EMA {EMA_FAST}/{EMA_SLOW} + RSI{RSI_PERIOD}")
print(f"Params   : SL={SL_ATR_MULT}x ATR | TP={TP_ATR_MULT}x ATR | Max {MAX_TRADES} trades/day | Lot={LOT_SIZE}")
print("=" * 75)

eod_w = [t for t in eods if t[0] == "WIN"]
eod_l = [t for t in eods if t[0] == "LOSS"]
print(f"\n  Total trades  : {len(all_trades)}  ({len(all_trades)/max(len(days_w_trades),1):.1f}/day on {len(days_w_trades)} active days)")
print(f"  Win/Loss      : {Fore.GREEN}{len(wins)}W{Style.RESET_ALL} / {Fore.RED}{len(losses)}L{Style.RESET_ALL}  (incl. {len(eod_w)}W+{len(eod_l)}L time exits)")
print(f"  Win rate      : {len(wins)/max(len(all_trades),1)*100:.1f}%")
print(f"  Win days      : {win_days}/{len(days_w_trades)}")
print(f"  Total P&L     : Rs.{total_pnl:,.0f}")
print(f"  Avg P&L/day   : Rs.{total_pnl/max(len(days_w_trades),1):,.0f}")
print(f"  Profit factor : {pf_win/pf_loss:.2f}" if pf_loss else "  Profit factor : inf")
print(f"  Best day      : Rs.{max(day_pnls.values()):,.0f}")
print(f"  Worst day     : Rs.{min(day_pnls.values()):,.0f}")

print(f"\n{'='*75}")
print(f"TRADE LOG")
print(f"{'='*75}")
print(f"{'DATE':<12} {'TIME':<8} {'DIR':<6} {'RES':<6} {'ENTRY':>8} {'EXIT':>8} {'PTS':>7} {'PNL':>10}  NOTE")
print(f"{'-'*75}")

cur_day = None
for t in sorted(all_trades, key=lambda x: (x[1], x[2])):
    result, day, tt, dirn, ep, xp, pts, pnl, note = t
    if day != cur_day:
        if cur_day is not None:
            dp = day_pnls[cur_day]
            dc = Fore.GREEN if dp > 0 else Fore.RED
            print(f"{'':57} {dc}{'[+]' if dp > 0 else '[-]'} Day Rs.{dp:>8,.0f}{Style.RESET_ALL}")
            print(f"{'-'*75}")
        cur_day = day
    is_win = result == "WIN"
    color  = Fore.GREEN if is_win else Fore.RED
    tag    = "WIN " if is_win else "LOSS"
    eod_flag = " [T]" if note == "EOD" else "    "
    print(f"{str(day):<12} {str(tt)[:5]:<8} {dirn:<6} {color}{tag}{Style.RESET_ALL} {ep:>8.2f} {xp:>8.2f} {color}{pts:>+7.1f} {pnl:>10,.0f}{Style.RESET_ALL}{eod_flag}")

if cur_day:
    dp = day_pnls[cur_day]
    dc = Fore.GREEN if dp > 0 else Fore.RED
    print(f"{'':57} {dc}{'[+]' if dp > 0 else '[-]'} Day Rs.{dp:>8,.0f}{Style.RESET_ALL}")

print(f"{'='*75}")
print(f"\n1 lot = {LOT_SIZE} barrels | 1 pt = Rs.{LOT_SIZE} | Brokerage Rs.{BROKERAGE}/trade")
