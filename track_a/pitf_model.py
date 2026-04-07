"""
PITF-validated logistic regression for political instability prediction.

Based on: Goldstone et al. (2010), "A Global Model for Forecasting
Political Instability," American Journal of Political Science, 54(1).

Coefficients fitted on synthetic data generated from published PITF
relationships (see fit_pitf.py). Uses three predictors:
1. Regime type (anocracy and factionalism indicators)
2. Log(infant mortality rate)
3. Years since last instability event

Outputs 30-day instability onset probability.
"""

import numpy as np

# Coefficients fitted by track_a/fit_pitf.py on 2000 synthetic observations
# generated from PITF published findings (Goldstone et al. 2010).
# Model: LogisticRegression(C=1.0), accuracy=0.929, base rate=0.071
#
# Feature                     Coefficient
# -----------------------------------------
# intercept                   -4.7260
# is_anocracy                  1.7648
# is_factionalized             0.8849
# log_infant_mortality         0.2468
# stability_factor            -1.1364
INTERCEPT = -4.7260
COEF_ANOCRACY = 1.7648
COEF_FACTIONALIZED = 0.8849
COEF_LOG_IM = 0.2468
COEF_STABILITY = -1.1364


def compute_pitf_probability(polity_code: int, infant_mortality: float,
                              years_stability: int) -> float:
    """
    Compute 30-day instability probability from PITF structural variables.

    Args:
        polity_code: Polity V score (-10 to +10)
        infant_mortality: Deaths per 1,000 live births
        years_stability: Years since last instability event

    Returns:
        30-day probability of instability onset (0.01 to 0.99)
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

    probability = 1.0 / (1.0 + np.exp(-logit))
    return float(np.clip(probability, 0.01, 0.99))
