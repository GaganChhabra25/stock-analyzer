"""
Corpus projection and SIP calculations for goal planning.
"""

from config import GOALS


def calc_future_value(present_value: float, annual_rate: float, years: int) -> float:
    """Lump sum future value at a given CAGR."""
    return present_value * ((1 + annual_rate) ** years)


def calc_sip_future_value(monthly_sip: float, annual_rate: float, years: int) -> float:
    """Future value of monthly SIP contributions (beginning-of-month)."""
    if monthly_sip <= 0:
        return 0.0
    monthly_rate = annual_rate / 12
    n_months = years * 12
    return monthly_sip * (((1 + monthly_rate) ** n_months - 1) / monthly_rate) * (1 + monthly_rate)


def calc_required_sip(target_corpus: float, present_value: float, annual_rate: float, years: int) -> float:
    """Monthly SIP needed to reach target_corpus given existing portfolio."""
    fv_lumpsum = calc_future_value(present_value, annual_rate, years)
    remaining = max(0.0, target_corpus - fv_lumpsum)
    if remaining <= 0:
        return 0.0
    monthly_rate = annual_rate / 12
    n_months = years * 12
    denominator = (((1 + monthly_rate) ** n_months - 1) / monthly_rate) * (1 + monthly_rate)
    return remaining / denominator


def calc_cagr(initial: float, final: float, years: float) -> float:
    """CAGR (%) between two portfolio values."""
    if initial <= 0 or years <= 0:
        return 0.0
    return ((final / initial) ** (1.0 / years) - 1) * 100


def project_corpus(current_portfolio_value: float, monthly_sip: float,
                   annual_rate: float = None, years: int = None) -> dict:
    """
    Full corpus projection combining existing portfolio + future SIPs.
    Returns a dict with projections and on-track status.
    """
    if annual_rate is None:
        annual_rate = GOALS["expected_cagr"]
    if years is None:
        years = GOALS["time_horizon_years"]

    fv_portfolio = calc_future_value(current_portfolio_value, annual_rate, years)
    fv_sip = calc_sip_future_value(monthly_sip, annual_rate, years)
    total_projected = fv_portfolio + fv_sip
    target = GOALS["target_corpus"]
    gap = target - total_projected
    required_sip = calc_required_sip(target, current_portfolio_value, annual_rate, years)

    # Scenario projections at different CAGR assumptions
    scenarios = {}
    for label, rate in [("Conservative (12%)", 0.12), ("Base (15%)", 0.15), ("Optimistic (18%)", 0.18)]:
        fv_p = calc_future_value(current_portfolio_value, rate, years)
        fv_s = calc_sip_future_value(monthly_sip, rate, years)
        scenarios[label] = fv_p + fv_s

    return {
        "current_portfolio":  current_portfolio_value,
        "fv_portfolio":       round(fv_portfolio, 2),
        "monthly_sip":        monthly_sip,
        "fv_sip":             round(fv_sip, 2),
        "total_projected":    round(total_projected, 2),
        "target_corpus":      target,
        "gap":                round(gap, 2),
        "on_track":           total_projected >= target,
        "required_sip":       round(required_sip, 2),
        "annual_rate":        annual_rate,
        "years":              years,
        "scenarios":          {k: round(v, 2) for k, v in scenarios.items()},
    }
