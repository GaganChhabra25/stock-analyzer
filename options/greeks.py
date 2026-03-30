"""
Black-Scholes IV and Greeks calculator.
No external dependencies — pure Python math.
"""

import math
from typing import Optional


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, opt: str) -> float:
    """Black-Scholes price. opt = 'CE' or 'PE'."""
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if opt == "CE" else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if opt == "CE":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    opt: str,
    max_iter: int = 100,
    tol: float = 1e-5,
) -> Optional[float]:
    """Calculate IV using Newton-Raphson. Returns None if no solution found."""
    if T <= 0 or market_price <= 0:
        return None

    sigma = 0.3  # initial guess
    for _ in range(max_iter):
        price  = bs_price(S, K, T, r, sigma, opt)
        diff   = price - market_price
        if abs(diff) < tol:
            return round(sigma * 100, 2)  # return as percentage

        # vega for Newton step
        d1    = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        vega  = S * _norm_pdf(d1) * math.sqrt(T)
        if vega < 1e-10:
            break
        sigma -= diff / vega
        if sigma <= 0:
            sigma = 1e-6

    return None


def calculate_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    opt: str,
) -> dict:
    """
    Calculate option Greeks.
    sigma is IV as a decimal (e.g. 0.20 for 20%).
    Returns dict with delta, gamma, theta, vega.
    """
    if T <= 0 or sigma <= 0:
        return {"delta": None, "gamma": None, "theta": None, "vega": None}

    d1   = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2   = d1 - sigma * math.sqrt(T)
    nd1  = _norm_pdf(d1)
    sqrt_T = math.sqrt(T)

    gamma = round(nd1 / (S * sigma * sqrt_T), 6)
    vega  = round(S * nd1 * sqrt_T / 100, 4)     # per 1% IV change

    if opt == "CE":
        delta = round(_norm_cdf(d1), 4)
        theta = round(
            (-S * nd1 * sigma / (2 * sqrt_T)
             - r * K * math.exp(-r * T) * _norm_cdf(d2)) / 365,
            4
        )
    else:
        delta = round(_norm_cdf(d1) - 1.0, 4)
        theta = round(
            (-S * nd1 * sigma / (2 * sqrt_T)
             + r * K * math.exp(-r * T) * _norm_cdf(-d2)) / 365,
            4
        )

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}
