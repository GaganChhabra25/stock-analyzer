"""
CRUDEOIL Quantitative Research — Deep Edge Discovery
====================================================
Run on Contabo server:
  python3 scripts/crude_quant_research.py 2>&1 | tee /tmp/crude_research.txt

Covers:
  S1  Data inventory & quality
  S2  Hourly session profiling
  S3  Day-of-week analysis
  S4  Momentum persistence (multi-candle streaks)
  S5  Volatility regime classification
  S6  Opening range breakout
  S7  OI wall & PCR analysis
  S8  WTI vs MCX direction alignment
  S9  Large candle follow-through
  S10 VWAP deviation → 5-min forward move
  S11 Hour-to-hour momentum continuation
  S12 Wednesday EIA spike analysis
  S13 Wick rejection signals
  S14 Daily range distribution
  S15 Range compression → expansion edge
"""

import os
import sys
import statistics
import math
from pathlib import Path
from collections import defaultdict

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

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                        user=DB_USER, password=DB_PASS)
cur = conn.cursor()


# ── helpers ───────────────────────────────────────────────────────────────────

def sep(title):
    w = 72
    print(f"\n{'═'*w}")
    print(f"  {title}")
    print(f"{'═'*w}")


def pstats(values, label="", unit="pts"):
    if not values:
        print(f"  {label}: [no data]")
        return
    n = len(values)
    avg = sum(values) / n
    srt = sorted(values)
    med = srt[n // 2]
    std = statistics.stdev(values) if n > 1 else 0
    pos = sum(1 for v in values if v > 0)
    print(f"  {label}: n={n}  avg={avg:+.1f}{unit}  med={med:+.1f}  "
          f"std={std:.1f}  pos={pos}/{n}({pos/n*100:.0f}%)")


def calc_atr(highs, lows, closes, period=14):
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i-1]),
                       abs(lows[i] - closes[i-1])))
    atr = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        atr[i] = sum(trs[i-period+1:i+1]) / period
    return atr


def profit_factor(pnls):
    gross_win  = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    return gross_win / gross_loss if gross_loss > 0 else float('inf')


def max_drawdown(equity_curve):
    peak = -float('inf')
    dd = 0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = min(dd, v - peak)
    return abs(dd)


# ═════════════════════════════════════════════════════════════════════════════
# S1: DATA INVENTORY
# ═════════════════════════════════════════════════════════════════════════════
sep("S1: DATA INVENTORY")

cur.execute("""
    SELECT interval,
           COUNT(*)                                                     AS rows,
           MIN(ts AT TIME ZONE 'Asia/Kolkata')::date                   AS start_d,
           MAX(ts AT TIME ZONE 'Asia/Kolkata')::date                   AS end_d,
           COUNT(DISTINCT (ts AT TIME ZONE 'Asia/Kolkata')::date)      AS n_days,
           SUM(CASE WHEN volume IS NULL OR volume = 0 THEN 1 ELSE 0 END) AS zero_vol
    FROM mcx_ohlc
    WHERE instrument = 'CRUDEOIL'
    GROUP BY interval
    ORDER BY CASE interval
        WHEN '1second'  THEN 1 WHEN 'minute' THEN 2
        WHEN '15minute' THEN 3 WHEN 'day'    THEN 4 ELSE 5 END
""")
rows = cur.fetchall()
print(f"\n  MCX CRUDEOIL data layers:")
print(f"  {'Interval':<12} {'Rows':>10} {'Days':>6} {'Start':>12} {'End':>12} {'ZeroVol':>8}")
print(f"  {'-'*65}")
for r in rows:
    print(f"  {r[0]:<12} {r[1]:>10,} {r[4]:>6} {str(r[2]):>12} {str(r[3]):>12} {r[5]:>8,}")

cur.execute("""
    SELECT COUNT(*) AS rows,
           MIN(ts AT TIME ZONE 'Asia/Kolkata')::date AS start_d,
           MAX(ts AT TIME ZONE 'Asia/Kolkata')::date AS end_d,
           COUNT(DISTINCT trade_date)                AS n_days,
           SUM(CASE WHEN pcr IS NULL THEN 1 ELSE 0 END) AS null_pcr,
           SUM(CASE WHEN ms_call_oi_wall IS NULL THEN 1 ELSE 0 END) AS null_oi_wall
    FROM crudeoil_features
""")
r = cur.fetchone()
print(f"\n  crudeoil_features: {r[0]:,} rows | {r[3]} days | {r[1]} to {r[2]}")
print(f"    null PCR: {r[4]:,}  |  null OI wall: {r[5]:,}")

cur.execute("""
    SELECT symbol, COUNT(*), MIN(date), MAX(date)
    FROM global_prices GROUP BY symbol ORDER BY symbol
""")
print(f"\n  global_prices:")
for r in cur.fetchall():
    print(f"    {r[0]:<12} {r[1]:>6} rows | {r[2]} to {r[3]}")

cur.execute("""
    SELECT symbol, interval, COUNT(*), MIN(ts AT TIME ZONE 'Asia/Kolkata')::date, MAX(ts AT TIME ZONE 'Asia/Kolkata')::date
    FROM us_market GROUP BY symbol, interval ORDER BY symbol, interval
""")
print(f"\n  us_market:")
for r in cur.fetchall():
    print(f"    {r[0]:<8} {r[1]:<10} {r[2]:>6} rows | {r[3]} to {r[4]}")

# Duplicate check
cur.execute("""
    SELECT COUNT(*) FROM (
        SELECT ts, instrument, interval, COUNT(*) AS cnt
        FROM mcx_ohlc
        WHERE instrument='CRUDEOIL'
        GROUP BY ts, instrument, interval HAVING COUNT(*) > 1
    ) x
""")
dups = cur.fetchone()[0]
print(f"\n  Duplicate rows in mcx_ohlc: {dups}")

# Data gap check (1-min: missing dates in trading range)
cur.execute("""
    SELECT COUNT(DISTINCT (ts AT TIME ZONE 'Asia/Kolkata')::date)
    FROM mcx_ohlc WHERE instrument='CRUDEOIL' AND interval='minute'
""")
n_days_min = cur.fetchone()[0]
print(f"  Distinct trading dates in 1-min data: {n_days_min}")


# ═════════════════════════════════════════════════════════════════════════════
# S2: HOURLY SESSION PROFILING (1-min data)
# ═════════════════════════════════════════════════════════════════════════════
sep("S2: HOURLY SESSION PROFILING  (1-min candles, IST)")

cur.execute("""
    WITH hourly AS (
        SELECT
            EXTRACT(HOUR FROM ts AT TIME ZONE 'Asia/Kolkata')::int AS hr,
            (ts AT TIME ZONE 'Asia/Kolkata')::date                AS d,
            MAX(high) - MIN(low)                                  AS hr_range,
            (array_agg(close ORDER BY ts DESC))[1]
            - (array_agg(close ORDER BY ts))[1]                   AS hr_move,
            SUM(COALESCE(volume,0))                               AS hr_vol
        FROM mcx_ohlc
        WHERE instrument='CRUDEOIL' AND interval='minute'
        GROUP BY hr, d
    )
    SELECT hr,
           COUNT(*)                                              AS n,
           ROUND(AVG(hr_range)::numeric,  1)                    AS avg_range,
           ROUND(AVG(hr_move)::numeric,   1)                    AS avg_move,
           ROUND(STDDEV(hr_move)::numeric,1)                    AS std_move,
           SUM(CASE WHEN hr_move > 0 THEN 1 ELSE 0 END)         AS bull_hrs,
           ROUND(AVG(hr_vol)::numeric, 0)                       AS avg_vol
    FROM hourly
    GROUP BY hr
    ORDER BY hr
""")
hr_rows = cur.fetchall()

print(f"\n  {'Hr(IST)':<8} {'N':>5} {'AvgRng':>8} {'AvgMv':>8} {'Std':>7} {'Bull%':>7} "
      f"{'AvgVol':>10}  Session")
print(f"  {'-'*70}")

for r in hr_rows:
    hr = r[0]
    if hr < 9:
        continue
    n = r[1]
    bull_pct = float(r[5]) / n * 100 if n else 0
    session = ""
    if 9 <= hr <= 11:   session = "MCX-MORNING"
    elif 14 <= hr <= 16: session = "EUROPEAN-CLOSE"
    elif 19 <= hr <= 20: session = "US-OPEN"
    elif 20 <= hr <= 22: session = "US-MAIN / EIA"
    elif hr == 23:       session = "MCX-CLOSE"
    print(f"  {hr:02d}:00    {n:>5} {str(r[2]):>8} {str(r[3]):>+8} {str(r[4]):>7} "
          f"{bull_pct:>6.0f}% {str(r[6]):>10}  {session}")


# ═════════════════════════════════════════════════════════════════════════════
# S3: DAY-OF-WEEK ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════
sep("S3: DAY-OF-WEEK STATISTICAL ANALYSIS")

cur.execute("""
    WITH daily AS (
        SELECT
            (ts AT TIME ZONE 'Asia/Kolkata')::date              AS d,
            EXTRACT(DOW FROM ts AT TIME ZONE 'Asia/Kolkata')::int AS dow,
            (array_agg(open  ORDER BY ts))[1]                   AS day_open,
            (array_agg(close ORDER BY ts DESC))[1]              AS day_close,
            MAX(high) AS day_high,
            MIN(low)  AS day_low,
            SUM(COALESCE(volume,0)) AS day_vol
        FROM mcx_ohlc
        WHERE instrument='CRUDEOIL' AND interval='minute'
        GROUP BY d, dow
    )
    SELECT dow,
           COUNT(*)                                                  AS n,
           ROUND(AVG(day_high  - day_low)::numeric,  1)             AS avg_range,
           ROUND(STDDEV(day_high - day_low)::numeric, 1)            AS std_range,
           ROUND(AVG(day_close - day_open)::numeric,  1)            AS avg_net,
           ROUND(STDDEV(day_close - day_open)::numeric, 1)          AS std_net,
           SUM(CASE WHEN day_close > day_open THEN 1 ELSE 0 END)    AS bull_days,
           MAX(day_high - day_low)                                   AS max_range,
           ROUND(AVG(day_vol)::numeric, 0)                          AS avg_vol
    FROM daily
    WHERE dow BETWEEN 1 AND 5
    GROUP BY dow
    ORDER BY dow
""")
dow_rows = cur.fetchall()

dow_names = {1: 'Mon', 2: 'Tue', 3: 'Wed*', 4: 'Thu', 5: 'Fri'}
print(f"\n  {'Day':<5} {'N':>4} {'AvgRng':>8} {'StdRng':>8} {'AvgNet':>8} {'Bull%':>7} {'MaxRng':>8}")
print(f"  {'-'*55}")
for r in dow_rows:
    dow = r[0]
    n   = r[1]
    bull_pct = float(r[6]) / n * 100 if n else 0
    eia = " <<EIA" if dow == 3 else ""
    print(f"  {dow_names[dow]:<5} {n:>4} {str(r[2]):>8} {str(r[3]):>8} {str(r[4]):>+8} "
          f"{bull_pct:>6.0f}% {float(r[7]):>8.0f}{eia}")

print(f"\n  * Wednesday has EIA crude inventory report (typically 20:00 IST)")


# ═════════════════════════════════════════════════════════════════════════════
# S4: MOMENTUM PERSISTENCE  (15-min candles)
# ═════════════════════════════════════════════════════════════════════════════
sep("S4: MOMENTUM PERSISTENCE  (15-min candles)")

cur.execute("""
    SELECT (ts AT TIME ZONE 'Asia/Kolkata') AS ts_ist, open, high, low, close
    FROM mcx_ohlc
    WHERE instrument='CRUDEOIL' AND interval='15minute'
    ORDER BY ts
""")
c15 = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in cur.fetchall()]
print(f"  15-min candles loaded: {len(c15)}")

if c15:
    def momentum_persistence(candles, streak):
        bull_cont = bull_rev = bear_cont = bear_rev = 0
        for i in range(streak, len(candles) - 1):
            all_bull = all(candles[i-j][4] > candles[i-j][1] for j in range(streak))
            all_bear = all(candles[i-j][4] < candles[i-j][1] for j in range(streak))
            next_bull = candles[i+1][4] > candles[i+1][1]
            if all_bull:
                if next_bull: bull_cont += 1
                else: bull_rev += 1
            elif all_bear:
                if not next_bull: bear_cont += 1
                else: bear_rev += 1
        return bull_cont, bull_rev, bear_cont, bear_rev

    print(f"\n  After N consecutive same-direction 15-min candles → next candle:")
    print(f"  {'N':<3} {'BullCont%':>10} {'BullRev%':>10} {'BearCont%':>10} {'BearRev%':>10}  "
          f"{'Sample':>8}  Verdict")
    print(f"  {'-'*72}")
    for n in [2, 3, 4, 5, 6]:
        bc, br, beac, bear_r = momentum_persistence(c15, n)
        bt = bc + br
        beart = beac + bear_r
        if bt == 0 and beart == 0:
            continue
        bc_pct   = bc   / bt    * 100 if bt    > 0 else 0
        beac_pct = beac / beart * 100 if beart > 0 else 0
        avg_cont = (bc + beac) / max(bt + beart, 1) * 100
        verdict = "TREND" if avg_cont > 55 else ("REVERSAL" if avg_cont < 45 else "RANDOM")
        print(f"  {n:<3} {bc_pct:>9.1f}% {100-bc_pct:>9.1f}% {beac_pct:>9.1f}% "
              f"{100-beac_pct:>9.1f}% {bt+beart:>8}  {verdict}")


# ═════════════════════════════════════════════════════════════════════════════
# S5: VOLATILITY REGIME CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════════════
sep("S5: VOLATILITY REGIME CLASSIFICATION  (14-day ATR)")

cur.execute("""
    WITH daily AS (
        SELECT
            (ts AT TIME ZONE 'Asia/Kolkata')::date              AS d,
            MAX(high) AS dh, MIN(low) AS dl,
            (array_agg(close ORDER BY ts DESC))[1]              AS dc,
            (array_agg(close ORDER BY ts))[1]                   AS do_
        FROM mcx_ohlc
        WHERE instrument='CRUDEOIL' AND interval='minute'
        GROUP BY d
        ORDER BY d
    ),
    atr14 AS (
        SELECT d, dh - dl AS daily_range, dc - do_ AS daily_net, dc, do_,
               AVG(dh - dl) OVER (ORDER BY d ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS atr
        FROM daily
    )
    SELECT d, ROUND(daily_range::numeric) AS range_pts,
           ROUND(daily_net::numeric)       AS net_pts,
           ROUND(atr::numeric, 1)          AS atr14,
           ROUND((daily_range / NULLIF(atr, 0))::numeric, 2) AS range_vs_atr,
           CASE
               WHEN daily_range > atr * 1.8 THEN 'HIGH_VOL'
               WHEN daily_range < atr * 0.5 THEN 'LOW_VOL'
               ELSE 'NORMAL'
           END AS regime
    FROM atr14
    WHERE atr IS NOT NULL
    ORDER BY d
""")
vol_rows = cur.fetchall()

regime_stats = defaultdict(lambda: {"ranges": [], "nets": [], "bull": 0})
for r in vol_rows:
    reg = r[5]
    rng = float(r[1]) if r[1] else 0
    net = float(r[2]) if r[2] else 0
    regime_stats[reg]["ranges"].append(rng)
    regime_stats[reg]["nets"].append(net)
    if net > 0:
        regime_stats[reg]["bull"] += 1

print(f"\n  {'Regime':<10} {'Days':>5} {'AvgRange':>9} {'AvgNet':>8} {'Bull%':>7} {'MaxRange':>9}")
print(f"  {'-'*55}")
for reg in ["LOW_VOL", "NORMAL", "HIGH_VOL"]:
    if reg not in regime_stats: continue
    d = regime_stats[reg]
    rng = d["ranges"]
    nets = d["nets"]
    n = len(rng)
    bull_pct = d["bull"] / n * 100 if n else 0
    avg_rng = sum(rng) / n if n else 0
    avg_net = sum(nets) / n if n else 0
    max_rng = max(rng) if rng else 0
    print(f"  {reg:<10} {n:>5} {avg_rng:>9.0f} {avg_net:>+8.0f} {bull_pct:>6.0f}% {max_rng:>9.0f}")

# ATR range context
all_atrs = [float(r[3]) for r in vol_rows if r[3]]
if all_atrs:
    print(f"\n  ATR-14 (daily): min={min(all_atrs):.0f}  avg={sum(all_atrs)/len(all_atrs):.0f}  "
          f"max={max(all_atrs):.0f}")


# ═════════════════════════════════════════════════════════════════════════════
# S6: OPENING RANGE BREAKOUT  (first 30 min: 9:00–9:29 IST)
# ═════════════════════════════════════════════════════════════════════════════
sep("S6: OPENING RANGE BREAKOUT  (9:00–9:29 IST → rest of day)")

cur.execute("""
    WITH src AS (
        SELECT
            (ts AT TIME ZONE 'Asia/Kolkata')::date                     AS d,
            EXTRACT(HOUR   FROM ts AT TIME ZONE 'Asia/Kolkata')::int   AS hr,
            EXTRACT(MINUTE FROM ts AT TIME ZONE 'Asia/Kolkata')::int   AS mn,
            open, high, low, close
        FROM mcx_ohlc
        WHERE instrument='CRUDEOIL' AND interval='minute'
    ),
    opening AS (
        SELECT d,
               MAX(high) AS or_high, MIN(low) AS or_low,
               (array_agg(open ORDER BY hr, mn))[1] AS or_open
        FROM src
        WHERE hr * 60 + mn < 570   -- before 9:30 IST
        GROUP BY d
    ),
    rest AS (
        SELECT d,
               MAX(high)                                           AS rest_high,
               MIN(low)                                            AS rest_low,
               (array_agg(close ORDER BY hr DESC, mn DESC))[1]    AS day_close
        FROM src
        WHERE hr * 60 + mn >= 570  -- 9:30 IST onwards
        GROUP BY d
    )
    SELECT o.d,
           ROUND(o.or_high - o.or_low)          AS or_range,
           ROUND(o.or_high)                      AS or_h,
           ROUND(o.or_low)                       AS or_l,
           ROUND(r.day_close)                    AS day_close,
           ROUND(r.rest_high - r.rest_low)       AS rest_range,
           CASE
               WHEN r.day_close > o.or_high THEN 'BULL_BO'
               WHEN r.day_close < o.or_low  THEN 'BEAR_BO'
               ELSE 'INSIDE'
           END AS outcome,
           CASE
               WHEN r.rest_high > o.or_high AND r.rest_low >= o.or_low THEN 'ONLY_UPSIDE'
               WHEN r.rest_low  < o.or_low  AND r.rest_high <= o.or_high THEN 'ONLY_DOWNSIDE'
               WHEN r.rest_high > o.or_high AND r.rest_low < o.or_low  THEN 'BOTH_SIDES'
               ELSE 'INSIDE'
           END AS breakout_side
    FROM opening o
    JOIN rest r USING (d)
    WHERE o.or_high > o.or_low
    ORDER BY o.d
""")
orb_rows = cur.fetchall()

if orb_rows:
    n = len(orb_rows)
    outcomes = defaultdict(int)
    sides = defaultdict(int)
    or_ranges = []
    for r in orb_rows:
        outcomes[r[6]] += 1
        sides[r[7]] += 1
        if r[1]: or_ranges.append(float(r[1]))

    print(f"\n  Total ORB days: {n}")
    if or_ranges:
        print(f"  OR size: avg={sum(or_ranges)/len(or_ranges):.0f}  "
              f"med={sorted(or_ranges)[len(or_ranges)//2]:.0f}  "
              f"max={max(or_ranges):.0f} pts")

    print(f"\n  Day-close vs OR (breakout direction):")
    for outcome, cnt in sorted(outcomes.items()):
        print(f"    {outcome:<10}: {cnt:>3}/{n} = {cnt/n*100:.0f}%")

    print(f"\n  Intraday OR break side (whether price touches each side):")
    for side, cnt in sorted(sides.items()):
        print(f"    {side:<14}: {cnt:>3}/{n} = {cnt/n*100:.0f}%")


# ═════════════════════════════════════════════════════════════════════════════
# S7: OI WALL & PCR ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════
sep("S7: OI WALL (SUPPORT/RESISTANCE) & PCR ANALYSIS")

cur.execute("""
    WITH daily AS (
        SELECT
            trade_date,
            AVG(pcr)                                              AS avg_pcr,
            MAX(ms_call_oi_wall)                                  AS call_wall,
            MAX(ms_put_oi_wall)                                   AS put_wall,
            MAX(ms_expected_move)                                 AS exp_move,
            MAX(ms_atm_straddle)                                  AS straddle,
            MIN(underlying_ltp)                                   AS day_low,
            MAX(underlying_ltp)                                   AS day_high,
            (array_agg(underlying_ltp ORDER BY ts))[1]           AS day_open,
            (array_agg(underlying_ltp ORDER BY ts DESC))[1]      AS day_close
        FROM crudeoil_features
        WHERE ms_call_oi_wall IS NOT NULL
        GROUP BY trade_date
        ORDER BY trade_date
    )
    SELECT trade_date,
           ROUND(avg_pcr::numeric, 3)                            AS pcr,
           ROUND(call_wall)                                      AS call_wall,
           ROUND(put_wall)                                       AS put_wall,
           ROUND(exp_move, 1)                                    AS exp_move,
           ROUND(straddle, 0)                                    AS straddle,
           ROUND(day_high - day_low, 0)                         AS actual_range,
           ROUND((day_high - day_low) / NULLIF(exp_move,0), 2)  AS ratio,
           day_high >= call_wall                                  AS call_broke,
           day_low  <= put_wall                                   AS put_broke,
           ROUND(day_close - day_open)                           AS net_move,
           ROUND(day_high)                                       AS day_high,
           ROUND(day_low)                                        AS day_low
    FROM daily
""")
oi_rows = cur.fetchall()

if oi_rows:
    n = len(oi_rows)
    call_held = sum(1 for r in oi_rows if not r[8])
    put_held  = sum(1 for r in oi_rows if not r[9])

    print(f"\n  OI Wall Effectiveness (N={n} days with options data):")
    print(f"    Call wall HELD as resistance: {call_held}/{n} = {call_held/n*100:.0f}%")
    print(f"    Put wall  HELD as support:   {put_held}/{n}  = {put_held/n*100:.0f}%")

    # PCR analysis
    pcr_vals = [(float(r[1]), float(r[10])) for r in oi_rows if r[1] and r[10] is not None]
    if pcr_vals:
        # Bin PCR into zones
        bull_extreme = [(pcr, net) for pcr, net in pcr_vals if pcr < 0.4]
        mild_bull    = [(pcr, net) for pcr, net in pcr_vals if 0.4 <= pcr < 0.7]
        neutral      = [(pcr, net) for pcr, net in pcr_vals if 0.7 <= pcr < 0.9]
        mild_bear    = [(pcr, net) for pcr, net in pcr_vals if 0.9 <= pcr < 1.1]
        bear_extreme = [(pcr, net) for pcr, net in pcr_vals if pcr >= 1.1]

        print(f"\n  PCR zone → intraday net move (day_close - day_open):")
        print(f"  {'PCR Zone':<20} {'N':>4} {'AvgNet':>8} {'Bull%':>7}  Implication")
        print(f"  {'-'*60}")
        for label, zone, impl in [
            ("< 0.4  (bull extreme)", bull_extreme, "Bearish reversal risk"),
            ("0.4–0.7 (mild bull)", mild_bull, "Bullish bias"),
            ("0.7–0.9 (neutral)",   neutral,   "Choppy/range"),
            ("0.9–1.1 (mild bear)", mild_bear, "Bearish bias"),
            (">= 1.1 (bear extreme)", bear_extreme, "Bullish reversal risk"),
        ]:
            if not zone: continue
            nets = [z[1] for z in zone]
            avg_net = sum(nets) / len(nets)
            bull_pct = sum(1 for m in nets if m > 0) / len(nets) * 100
            print(f"  {label:<20} {len(zone):>4} {avg_net:>+8.0f} {bull_pct:>6.0f}%  {impl}")

    # Expected move analysis
    ratios = [float(r[7]) for r in oi_rows if r[7]]
    if ratios:
        above_exp = sum(1 for r in ratios if r > 1.0)
        below_exp = sum(1 for r in ratios if r < 0.8)
        print(f"\n  Straddle vs Actual Range:")
        print(f"    Avg actual/expected ratio: {sum(ratios)/len(ratios):.2f}x")
        print(f"    Days > expected (straddle sellers lose): {above_exp}/{len(ratios)} = {above_exp/len(ratios)*100:.0f}%")
        print(f"    Days < 0.8x (straddle sellers profit):  {below_exp}/{len(ratios)} = {below_exp/len(ratios)*100:.0f}%")
        srt = sorted(ratios)
        print(f"    P10={srt[len(srt)//10]:.2f}  P25={srt[len(srt)//4]:.2f}  "
              f"P50={srt[len(srt)//2]:.2f}  P75={srt[3*len(srt)//4]:.2f}  P90={srt[9*len(srt)//10]:.2f}")

    # Print daily table
    print(f"\n  {'Date':<12} {'PCR':>6} {'CallWall':>9} {'PutWall':>8} {'ExpMv':>7} "
          f"{'ActRng':>7} {'A/E':>5} {'Net':>+7} {'CWall':>6} {'PWall':>6}")
    print(f"  {'-'*80}")
    for r in oi_rows:
        cw_s = 'BROKE' if r[8] else 'held '
        pw_s = 'BROKE' if r[9] else 'held '
        print(f"  {str(r[0]):<12} {str(r[1]):>6} {str(r[2]):>9} {str(r[3]):>8} "
              f"{str(r[4]):>7} {str(r[6]):>7} {str(r[7]):>5} {float(r[10]):>+7.0f} "
              f"{cw_s:>6} {pw_s:>6}")


# ═════════════════════════════════════════════════════════════════════════════
# S8: WTI vs MCX DIRECTION ALIGNMENT
# ═════════════════════════════════════════════════════════════════════════════
sep("S8: WTI vs MCX CRUDEOIL DIRECTION ALIGNMENT")

cur.execute("""
    WITH mcx_day AS (
        SELECT
            (ts AT TIME ZONE 'Asia/Kolkata')::date              AS d,
            (array_agg(open  ORDER BY ts))[1]                   AS o,
            (array_agg(close ORDER BY ts DESC))[1]              AS c,
            MAX(high) AS h, MIN(low) AS l
        FROM mcx_ohlc
        WHERE instrument='CRUDEOIL' AND interval='day'
        GROUP BY 1
    ),
    wti AS (
        SELECT date AS d, close,
               LAG(close,1) OVER (ORDER BY date) AS prev_close,
               change_pct
        FROM global_prices WHERE symbol='WTI'
    ),
    usdinr AS (
        SELECT date AS d, close AS fx,
               LAG(close,1) OVER (ORDER BY date) AS fx_prev
        FROM global_prices WHERE symbol='USDINR'
    )
    SELECT m.d,
           ROUND(m.c - m.o)                                     AS mcx_net,
           ROUND(m.h - m.l)                                     AS mcx_range,
           CASE WHEN m.c > m.o THEN 1 ELSE -1 END               AS mcx_dir,
           ROUND((w.close - w.prev_close)::numeric, 2)           AS wti_chg,
           CASE WHEN w.close > w.prev_close THEN 1 ELSE -1 END   AS wti_dir,
           ROUND((u.fx - u.fx_prev)::numeric, 4)                 AS fx_chg
    FROM mcx_day m
    LEFT JOIN wti    w ON m.d = w.d + 1
    LEFT JOIN usdinr u ON m.d = u.d + 1
    WHERE w.close IS NOT NULL AND w.prev_close IS NOT NULL
    ORDER BY m.d
""")
corr_rows = cur.fetchall()

if corr_rows:
    n = len(corr_rows)
    aligned = sum(1 for r in corr_rows if r[3] == r[5])

    wti_up_days = [r for r in corr_rows if r[5] == 1]
    wti_dn_days = [r for r in corr_rows if r[5] == -1]
    wti_up_mcx_up = sum(1 for r in wti_up_days if r[3] == 1)
    wti_dn_mcx_dn = sum(1 for r in wti_dn_days if r[3] == -1)

    div_days = [r for r in corr_rows if r[3] != r[5]]
    align_days = [r for r in corr_rows if r[3] == r[5]]

    print(f"\n  Total days with WTI data: {n}")
    print(f"  Overall alignment rate:  {aligned}/{n} = {aligned/n*100:.0f}%")
    print(f"\n  WTI UP  → MCX UP:   {wti_up_mcx_up}/{len(wti_up_days)} = {wti_up_mcx_up/max(len(wti_up_days),1)*100:.0f}%")
    print(f"  WTI DN  → MCX DN:   {wti_dn_mcx_dn}/{len(wti_dn_days)} = {wti_dn_mcx_dn/max(len(wti_dn_days),1)*100:.0f}%")

    if div_days:
        div_nets = [float(r[1]) for r in div_days if r[1] is not None]
        print(f"\n  Divergence days ({len(div_days)} total):")
        if div_nets:
            print(f"    Avg MCX net move on divergence: {sum(div_nets)/len(div_nets):+.0f} pts")
            print(f"    Divergence days: MCX bull {sum(1 for v in div_nets if v>0)}/{len(div_nets)}")

    if align_days:
        aln_nets = [float(r[1]) for r in align_days if r[1] is not None]
        print(f"\n  Aligned days ({len(align_days)} total):")
        if aln_nets:
            print(f"    Avg MCX net move on alignment: {sum(aln_nets)/len(aln_nets):+.0f} pts")
            print(f"    Aligned days: MCX bull {sum(1 for v in aln_nets if v>0)}/{len(aln_nets)}")

    # FX impact: when USDINR rises, MCX crude rises (imports cost more)
    fx_data = [(float(r[6]), float(r[1])) for r in corr_rows if r[6] and r[1]]
    if fx_data:
        fx_up_mcx_up = sum(1 for fx, net in fx_data if fx > 0 and net > 0)
        fx_up_days   = sum(1 for fx, net in fx_data if fx > 0)
        print(f"\n  USDINR UP → MCX UP (currency effect): {fx_up_mcx_up}/{fx_up_days} = {fx_up_mcx_up/max(fx_up_days,1)*100:.0f}%")


# ═════════════════════════════════════════════════════════════════════════════
# S9: LARGE CANDLE FOLLOW-THROUGH  (15-min)
# ═════════════════════════════════════════════════════════════════════════════
sep("S9: LARGE CANDLE FOLLOW-THROUGH  (15-min, body > N×ATR)")

if c15:
    opens_15  = [r[1] for r in c15]
    highs_15  = [r[2] for r in c15]
    lows_15   = [r[3] for r in c15]
    closes_15 = [r[4] for r in c15]
    atr_15 = calc_atr(highs_15, lows_15, closes_15, 14)

    print(f"\n  {'Mult':<6} {'LgBull→Bull':>12} {'LgBull→Bear':>12} {'LgBear→Bear':>12} {'LgBear→Bull':>12}")
    print(f"  {'-'*58}")

    for mult in [1.0, 1.5, 2.0]:
        lb_cont = lb_rev = ls_cont = ls_rev = 0
        for i in range(14, len(c15) - 1):
            if atr_15[i] is None: continue
            body = closes_15[i] - opens_15[i]
            next_body = closes_15[i+1] - opens_15[i+1]
            atr = atr_15[i]
            if body > mult * atr:
                if next_body > 0: lb_cont += 1
                else: lb_rev += 1
            elif body < -mult * atr:
                if next_body < 0: ls_cont += 1
                else: ls_rev += 1
        bt = lb_cont + lb_rev
        st = ls_cont + ls_rev
        if bt == 0 and st == 0: continue
        lb_pct = lb_cont/bt*100 if bt else 0
        ls_pct = ls_cont/st*100 if st else 0
        print(f"  >{mult:.1f}x  {lb_cont:>5}/{bt:<5}({lb_pct:.0f}%) "
              f"{lb_rev:>5}/{bt:<5}({100-lb_pct:.0f}%) "
              f"{ls_cont:>5}/{st:<5}({ls_pct:.0f}%) "
              f"{ls_rev:>5}/{st:<5}({100-ls_pct:.0f}%)")

    # Large candle 2-bar forward
    print(f"\n  After large candle (>1.5×ATR), how does price behave 2 candles later?")
    lb_net_2 = []
    ls_net_2 = []
    for i in range(14, len(c15) - 2):
        if atr_15[i] is None: continue
        body = closes_15[i] - opens_15[i]
        atr = atr_15[i]
        move_2 = closes_15[i+2] - closes_15[i]
        if body > 1.5 * atr:
            lb_net_2.append(move_2)
        elif body < -1.5 * atr:
            ls_net_2.append(move_2)
    if lb_net_2:
        pstats(lb_net_2, "Large bull → +2 candle net")
    if ls_net_2:
        pstats(ls_net_2, "Large bear → +2 candle net")


# ═════════════════════════════════════════════════════════════════════════════
# S10: VWAP DEVIATION → FORWARD MOVE
# ═════════════════════════════════════════════════════════════════════════════
sep("S10: VWAP DEVIATION → 5-MIN FORWARD MOVE")

cur.execute("""
    WITH src AS (
        SELECT
            (ts AT TIME ZONE 'Asia/Kolkata')::date                     AS d,
            (ts AT TIME ZONE 'Asia/Kolkata')                           AS ts_ist,
            (high + low + close) / 3                                   AS tp,
            COALESCE(volume, 1)                                        AS vol,
            close
        FROM mcx_ohlc
        WHERE instrument='CRUDEOIL' AND interval='minute'
    ),
    vwap AS (
        SELECT d, ts_ist, close,
               SUM(tp * vol) OVER (PARTITION BY d ORDER BY ts_ist)
               / NULLIF(SUM(vol) OVER (PARTITION BY d ORDER BY ts_ist), 0)  AS vwap,
               STDDEV(close) OVER (PARTITION BY d ORDER BY ts_ist
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)         AS vwap_sd,
               LEAD(close, 5) OVER (PARTITION BY d ORDER BY ts_ist)          AS c5
        FROM src
    )
    SELECT
        CASE
            WHEN vwap_sd > 0 AND (close-vwap)/vwap_sd >  2 THEN 'ABOVE_2SD'
            WHEN vwap_sd > 0 AND (close-vwap)/vwap_sd >  1 THEN 'ABOVE_1SD'
            WHEN vwap_sd > 0 AND (close-vwap)/vwap_sd < -2 THEN 'BELOW_2SD'
            WHEN vwap_sd > 0 AND (close-vwap)/vwap_sd < -1 THEN 'BELOW_1SD'
            ELSE 'NEAR_VWAP'
        END                                          AS zone,
        COUNT(*)                                     AS n,
        SUM(CASE WHEN c5 < close THEN 1 ELSE 0 END) AS fell,
        SUM(CASE WHEN c5 > close THEN 1 ELSE 0 END) AS rose,
        ROUND(AVG(c5 - close)::numeric, 2)           AS avg_5m_chg
    FROM vwap
    WHERE c5 IS NOT NULL AND vwap_sd > 5
    GROUP BY zone
    ORDER BY zone
""")
vwap_rows = cur.fetchall()

if vwap_rows:
    print(f"\n  VWAP deviation zone → 5-min forward move (VWAP SD filter > 5 pts):")
    print(f"  {'Zone':<12} {'N':>8} {'Fell%':>7} {'Rose%':>7} {'Avg5mChg':>9}  Edge")
    print(f"  {'-'*55}")
    for r in vwap_rows:
        n = r[1]
        fell_pct = r[2]/n*100 if n else 0
        rose_pct = r[3]/n*100 if n else 0
        avg5m = float(r[4]) if r[4] else 0
        edge = ""
        if r[0] == 'ABOVE_2SD' and fell_pct > 57: edge = "  SHORT EDGE"
        if r[0] == 'BELOW_2SD' and rose_pct > 57: edge = "  LONG EDGE"
        if r[0] == 'ABOVE_1SD' and fell_pct > 54: edge = "  Soft short"
        if r[0] == 'BELOW_1SD' and rose_pct > 54: edge = "  Soft long"
        print(f"  {r[0]:<12} {n:>8,} {fell_pct:>6.1f}% {rose_pct:>6.1f}% {avg5m:>+9.2f}{edge}")


# ═════════════════════════════════════════════════════════════════════════════
# S11: HOUR-TO-HOUR MOMENTUM CONTINUATION
# ═════════════════════════════════════════════════════════════════════════════
sep("S11: HOUR-TO-HOUR MOMENTUM CONTINUATION")

cur.execute("""
    WITH hourly AS (
        SELECT
            (ts AT TIME ZONE 'Asia/Kolkata')::date                     AS d,
            EXTRACT(HOUR FROM ts AT TIME ZONE 'Asia/Kolkata')::int     AS hr,
            (array_agg(close ORDER BY ts DESC))[1]
            - (array_agg(open ORDER BY ts))[1]                         AS hr_move,
            MAX(high) - MIN(low)                                        AS hr_range
        FROM mcx_ohlc
        WHERE instrument='CRUDEOIL' AND interval='minute'
        GROUP BY d, hr
    )
    SELECT h1.hr,
           COUNT(*)                                                          AS n,
           SUM(CASE WHEN SIGN(h1.hr_move) = SIGN(h2.hr_move) THEN 1 END)   AS same_dir,
           ROUND(AVG(h2.hr_range)::numeric, 1)                              AS avg_next_rng,
           ROUND(AVG(ABS(h2.hr_move))::numeric, 1)                          AS avg_next_move
    FROM hourly h1
    JOIN hourly h2 ON h1.d = h2.d AND h2.hr = h1.hr + 1
    WHERE ABS(h1.hr_move) > 10   -- filter near-flat hours
    GROUP BY h1.hr
    HAVING COUNT(*) >= 10
    ORDER BY h1.hr
""")
cont_rows = cur.fetchall()

print(f"\n  Hour → next-hour continuation (only hours with |move|>10pts, N≥10):")
print(f"  {'Hr':>4} {'N':>4} {'SameDir%':>9} {'AvgNextRng':>11} {'AvgNextMv':>10}  Verdict")
print(f"  {'-'*55}")
for r in cont_rows:
    hr = r[0]
    if hr < 9: continue
    n = r[1]
    same_pct = float(r[2]) / n * 100 if r[2] and n else 0
    verdict = "TREND" if same_pct > 58 else ("REVERSAL" if same_pct < 42 else "~random")
    print(f"  {hr:>4}:00 {n:>4} {same_pct:>8.1f}% {str(r[3]):>11} {str(r[4]):>10}  {verdict}")


# ═════════════════════════════════════════════════════════════════════════════
# S12: WEDNESDAY EIA SPIKE ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════
sep("S12: WEDNESDAY EIA INVENTORY — SESSION BREAKDOWN")

cur.execute("""
    WITH daily AS (
        SELECT
            (ts AT TIME ZONE 'Asia/Kolkata')::date                     AS d,
            EXTRACT(DOW FROM ts AT TIME ZONE 'Asia/Kolkata')::int      AS dow,
            EXTRACT(HOUR FROM ts AT TIME ZONE 'Asia/Kolkata')::int     AS hr,
            EXTRACT(MINUTE FROM ts AT TIME ZONE 'Asia/Kolkata')::int   AS mn,
            open, high, low, close, COALESCE(volume,0) AS vol
        FROM mcx_ohlc
        WHERE instrument='CRUDEOIL' AND interval='minute'
    ),
    sessions AS (
        SELECT d, dow,
               MAX(high) - MIN(low)                                                         AS full_range,
               (array_agg(close ORDER BY hr DESC, mn DESC))[1]
               - (array_agg(open ORDER BY hr, mn))[1]                                       AS full_net,
               -- Morning 9–11
               MAX(CASE WHEN hr BETWEEN 9 AND 10  THEN high END)
               - MIN(CASE WHEN hr BETWEEN 9 AND 10  THEN low  END)                          AS morn_range,
               -- US open 19-20
               MAX(CASE WHEN hr BETWEEN 19 AND 20 THEN high END)
               - MIN(CASE WHEN hr BETWEEN 19 AND 20 THEN low  END)                          AS us_open_range,
               -- EIA window 20-21
               MAX(CASE WHEN hr BETWEEN 20 AND 21 THEN high END)
               - MIN(CASE WHEN hr BETWEEN 20 AND 21 THEN low  END)                          AS eia_range,
               (array_agg(CASE WHEN hr=20 AND mn=0 THEN close END ORDER BY hr, mn))[1]     AS eia_open,
               (array_agg(CASE WHEN hr=21 THEN close END ORDER BY hr DESC, mn DESC))[1]    AS eia_close,
               SUM(CASE WHEN hr BETWEEN 20 AND 21 THEN vol ELSE 0 END)                     AS eia_vol
        FROM daily
        WHERE dow = 3
        GROUP BY d, dow
    )
    SELECT d,
           ROUND(full_range)    AS full_rng,
           ROUND(full_net)      AS full_net,
           ROUND(morn_range)    AS morn_rng,
           ROUND(us_open_range) AS us_open_rng,
           ROUND(eia_range)     AS eia_rng,
           ROUND(eia_close - eia_open) AS eia_net,
           eia_vol
    FROM sessions
    WHERE full_range > 50
    ORDER BY d
""")
wed_rows = cur.fetchall()

if wed_rows:
    n = len(wed_rows)
    full_rngs  = [float(r[1]) for r in wed_rows if r[1]]
    full_nets  = [float(r[2]) for r in wed_rows if r[2]]
    eia_rngs   = [float(r[5]) for r in wed_rows if r[5]]
    eia_nets   = [float(r[6]) for r in wed_rows if r[6]]

    print(f"\n  Wednesday (EIA day) — {n} sessions:")
    print(f"  {'Date':<12} {'FullRng':>8} {'FullNet':>8} {'MornRng':>8} {'USOpenRng':>10} {'EIARng':>8} {'EIANet':>8}")
    print(f"  {'-'*65}")
    for r in wed_rows:
        print(f"  {str(r[0]):<12} {str(r[1]):>8} {str(r[2]):>+8} {str(r[3]):>8} "
              f"{str(r[4]):>10} {str(r[5]):>8} {str(r[6]):>+8}")

    print(f"\n  Wednesday stats:")
    pstats(full_rngs, "  Full day range")
    pstats(full_nets, "  Full day net  ")
    pstats(eia_rngs,  "  EIA hour range")
    pstats(eia_nets,  "  EIA hour net  ")

    # Compare EIA range vs other days' same hour
    cur.execute("""
        WITH daily AS (
            SELECT
                (ts AT TIME ZONE 'Asia/Kolkata')::date                     AS d,
                EXTRACT(DOW FROM ts AT TIME ZONE 'Asia/Kolkata')::int      AS dow,
                EXTRACT(HOUR FROM ts AT TIME ZONE 'Asia/Kolkata')::int     AS hr,
                high, low
            FROM mcx_ohlc
            WHERE instrument='CRUDEOIL' AND interval='minute'
        )
        SELECT dow,
               ROUND(AVG(day_range)::numeric) AS avg_eia_hr_range
        FROM (
            SELECT d, dow,
                   MAX(CASE WHEN hr BETWEEN 20 AND 21 THEN high END)
                   - MIN(CASE WHEN hr BETWEEN 20 AND 21 THEN low END) AS day_range
            FROM daily
            GROUP BY d, dow
        ) x
        WHERE dow BETWEEN 1 AND 5 AND day_range IS NOT NULL
        GROUP BY dow ORDER BY dow
    """)
    print(f"\n  EIA window (20-21h) avg range by day-of-week:")
    dow_n = {1:'Mon', 2:'Tue', 3:'Wed*EIA', 4:'Thu', 5:'Fri'}
    for r in cur.fetchall():
        marker = " <<" if int(r[0]) == 3 else ""
        print(f"    {dow_n.get(int(r[0]),str(r[0]))}: {r[1]} pts{marker}")


# ═════════════════════════════════════════════════════════════════════════════
# S13: WICK REJECTION SIGNALS  (15-min)
# ═════════════════════════════════════════════════════════════════════════════
sep("S13: WICK REJECTION SIGNALS  (15-min candles)")

if c15:
    # Classify each candle's wick pattern
    def wick_analysis(candles, wick_mult):
        uw_bear = uw_bull = lw_bull = lw_bear = 0
        total_uw = total_lw = 0
        for i in range(len(candles) - 1):
            _, o, h, l, c = candles[i]
            body = abs(c - o)
            upper_wick = h - max(o, c)
            lower_wick = min(o, c) - l
            next_move  = candles[i+1][4] - candles[i+1][1]

            if body > 0 and upper_wick > wick_mult * body:
                total_uw += 1
                if next_move < 0: uw_bear += 1
                else: uw_bull += 1

            if body > 0 and lower_wick > wick_mult * body:
                total_lw += 1
                if next_move > 0: lw_bull += 1
                else: lw_bear += 1

        return total_uw, uw_bear, total_lw, lw_bull

    print(f"\n  {'WickMult':<10} {'UpperWick(N)':>12} {'→Bear%':>7} {'LowerWick(N)':>13} {'→Bull%':>7}")
    print(f"  {'-'*55}")
    for mult in [1.5, 2.0, 3.0]:
        tuw, uw_b, tlw, lw_b = wick_analysis(c15, mult)
        uw_pct = uw_b/tuw*100 if tuw else 0
        lw_pct = lw_b/tlw*100 if tlw else 0
        print(f"  >{mult:.1f}x body  {tuw:>8} UW    {uw_pct:>6.0f}% {tlw:>9} LW    {lw_pct:>6.0f}%")


# ═════════════════════════════════════════════════════════════════════════════
# S14: DAILY RANGE DISTRIBUTION
# ═════════════════════════════════════════════════════════════════════════════
sep("S14: DAILY RANGE DISTRIBUTION & PROBABILITY")

cur.execute("""
    SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date AS d,
           MAX(high) - MIN(low) AS rng
    FROM mcx_ohlc
    WHERE instrument='CRUDEOIL' AND interval='minute'
    GROUP BY d
    HAVING MAX(high) - MIN(low) > 50
    ORDER BY d
""")
rng_rows = cur.fetchall()
all_rngs = [float(r[1]) for r in rng_rows]

if all_rngs:
    n = len(all_rngs)
    srt = sorted(all_rngs)
    avg = sum(all_rngs) / n
    med = srt[n//2]
    std = statistics.stdev(all_rngs)

    print(f"\n  Daily range ({n} trading days, range > 50 pts):")
    print(f"    Mean:   {avg:.0f} pts")
    print(f"    Median: {med:.0f} pts")
    print(f"    StdDev: {std:.0f} pts")
    print(f"    Min: {min(all_rngs):.0f}   P10: {srt[n//10]:.0f}   P25: {srt[n//4]:.0f}   "
          f"P75: {srt[3*n//4]:.0f}   P90: {srt[9*n//10]:.0f}   Max: {max(all_rngs):.0f}")

    print(f"\n  Range bucket probability:")
    buckets = [
        (50,  150,  "Very quiet"),
        (150, 250,  "Quiet"),
        (250, 350,  "Normal"),
        (350, 500,  "Trending"),
        (500, 700,  "Volatile"),
        (700, 99999,"Extreme"),
    ]
    for lo, hi, label in buckets:
        cnt = sum(1 for r in all_rngs if lo <= r < hi)
        pct = cnt / n * 100
        bar = "█" * int(pct / 2)
        print(f"    {lo:>4}–{hi if hi<99999 else '∞':<4} ({label:<12}): "
              f"{cnt:>3} days ({pct:>4.0f}%) {bar}")


# ═════════════════════════════════════════════════════════════════════════════
# S15: RANGE COMPRESSION → EXPANSION EDGE  (15-min)
# ═════════════════════════════════════════════════════════════════════════════
sep("S15: RANGE COMPRESSION → EXPANSION EDGE  (15-min)")

if c15 and atr_15:
    print(f"\n  Searching for N consecutive compressed candles "
          f"(range < threshold × ATR) → next candle expansion:")
    print(f"\n  {'N':>3} {'Threshold':>10} {'Events':>7} {'Expand%':>9} {'Avg_Nxt_Rng':>12}")
    print(f"  {'-'*50}")

    for n_comp in [3, 4, 5]:
        for comp_th in [0.4, 0.6]:
            expand_th = 1.5
            expand_cnt = no_expand_cnt = 0
            expand_rngs = []

            for i in range(n_comp + 13, len(c15) - 1):
                if atr_15[i] is None: continue
                atr = atr_15[i]
                if atr < 5: continue

                compressed = all(
                    atr_15[i-j] is not None and
                    (highs_15[i-j] - lows_15[i-j]) < comp_th * atr_15[i-j]
                    for j in range(n_comp)
                )
                if not compressed: continue

                next_rng = highs_15[i+1] - lows_15[i+1]
                if next_rng > expand_th * atr:
                    expand_cnt += 1
                    expand_rngs.append(next_rng)
                else:
                    no_expand_cnt += 1

            total = expand_cnt + no_expand_cnt
            if total < 5: continue
            exp_pct = expand_cnt / total * 100
            avg_nxt = sum(expand_rngs) / len(expand_rngs) if expand_rngs else 0
            print(f"  {n_comp:>3} {comp_th:>10.1f}x {total:>7} {exp_pct:>8.1f}%  {avg_nxt:>12.0f}")

    # Direction bias after compression
    print(f"\n  Compression → expansion direction bias:")
    n_comp, comp_th = 4, 0.5
    bull_exp = bear_exp = 0
    for i in range(n_comp + 13, len(c15) - 1):
        if atr_15[i] is None: continue
        atr = atr_15[i]
        if atr < 5: continue

        compressed = all(
            atr_15[i-j] is not None and
            (highs_15[i-j] - lows_15[i-j]) < comp_th * atr_15[i-j]
            for j in range(n_comp)
        )
        if not compressed: continue

        next_body = closes_15[i+1] - opens_15[i+1]
        next_rng  = highs_15[i+1] - lows_15[i+1]
        if next_rng > 1.5 * atr:
            if next_body > 0: bull_exp += 1
            else: bear_exp += 1

    total_dir = bull_exp + bear_exp
    if total_dir > 0:
        print(f"    Bull expansion: {bull_exp}/{total_dir} = {bull_exp/total_dir*100:.0f}%")
        print(f"    Bear expansion: {bear_exp}/{total_dir} = {bear_exp/total_dir*100:.0f}%")
        print(f"    → Direction after compression is {'random' if abs(bull_exp-bear_exp) < total_dir*0.1 else 'biased'}")


# ═════════════════════════════════════════════════════════════════════════════
# SYNTHESIS: EDGE RANKING
# ═════════════════════════════════════════════════════════════════════════════
sep("SYNTHESIS: EDGES TO TRADE vs AVOID")
print("""
  [INTERPRET THE NUMBERS ABOVE TO FILL THIS IN]

  Mark each row after reviewing S1-S15:
  ┌─────────────────────────────────────────────────────────────────────┐
  │ Edge                      │ Win% │ PF │ N   │ Verdict               │
  ├─────────────────────────────────────────────────────────────────────┤
  │ OI Call wall resistance   │  ?   │ ?  │  ?  │ see S7                │
  │ OI Put wall support       │  ?   │ ?  │  ?  │ see S7                │
  │ VWAP +2SD short           │  ?   │ ?  │  ?  │ see S10               │
  │ VWAP −2SD long            │  ?   │ ?  │  ?  │ see S10               │
  │ WTI alignment trade       │  ?   │ ?  │  ?  │ see S8                │
  │ ORB breakout continuation │  ?   │ ?  │  ?  │ see S6                │
  │ EIA Wednesday spike       │  ?   │ ?  │  ?  │ see S12               │
  │ Wick rejection            │  ?   │ ?  │  ?  │ see S13               │
  │ Compression → expansion   │  ?   │ ?  │  ?  │ see S15               │
  └─────────────────────────────────────────────────────────────────────┘

  Run this script → paste output → next step: build concrete backtest
  for any edge where win% > 58% AND N > 50 AND PF > 1.4
""")

cur.close()
conn.close()
print("\n" + "═"*72)
print("  QUANTITATIVE RESEARCH COMPLETE")
print("═"*72)
