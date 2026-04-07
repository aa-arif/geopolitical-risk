"""
Brier score decomposition into uncertainty, reliability, and resolution.

Murphy (1973) decomposition:
BS = Uncertainty - Resolution + Reliability

- Uncertainty: variance of the base rate (irreducible)
- Reliability: how close predicted probabilities are to observed frequencies (lower = better)
- Resolution: how much the predicted probabilities vary from the base rate (higher = better)
"""

import numpy as np


def decompose_brier(predictions: list, actuals: list,
                    n_bins: int = 10) -> dict:
    """
    Decompose aggregate Brier score into components.

    Args:
        predictions: List of predicted probabilities
        actuals: List of actual outcomes (0 or 1)
        n_bins: Number of bins for grouping predictions

    Returns:
        Dict with uncertainty, reliability, resolution, and total
    """
    if not predictions:
        return {"uncertainty": 0, "reliability": 0, "resolution": 0, "total": 0}

    preds = np.array(predictions)
    acts = np.array(actuals)
    n = len(preds)

    # Base rate
    base_rate = acts.mean()
    uncertainty = base_rate * (1 - base_rate)

    # Bin predictions
    bin_edges = np.linspace(0, 1, n_bins + 1)
    reliability = 0.0
    resolution = 0.0

    for i in range(n_bins):
        mask = (preds >= bin_edges[i]) & (preds < bin_edges[i + 1])
        if i == n_bins - 1:  # Include right edge in last bin
            mask = (preds >= bin_edges[i]) & (preds <= bin_edges[i + 1])

        n_k = mask.sum()
        if n_k == 0:
            continue

        avg_pred = preds[mask].mean()
        avg_actual = acts[mask].mean()

        reliability += n_k * (avg_pred - avg_actual) ** 2
        resolution += n_k * (avg_actual - base_rate) ** 2

    reliability /= n
    resolution /= n

    total = uncertainty - resolution + reliability

    return {
        "uncertainty": float(uncertainty),
        "reliability": float(reliability),
        "resolution": float(resolution),
        "total": float(total),
    }
