"""
Load and validate holdings CSVs.
Auto-detects Zerodha native export format vs standard format.
"""

import os
import pandas as pd

# ── ETF / Index Fund detection ──────────────────────────────────────────────
# Symbols that are ETFs/Index funds on NSE (not direct equities)
ETF_SUFFIXES   = ("BEES", "CASE", "ETF")
ETF_EXACT      = {
    "MON100", "MODEFENCE", "TATAGOLD", "GROWWRAIL",
    "HEALTHY", "METAL", "TMCV", "TMPV", "JUNIORBEES",
    "NIFTYBEES", "GOLDBEES", "BANKBEES", "ITBEES",
    "PHARMABEES", "AUTOBEES", "PVTBANIETF", "OILIETF",
    "GOLDCASE", "MID150CASE",
}

ETF_CATEGORIES = {
    "NIFTYBEES":   ("Nifty 50 ETF",        "Broad Market"),
    "JUNIORBEES":  ("Nifty Next 50 ETF",   "Broad Market"),
    "BANKBEES":    ("Bank Nifty ETF",       "Sector - Banking"),
    "PVTBANIETF":  ("Pvt Bank ETF",         "Sector - Banking"),
    "ITBEES":      ("IT ETF",               "Sector - IT"),
    "PHARMABEES":  ("Pharma ETF",           "Sector - Pharma"),
    "AUTOBEES":    ("Auto ETF",             "Sector - Auto"),
    "MODEFENCE":   ("Defence ETF",          "Sector - Defence"),
    "METAL":       ("Metal ETF",            "Sector - Metal"),
    "OILIETF":     ("Oil ETF",              "Sector - Energy"),
    "HEALTHY":     ("Healthcare ETF",       "Sector - Healthcare"),
    "GROWWRAIL":   ("Rail/Infra ETF",       "Sector - Infrastructure"),
    "GOLDBEES":    ("Gold ETF",             "Commodity - Gold"),
    "GOLDCASE":    ("Gold ETF",             "Commodity - Gold"),
    "TATAGOLD":    ("Gold ETF",             "Commodity - Gold"),
    "MON100":      ("Nasdaq 100 ETF",       "International - US Tech"),
    "MID150CASE":  ("Midcap 150 ETF",       "Broad Market - Midcap"),
    "TMCV":        ("Tata Motor CommVeh ETF","Sector - Auto"),
    "TMPV":        ("Tata Motor PassVeh ETF","Sector - Auto"),
}


def is_etf(symbol: str) -> bool:
    sym = symbol.upper().strip()
    if sym in ETF_EXACT:
        return True
    return any(sym.endswith(sfx) for sfx in ETF_SUFFIXES)


def etf_meta(symbol: str) -> tuple:
    """Return (etf_name, category) for known ETFs."""
    return ETF_CATEGORIES.get(symbol.upper(), (f"{symbol} ETF", "ETF"))


# ── Zerodha native format parser ─────────────────────────────────────────────

def _parse_zerodha_native(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={
        "Instrument": "Symbol",
        "Qty.":       "Quantity",
        "Avg. cost":  "Avg_Cost",
        "LTP":        "LTP",
        "Cur. val":   "Current_Value",
        "P&L":        "PnL",
        "Net chg.":   "Net_Change_Pct",
    })
    for col in ["Symbol"]:
        df[col] = df[col].astype(str).str.strip().str.upper()
    for col in ["Quantity", "Avg_Cost", "LTP", "Current_Value", "PnL", "Net_Change_Pct"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["Quantity"]     = df["Quantity"].fillna(0)
    df["Avg_Cost"]     = df["Avg_Cost"].fillna(0)
    df["Is_ETF"]       = df["Symbol"].apply(is_etf)
    df["ETF_Name"]     = df["Symbol"].apply(lambda s: etf_meta(s)[0])
    df["ETF_Category"] = df["Symbol"].apply(lambda s: etf_meta(s)[1])

    df = df[df["Quantity"] > 0].copy()
    # Drop empty trailing rows (Zerodha adds an empty last row)
    df = df[df["Symbol"].str.len() > 0]
    return df.reset_index(drop=True)


# ── Standard format parser ───────────────────────────────────────────────────

def _parse_standard(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["Symbol", "Quantity", "Avg_Cost"]:
        if col not in df.columns:
            raise ValueError(f"Missing column: {col}")
    df["Symbol"]   = df["Symbol"].astype(str).str.strip().str.upper()
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0)
    df["Avg_Cost"] = pd.to_numeric(df["Avg_Cost"], errors="coerce").fillna(0)
    df["LTP"]            = pd.to_numeric(df.get("LTP", None), errors="coerce")
    df["Current_Value"]  = None
    df["PnL"]            = None
    df["Net_Change_Pct"] = None
    df["Is_ETF"]         = df["Symbol"].apply(is_etf)
    df["ETF_Name"]       = df["Symbol"].apply(lambda s: etf_meta(s)[0])
    df["ETF_Category"]   = df["Symbol"].apply(lambda s: etf_meta(s)[1])
    df = df[df["Quantity"] > 0]
    return df.reset_index(drop=True)


# ── Public loader ────────────────────────────────────────────────────────────

def load_zerodha_holdings(filepath: str) -> pd.DataFrame:
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Holdings file not found: {filepath}")
    df = pd.read_csv(filepath)
    df.columns = df.columns.str.strip().str.replace('"', '')
    # Detect Zerodha native format by presence of "Instrument" column
    if "Instrument" in df.columns:
        return _parse_zerodha_native(df)
    return _parse_standard(df)


# ── MF category detection from fund name ────────────────────────────────────

def _detect_mf_category(name: str) -> str:
    n = name.lower()
    if "small cap"   in n: return "Small Cap Fund"
    if "mid cap"     in n or "midcap" in n: return "Mid Cap Fund"
    if "large & mid" in n or "large and mid" in n: return "Large & Mid Cap Fund"
    if "large cap"   in n or "largecap" in n: return "Large Cap Fund"
    if "flexi cap"   in n or "flexicap" in n: return "Flexi Cap Fund"
    if "multi cap"   in n or "multicap" in n: return "Multi Cap Fund"
    if "focused"     in n: return "Focused Fund"
    if "contra"      in n or "value"   in n: return "Value/Contra Fund"
    if "elss"        in n or "tax saver" in n: return "ELSS"
    if "balanced advantage" in n or "dynamic asset" in n: return "Balanced Advantage Fund"
    if "hybrid"      in n or "aggressive hybrid" in n: return "Aggressive Hybrid Fund"
    if "index"       in n or "nifty 50" in n: return "Index Fund"
    if "liquid"      in n or "overnight" in n or "money market" in n: return "Liquid/Debt Fund"
    if "sectoral"    in n or "thematic" in n or "manufacturing" in n \
       or "infra" in n or "banking" in n or "pharma" in n \
       or "technology" in n or "defence" in n: return "Sectoral/Thematic Fund"
    return "Diversified Equity Fund"


# ── Scheme code lookup via mfapi.in ─────────────────────────────────────────

_SCHEME_CACHE: dict = {}


def _lookup_scheme_code(fund_name: str):
    """Search mfapi.in for the Direct Growth scheme code matching fund_name."""
    if fund_name in _SCHEME_CACHE:
        return _SCHEME_CACHE[fund_name]
    try:
        import requests, re
        # Fetch all schemes list
        resp = requests.get("https://api.mfapi.in/mf", timeout=12)
        resp.raise_for_status()
        all_schemes = resp.json()

        # Clean up fund name for matching
        query = re.sub(r'\s+', ' ', fund_name.lower()
                       .replace("direct", "").replace("growth", "")
                       .replace("plan", "").replace("option", "").strip())

        best_code  = None
        best_score = 0

        for s in all_schemes:
            sname = s["schemeName"].lower()
            # Prefer Direct Growth plans
            if "direct" not in sname:
                continue
            # Score: count query words that appear in scheme name
            words  = [w for w in query.split() if len(w) > 3]
            score  = sum(1 for w in words if w in sname)
            if score > best_score:
                best_score = score
                best_code  = s["schemeCode"]

        _SCHEME_CACHE[fund_name] = best_code
        return best_code
    except Exception:
        return None


# ── Zerodha Coin MF format parser ────────────────────────────────────────────

def _parse_zerodha_coin_mf(df: pd.DataFrame) -> pd.DataFrame:
    """Parse Zerodha Coin MF export (same column structure as equity holdings)."""
    df = df.rename(columns={
        "Instrument": "Fund_Name",
        "Qty.":       "Total_Units",
        "Avg. cost":  "Avg_Purchase_NAV",
        "LTP":        "Current_NAV",
        "Invested":   "Invested_Amount",
        "Cur. val":   "Current_Value",
        "P&L":        "PnL",
        "Net chg.":   "Net_Change_Pct",
    })
    df["Fund_Name"]        = df["Fund_Name"].astype(str).str.strip()
    df["Total_Units"]      = pd.to_numeric(df["Total_Units"],      errors="coerce").fillna(0)
    df["Avg_Purchase_NAV"] = pd.to_numeric(df["Avg_Purchase_NAV"], errors="coerce").fillna(0)
    df["Current_NAV"]      = pd.to_numeric(df["Current_NAV"],      errors="coerce")
    df["Invested_Amount"]  = pd.to_numeric(df["Invested_Amount"],  errors="coerce")
    df["Current_Value"]    = pd.to_numeric(df["Current_Value"],    errors="coerce")
    df["PnL"]              = pd.to_numeric(df["PnL"],              errors="coerce")
    df["Net_Change_Pct"]   = pd.to_numeric(df["Net_Change_Pct"],   errors="coerce")

    # Drop empty rows
    df = df[df["Fund_Name"].str.len() > 2]
    df = df[df["Total_Units"] > 0].copy()

    # Detect category from name
    df["Category"] = df["Fund_Name"].apply(_detect_mf_category)

    # SIP unknown from Zerodha export — default 0
    df["Monthly_SIP"]  = 0.0
    df["Expense_Ratio"] = 0.0
    df["Notes"]        = ""

    # Try to resolve scheme codes (may fail if network unavailable)
    print("  Looking up MF scheme codes from mfapi.in ...")
    codes = []
    for name in df["Fund_Name"]:
        code = _lookup_scheme_code(name)
        codes.append(code if code else 0)
        status = f"✓ {code}" if code else "✗ not found"
        print(f"    {name[:55]:55s} → {status}")
    df["Scheme_Code"] = codes

    return df.reset_index(drop=True)


# ── MF loader (auto-detects format) ─────────────────────────────────────────

MF_REQUIRED_COLS = {"Fund_Name", "Scheme_Code", "Monthly_SIP", "Total_Units", "Avg_Purchase_NAV"}

# Path for Zerodha Coin native export
MF_ZERODHA_FILE = "data/mf_holdings_zerodha.csv"


def load_mf_holdings(filepath: str) -> pd.DataFrame:
    # Try Zerodha Coin native file first
    zerodha_path = MF_ZERODHA_FILE
    if os.path.exists(zerodha_path):
        df = pd.read_csv(zerodha_path)
        df.columns = df.columns.str.strip().str.replace('"', '')
        if "Instrument" in df.columns:
            return _parse_zerodha_coin_mf(df)

    # Fall back to standard format
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"MF holdings file not found: {filepath}")
    df = pd.read_csv(filepath)
    df.columns = df.columns.str.strip()

    missing = MF_REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"mf_holdings.csv is missing columns: {missing}")

    df["Fund_Name"]        = df["Fund_Name"].str.strip()
    df["Scheme_Code"]      = pd.to_numeric(df["Scheme_Code"], errors="coerce").fillna(0).astype(int)
    df["Monthly_SIP"]      = pd.to_numeric(df["Monthly_SIP"], errors="coerce").fillna(0)
    df["Total_Units"]      = pd.to_numeric(df["Total_Units"], errors="coerce").fillna(0)
    df["Avg_Purchase_NAV"] = pd.to_numeric(df["Avg_Purchase_NAV"], errors="coerce").fillna(0)
    df["Category"]         = df["Fund_Name"].apply(_detect_mf_category)
    for col in ["Expense_Ratio", "Notes"]:
        if col not in df.columns:
            df[col] = 0.0 if col == "Expense_Ratio" else ""
    df = df[df["Total_Units"] > 0]
    return df.reset_index(drop=True)
