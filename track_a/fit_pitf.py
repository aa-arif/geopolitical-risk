"""
Fit PITF logistic regression on synthetic data generated from
published findings in Goldstone et al. (2010).

Known relationships from the literature:
- Factionalized partial democracies (polity 1-7) are up to 30x more
  likely to experience instability than consolidated democracies
- Full autocracies (-10 to -6) have moderate risk
- Anocracies (-5 to +5) are highest risk category
- Higher infant mortality correlates with risk (log-linear)
- More years of stability reduces risk with diminishing returns
- Annual base rate across all countries: ~3-4%
- Highest-risk countries (factionalized anocracies, high IM): ~10-15% annual
- Consolidated democracies with low IM: well under 1% annual

Usage:
    python -m track_a.fit_pitf
"""

import numpy as np
from sklearn.linear_model import LogisticRegression

np.random.seed(42)


def _monthly_onset_probability(polity_code, infant_mortality, years_stability):
    """
    Ground-truth probability function based on published PITF findings.
    Returns 30-DAY instability onset probability.

    Calibrated so that:
    - Highest risk (factionalized anocracy, high IM, recent): ~15-25% per month
    - Moderate risk (anocracy, medium IM): ~8-15%
    - Low risk (democracy, low IM, stable): ~1-3%
    - Very low (consolidated democracy): <1%
    """
    # Regime type risk multiplier (relative to consolidated democracy baseline)
    if -5 <= polity_code <= 5:
        # Anocracy: highest risk
        if 0 <= polity_code <= 5:
            # Factionalized partial democracy: up to 30x baseline
            regime_mult = 20.0 + 10.0 * (1.0 - abs(polity_code - 3) / 5.0)
        else:
            # Closed anocracy (-5 to -1): high but less than factionalized
            regime_mult = 8.0 + 4.0 * (1.0 - abs(polity_code + 2) / 5.0)
    elif polity_code < -5:
        # Full autocracy: moderate risk (higher than democracy, lower than anocracy)
        regime_mult = 3.0 + 2.0 * (1.0 - abs(polity_code + 8) / 5.0)
    else:
        # Democracy (6+): lowest risk, decreasing with higher scores
        regime_mult = max(0.3, 1.5 - 0.15 * (polity_code - 6))

    # Infant mortality: log-linear relationship
    # Low IM (~5) -> mult ~0.5, Medium IM (~40) -> mult ~1.0, High IM (~100) -> mult ~1.5
    im_mult = 0.3 + 0.25 * np.log(max(infant_mortality, 2.0))

    # Stability years: diminishing returns
    # 0 years -> mult 2.0 (just had instability), 10+ years -> mult ~0.5
    stability_mult = 0.4 + 1.6 * np.exp(-0.2 * years_stability)

    # Combine: consolidated democracy baseline is ~0.5% per 30-day window
    # This produces ~15-25% for highest-risk factionalized anocracies,
    # ~8-15% for moderate, ~1-4% for democracies
    baseline_30day = 0.005
    p = baseline_30day * regime_mult * im_mult * stability_mult

    return np.clip(p, 0.001, 0.40)


def generate_synthetic_data(n=2000):
    """Generate synthetic country-year observations from PITF relationships."""
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

    # Generate outcomes probabilistically based on true function
    outcomes = np.zeros(n, dtype=int)
    for i in range(n):
        p = _monthly_onset_probability(polity_codes[i], infant_mortality[i],
                                        years_stability[i])
        outcomes[i] = 1 if np.random.random() < p else 0

    return polity_codes, infant_mortality, years_stability, outcomes


def build_features(polity_codes, infant_mortality, years_stability):
    """Build feature matrix matching pitf_model.py structure."""
    n = len(polity_codes)
    is_anocracy = np.array([1.0 if -5 <= p <= 5 else 0.0 for p in polity_codes])
    is_factionalized = np.array([1.0 if 0 <= p <= 5 else 0.0 for p in polity_codes])
    log_im = np.log(np.maximum(infant_mortality, 1.0))
    stability_factor = np.minimum(years_stability, 20) / 20.0

    X = np.column_stack([is_anocracy, is_factionalized, log_im, stability_factor])
    return X


def fit():
    """Fit logistic regression and print coefficients."""
    polity, im, years, outcomes = generate_synthetic_data(n=2000)
    X = build_features(polity, im, years)

    model = LogisticRegression(C=1.0, max_iter=1000, solver="lbfgs")
    model.fit(X, outcomes)

    print("=== PITF Logistic Regression Coefficients (ANNUAL model) ===")
    print(f"  Intercept:          {model.intercept_[0]:.4f}")
    names = ["is_anocracy", "is_factionalized", "log_infant_mortality", "stability_factor"]
    for name, coef in zip(names, model.coef_[0]):
        print(f"  {name:25s} {coef:.4f}")

    print()
    print("=== Verification: 30-day probabilities ===")
    test_cases = [
        ("Factionalized anocracy, high IM, recent", 4, 70, 2),
        ("Factionalized anocracy, med IM, recent", 2, 50, 1),
        ("Closed anocracy, high IM, recent", -3, 60, 2),
        ("Full autocracy, high IM, moderate", -8, 55, 5),
        ("Democracy, low IM, stable", 8, 10, 10),
        ("Strong democracy, very low IM, stable", 10, 5, 15),
    ]
    for desc, polity, im_val, yrs in test_cases:
        X_test = build_features(np.array([polity]), np.array([im_val]), np.array([yrs]))
        p_30 = model.predict_proba(X_test)[0][1]
        print(f"  {desc:45s} P(30d)={p_30:.4f} ({p_30*100:.1f}%)")

    # Print the base rate
    print(f"\n  Training data base rate: {outcomes.mean():.4f}")
    print(f"  Model accuracy: {model.score(X, outcomes):.4f}")

    return model


if __name__ == "__main__":
    fit()
