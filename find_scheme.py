#!/usr/bin/env python3
"""
Helper: Search for MF scheme codes from mfapi.in
Usage: python find_scheme.py "mirae asset"
       python find_scheme.py "parag parikh"
"""

import sys
import requests
from tabulate import tabulate

MFAPI_ALL = "https://api.mfapi.in/mf"


def search(query: str):
    print(f"Fetching all schemes from mfapi.in …")
    resp = requests.get(MFAPI_ALL, timeout=15)
    resp.raise_for_status()
    all_schemes = resp.json()   # list of {"schemeCode": int, "schemeName": str}

    q = query.lower()
    matches = [
        s for s in all_schemes
        if q in s["schemeName"].lower()
    ]

    if not matches:
        print(f"No schemes found matching '{query}'")
        return

    # Prefer Direct Growth plans
    direct = [m for m in matches if "direct" in m["schemeName"].lower()]
    growth = [m for m in (direct or matches) if "growth" in m["schemeName"].lower()]
    show   = growth or direct or matches

    rows = [[s["schemeCode"], s["schemeName"]] for s in show[:20]]
    print(f"\nTop matches for '{query}':\n")
    print(tabulate(rows, headers=["Scheme Code", "Scheme Name"], tablefmt="rounded_outline"))
    print("\nCopy the Scheme Code into your data/mf_holdings.csv")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python find_scheme.py <fund name keyword>")
        print('Example: python find_scheme.py "mirae asset large"')
        sys.exit(1)
    search(" ".join(sys.argv[1:]))
