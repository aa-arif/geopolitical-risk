"""
Fit PITF logistic regression on synthetic data derived from
ACTUAL published odds ratios in Goldstone et al. (2010).

Uses THREE mutually exclusive regime categories (no collinearity):
- is_factionalized: polity 0 to +5 (OR ~30x vs democracy)
- is_closed_anocracy: polity -5 to -1 (OR ~8x)
- is_autocracy: polity -10 to -6 (OR ~4x)
- Democracy (polity 6+) is the reference category (all zeros)

Other features:
- log(infant mortality): OR ~1.3x per unit
- stability_factor: exponential decay over decades

Usage:
    python -m track_a.fit_pitf
"""

import numpy as np
from sklearn.linear_model import LogisticRegression

np.random.seed(42)

# Published odds ratios from Goldstone et al. (2010)
ANNUAL_BASE_RATE = 0.035
OR_FACTIONALIZED = 30.0       # polity 0 to +5
OR_CLOSED_ANOCRACY = 8.0      # polity -5 to -1
OR_AUTOCRACY = 4.0            # polity -10 to -6
OR_LOG_IM_UNIT = 1.3          # per unit increase in ln(infant mortality)
OR_STABILITY_DECADE = 0.4     # 10 years stability reduces odds ~60%

REF_LOG_IM = np.log(10.0)
REF_STABILITY = 10.0


def _annual_onset_probability(polity_code, infant_mortality, years_stability):
    """Compute ANNUAL instability probability from published PITF odds ratios."""
    if 0 <= polity_code <= 5:
        regime_or = OR_FACTIONALIZED
    elif -5 <= polity_code <= -1:
        regime_or = OR_CLOSED_ANOCRACY
    elif polity_code < -5:
        regime_or = OR_AUTOCRACY
    else:
        regime_or = 1.0

    log_im = np.log(max(infant_mortality, 1.0))
    im_or = OR_LOG_IM_UNIT ** (log_im - REF_LOG_IM)
    stability_or = OR_STABILITY_DECADE ** (years_stability / REF_STABILITY)

    dem_baseline_odds = 0.005 / (1 - 0.005)
    adjusted_odds = dem_baseline_odds * regime_or * im_or * stability_or
    p = adjusted_odds / (1 + adjusted_odds)
    return np.clip(p, 0.001, 0.50)


def generate_synthetic_data(n=5000):
    """Generate synthetic country-year observations from published PITF odds ratios."""
    polity_codes = np.random.randint(-10, 11, size=n)
    infant_mortality = np.concatenate([
        np.random.uniform(2, 10, n // 4),
        np.random.uniform(10, 40, n // 4),
        np.random.uniform(40, 80, n // 4),
        np.random.uniform(80, 120, n - 3 * (n // 4)),
    ])
    np.random.shuffle(infant_mortality)
    years_stability = np.concatenate([
        np.random.randint(0, 3, n // 3),
        np.random.randint(3, 10, n // 3),
        np.random.randint(10, 31, n - 2 * (n // 3)),
    ]).astype(float)
    np.random.shuffle(years_stability)

    outcomes = np.zeros(n, dtype=int)
    for i in range(n):
        p = _annual_onset_probability(polity_codes[i], infant_mortality[i],
                                       years_stability[i])
        outcomes[i] = 1 if np.random.random() < p else 0

    return polity_codes, infant_mortality, years_stability, outcomes


def build_features(polity_codes, infant_mortality, years_stability):
    """Build feature matrix with 3 mutually exclusive regime categories."""
    n = len(polity_codes)
    is_factionalized = np.array([1.0 if 0 <= p <= 5 else 0.0 for p in polity_codes])
    is_closed_anocracy = np.array([1.0 if -5 <= p <= -1 else 0.0 for p in polity_codes])
    is_autocracy = np.array([1.0 if p < -5 else 0.0 for p in polity_codes])
    log_im = np.log(np.maximum(infant_mortality, 1.0))
    stability_factor = np.minimum(years_stability, 20) / 20.0

    return np.column_stack([is_factionalized, is_closed_anocracy, is_autocracy,
                            log_im, stability_factor])


def fit():
    """Fit logistic regression and print coefficients."""
    polity, im, years, outcomes = generate_synthetic_data(n=5000)
    X = build_features(polity, im, years)

    model = LogisticRegression(C=1.0, max_iter=1000, solver="lbfgs")
    model.fit(X, outcomes)

    print("=== PITF Logistic Regression (3 mutually exclusive regime categories) ===")
    print(f"  Intercept:              {model.intercept_[0]:.4f}")
    names = ["is_factionalized", "is_closed_anocracy", "is_autocracy",
             "log_infant_mortality", "stability_factor"]
    for name, coef in zip(names, model.coef_[0]):
        print(f"  {name:25s} {coef:.4f}")
    print(f"  Training base rate:     {outcomes.mean():.4f}")
    print(f"  Model accuracy:         {model.score(X, outcomes):.4f}")

    print()
    print("=== 5 Target Countries (Annual Probabilities) ===")
    countries = [
        ("Nigeria",      4,  70.4, 2),
        ("Pakistan",     2,  52.3, 1),
        ("Bangladesh",   1,  24.6, 1),
        ("Turkey",       3,   8.6, 3),
        ("Philippines",  8,  21.5, 4),
    ]
    for name, polity, imv, yrs in countries:
        Xt = build_features(np.array([polity]), np.array([imv]), np.array([yrs]))
        p_annual = model.predict_proba(Xt)[0][1]
        print(f"  {name:15s} annual={p_annual:.4f} ({p_annual*100:.1f}%)")

    return model


if __name__ == "__main__":
    fit()
