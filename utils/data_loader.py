"""
Load and validate holdings CSVs.
Auto-detects Zerodha native export format vs standard format.
"""

import logging
import os
import time

import pandas as pd

from utils.validators import validate_holdings_row, validate_mf_row

logger = logging.getLogger(__name__)

# ── ETF / Index Fund detection ────────────────────────────────────────────────

ETF_SUFFIXES = ("BEES", "CASE", "ETF")
ETF_EXACT    = {
    "MON100", "MODEFENCE", "TATAGOLD", "GROWWRAIL",
    "HEALTHY", "METAL", "TMCV", "TMPV", "JUNIORBEES",
    "NIFTYBEES", "GOLDBEES", "BANKBEES", "ITBEES",
    "PHARMABEES", "AUTOBEES", "PVTBANIETF", "OILIETF",
    "GOLDCASE", "MID150CASE",
}

ETF_CATEGORIES = {
    "NIFTYBEES":   ("Nifty 50 ETF",           "Broad Market"),
    "JUNIORBEES":  ("Nifty Next 50 ETF",      "Broad Market"),
    "BANKBEES":    ("Bank Nifty ETF",          "Sector - Banking"),
    "PVTBANIETF":  ("Pvt Bank ETF",            "Sector - Banking"),
    "ITBEES":      ("IT ETF",                  "Sector - IT"),
    "PHARMABEES":  ("Pharma ETF",              "Sector - Pharma"),
    "AUTOBEES":    ("Auto ETF",                "Sector - Auto"),
    "MODEFENCE":   ("Defence ETF",             "Sector - Defence"),
    "METAL":       ("Metal ETF",               "Sector - Metal"),
    "OILIETF":     ("Oil ETF",                 "Sector - Energy"),
    "HEALTHY":     ("Healthcare ETF",          "Sector - Healthcare"),
    "GROWWRAIL":   ("Rail/Infra ETF",          "Sector - Infrastructure"),
    "GOLDBEES":    ("Gold ETF",                "Commodity - Gold"),
    "GOLDCASE":    ("Gold ETF",                "Commodity - Gold"),
    "TATAGOLD":    ("Gold ETF",                "Commodity - Gold"),
    "MON100":      ("Nasdaq 100 ETF",          "International - US Tech"),
    "MID150CASE":  ("Midcap 150 ETF",          "Broad Market - Midcap"),
    "TMCV":        ("Tata Motor CommVeh ETF",  "Sector - Auto"),
    "TMPV":        ("Tata Motor PassVeh ETF",  "Sector - Auto"),
}


def is_etf(symbol: str) -> bool:
    sym = symbol.upper().strip()
    if sym in ETF_EXACT:
        return True
    return any(sym.endswith(sfx) for sfx in ETF_SUFFIXES)


def etf_meta(symbol: str) -> tuple:
    """Return (etf_name, category) for known ETFs."""
    return ETF_CATEGORIES.get(symbol.upper(), (f"{symbol} ETF", "ETF"))


# ── Zerodha native format parser ──────────────────────────────────────────────

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
    df = df[df["Symbol"].str.len() > 0]

    # Validate rows and log warnings for any issues
    for _, row in df.iterrows():
        validate_holdings_row(row.to_dict())

    return df.reset_index(drop=True)


# ── Standard format parser ────────────────────────────────────────────────────

def _parse_standard(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["Symbol", "Quantity", "Avg_Cost"]:
        if col not in df.columns:
            raise ValueError(f"Missing column: {col}")
    df["Symbol"]        = df["Symbol"].astype(str).str.strip().str.upper()
    df["Quantity"]      = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0)
    df["Avg_Cost"]      = pd.to_numeric(df["Avg_Cost"], errors="coerce").fillna(0)
    df["LTP"]           = pd.to_numeric(df.get("LTP", None), errors="coerce")
    df["Current_Value"] = None
    df["PnL"]           = None
    df["Net_Change_Pct"]= None
    df["Is_ETF"]        = df["Symbol"].apply(is_etf)
    df["ETF_Name"]      = df["Symbol"].apply(lambda s: etf_meta(s)[0])
    df["ETF_Category"]  = df["Symbol"].apply(lambda s: etf_meta(s)[1])
    df = df[df["Quantity"] > 0]
    return df.reset_index(drop=True)


# ── Public loader ─────────────────────────────────────────────────────────────

def load_zerodha_holdings(filepath: str) -> pd.DataFrame:
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Holdings file not found: {filepath}")
    df = pd.read_csv(filepath)
    df.columns = df.columns.str.strip().str.replace('"', '')
    if "Instrument" in df.columns:
        logger.info("Detected Zerodha native format in %s", filepath)
        return _parse_zerodha_native(df)
    logger.info("Detected standard format in %s", filepath)
    return _parse_standard(df)


# ── MF category detection ─────────────────────────────────────────────────────

def _detect_mf_category(name: str) -> str:
    n = name.lower()
    if "small cap"            in n: return "Small Cap Fund"
    if "mid cap"              in n or "midcap"     in n: return "Mid Cap Fund"
    if "large & mid"          in n or "large and mid" in n: return "Large & Mid Cap Fund"
    if "large cap"            in n or "largecap"   in n: return "Large Cap Fund"
    if "flexi cap"            in n or "flexicap"   in n: return "Flexi Cap Fund"
    if "multi cap"            in n or "multicap"   in n: return "Multi Cap Fund"
    if "focused"              in n: return "Focused Fund"
    if "contra"               in n or "value"      in n: return "Value/Contra Fund"
    if "elss"                 in n or "tax saver"  in n: return "ELSS"
    if "balanced advantage"   in n or "dynamic asset" in n: return "Balanced Advantage Fund"
    if "hybrid"               in n or "aggressive hybrid" in n: return "Aggressive Hybrid Fund"
    if "index"                in n or "nifty 50"   in n: return "Index Fund"
    if "liquid"               in n or "overnight"  in n or "money market" in n: return "Liquid/Debt Fund"
    if "sectoral" in n or "thematic" in n or "manufacturing" in n \
       or "infra" in n or "banking" in n or "pharma" in n \
       or "technology" in n or "defence" in n: return "Sectoral/Thematic Fund"
    return "Diversified Equity Fund"


# ── Scheme code lookup ────────────────────────────────────────────────────────

_SCHEME_CACHE: dict = {}
_SCHEME_CACHE_TS: float = 0.0
_SCHEME_CACHE_TTL = 3600   # 1 hour


def _lookup_scheme_code(fund_name: str):
    """Search mfapi.in for the Direct Growth scheme code matching fund_name."""
    global _SCHEME_CACHE, _SCHEME_CACHE_TS

    # Clear cache if stale
    if time.time() - _SCHEME_CACHE_TS > _SCHEME_CACHE_TTL:
        _SCHEME_CACHE.clear()
        _SCHEME_CACHE_TS = time.time()
        logger.debug("Scheme code cache cleared (TTL expired)")

    if fund_name in _SCHEME_CACHE:
        return _SCHEME_CACHE[fund_name]

    try:
        import re
        import requests
        resp = requests.get("https://api.mfapi.in/mf", timeout=12)
        resp.raise_for_status()
        all_schemes = resp.json()

        query = re.sub(
            r'\s+', ' ',
            fund_name.lower()
                     .replace("direct", "")
                     .replace("growth", "")
                     .replace("plan", "")
                     .replace("option", "")
                     .strip()
        )

        best_code  = None
        best_score = 0

        for s in all_schemes:
            sname = s["schemeName"].lower()
            if "direct" not in sname:
                continue
            words = [w for w in query.split() if len(w) > 3]
            score = sum(1 for w in words if w in sname)
            if score > best_score:
                best_score = score
                best_code  = s["schemeCode"]

        _SCHEME_CACHE[fund_name] = best_code
        return best_code

    except Exception as exc:
        logger.warning("Scheme code lookup failed for '%s': %s", fund_name, exc)
        return None


# ── Zerodha Coin MF format parser ─────────────────────────────────────────────

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

    df = df[df["Fund_Name"].str.len() > 2]
    df = df[df["Total_Units"] > 0].copy()
    df["Category"]     = df["Fund_Name"].apply(_detect_mf_category)
    df["Monthly_SIP"]  = 0.0
    df["Expense_Ratio"] = 0.0
    df["Notes"]        = ""

    logger.info("Looking up MF scheme codes from mfapi.in for %d funds…", len(df))
    codes = []
    for name in df["Fund_Name"]:
        code   = _lookup_scheme_code(name)
        status = f"found: {code}" if code else "not found"
        logger.info("  %-55s → %s", name[:55], status)
        codes.append(code if code else 0)
    df["Scheme_Code"] = codes

    # Validate rows
    for _, row in df.iterrows():
        validate_mf_row(row.to_dict())

    return df.reset_index(drop=True)


# ── MF loader (auto-detects format) ──────────────────────────────────────────

MF_REQUIRED_COLS  = {"Fund_Name", "Scheme_Code", "Monthly_SIP", "Total_Units", "Avg_Purchase_NAV"}
MF_ZERODHA_FILE   = "data/mf_holdings_zerodha.csv"


def load_mf_holdings(filepath: str) -> pd.DataFrame:
    # Try Zerodha Coin native file first
    if os.path.exists(MF_ZERODHA_FILE):
        df = pd.read_csv(MF_ZERODHA_FILE)
        df.columns = df.columns.str.strip().str.replace('"', '')
        if "Instrument" in df.columns:
            logger.info("Loading MF holdings from Zerodha Coin format: %s", MF_ZERODHA_FILE)
            return _parse_zerodha_coin_mf(df)

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

    # Validate rows
    for _, row in df.iterrows():
        validate_mf_row(row.to_dict())

    logger.info("Loaded %d MF holdings from %s", len(df), filepath)
    return df.reset_index(drop=True)
