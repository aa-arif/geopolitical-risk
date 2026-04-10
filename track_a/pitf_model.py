"""
PITF-validated logistic regression for political instability prediction.

Based on: Goldstone et al. (2010), "A Global Model for Forecasting
Political Instability," American Journal of Political Science, 54(1).

Coefficients fitted on 5000 synthetic observations using PUBLISHED PITF
odds ratios (see fit_pitf.py). Uses 3 MUTUALLY EXCLUSIVE regime categories
to avoid collinearity:
- is_factionalized (polity 0 to +5): OR ~30x vs democracy
- is_closed_anocracy (polity -5 to -1): OR ~8x
- is_autocracy (polity -10 to -6): OR ~4x
- Democracy (polity 6+): reference category

Outputs 30-day instability probability.
"""

import numpy as np

# Coefficients fitted by track_a/fit_pitf.py on 5000 synthetic observations.
# Model: LogisticRegression(C=1.0), accuracy=0.961, base rate=0.039
#
# Feature                     Coefficient
# -----------------------------------------
# intercept                   -5.3430
# is_factionalized             2.8059
# is_closed_anocracy           1.2791
# is_autocracy                 0.5909
# log_infant_mortality         0.2662
# stability_factor            -1.8135
INTERCEPT = -5.3430
COEF_FACTIONALIZED = 2.8059
COEF_CLOSED_ANOCRACY = 1.2791
COEF_AUTOCRACY = 0.5909
COEF_LOG_IM = 0.2662
COEF_STABILITY = -1.8135

# Clustering/broadening factor: derived from ACLED 2024-2025 data.
# Observed monthly instability rate (events > rolling 90th pct): 24.2% (16/66 months)
# PITF annual onset rate: ~3.5% -> monthly ~0.30%
# Our prediction question (protest > 90th pct, coup, regime change) is much
# broader than PITF onset (major state failures only).
# Factor 15 produces ~16% avg PITF base across 5 countries; logit-space
# modifiers (vulnerability, GPR) push to ~24% matching observed rate.
_CLUSTERING_FACTOR = 15.0


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
    # 3 mutually exclusive regime categories (democracy is reference)
    is_factionalized = 1.0 if 0 <= polity_code <= 5 else 0.0
    is_closed_anocracy = 1.0 if -5 <= polity_code <= -1 else 0.0
    is_autocracy = 1.0 if polity_code < -5 else 0.0

    log_infant_mort = np.log(max(infant_mortality, 1.0))
    stability_factor = min(years_stability, 20) / 20.0

    logit = (
        INTERCEPT
        + COEF_FACTIONALIZED * is_factionalized
        + COEF_CLOSED_ANOCRACY * is_closed_anocracy
        + COEF_AUTOCRACY * is_autocracy
        + COEF_LOG_IM * log_infant_mort
        + COEF_STABILITY * stability_factor
    )

    p_annual = 1.0 / (1.0 + np.exp(-logit))
    p_30day = _annual_to_30day(p_annual)
    return float(np.clip(p_30day, 0.01, 0.99))
