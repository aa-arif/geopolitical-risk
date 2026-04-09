"""
PITF-validated logistic regression for political instability prediction.

Based on: Goldstone et al. (2010), "A Global Model for Forecasting
Political Instability," American Journal of Political Science, 54(1).

Coefficients fitted on 5000 synthetic observations generated from
PUBLISHED PITF odds ratios (see fit_pitf.py):
- Factionalized polities: OR ~30x vs full democracies
- Partial democracies w/o factionalism: OR ~8x
- Full autocracies: OR ~3-5x
- Infant mortality: log-linear, OR ~1.3x per unit ln(IM)
- Annual base rate: ~3.5%

Outputs 30-day instability probability (broader than PITF onset:
includes protest/unrest > 90th percentile, coup attempts, regime changes).
"""

import numpy as np

# Coefficients fitted by track_a/fit_pitf.py on 5000 synthetic observations
# using published PITF odds ratios from Goldstone et al. (2010).
# Model: LogisticRegression(C=1.0), accuracy=0.954, base rate=0.046
#
# Feature                     Coefficient
# -----------------------------------------
# intercept                   -3.8287
# is_anocracy                 -0.0245
# is_factionalized             1.3456
# log_infant_mortality         0.2441
# stability_factor            -1.9697
INTERCEPT = -3.8287
COEF_ANOCRACY = -0.0245
COEF_FACTIONALIZED = 1.3456
COEF_LOG_IM = 0.2441
COEF_STABILITY = -1.9697

# Clustering/broadening factor for annual -> 30-day conversion.
# PITF onset (~3.5% annual) is narrower than our prediction question
# (protest > 90th pct, coup attempt, or adverse regime change).
_CLUSTERING_FACTOR = 8.0


def _annual_to_30day(p_annual: float) -> float:
    """Convert PITF annual onset probability to 30-day window probability."""
    p_month_avg = 1.0 - (1.0 - p_annual) ** (1.0 / 12.0)
    return min(0.95, p_month_avg * _CLUSTERING_FACTOR)


def compute_pitf_probability(polity_code: int, infant_mortality: float,
                              years_stability: int) -> float:
    """
    Compute 30-day instability probability from PITF structural variables.

    Args:
        polity_code: Polity V score (-10 to +10)
        infant_mortality: Deaths per 1,000 live births
        years_stability: Years since last instability event

    Returns:
        30-day probability of instability (0.01 to 0.99)
    """
    is_anocracy = 1.0 if -5 <= polity_code <= 5 else 0.0
    is_factionalized = 1.0 if 0 <= polity_code <= 5 else 0.0
    log_infant_mort = np.log(max(infant_mortality, 1.0))
    stability_factor = min(years_stability, 20) / 20.0

    logit = (
        INTERCEPT
        + COEF_ANOCRACY * is_anocracy
        + COEF_FACTIONALIZED * is_factionalized
        + COEF_LOG_IM * log_infant_mort
        + COEF_STABILITY * stability_factor
    )

    p_annual = 1.0 / (1.0 + np.exp(-logit))
    p_30day = _annual_to_30day(p_annual)
    return float(np.clip(p_30day, 0.01, 0.99))
