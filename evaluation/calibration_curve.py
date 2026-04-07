"""
Calibration curve computation.
Bins predictions into buckets and compares predicted vs actual frequencies.
A perfectly calibrated system has predicted == actual for every bin.
"""

import numpy as np


def compute_calibration_curve(predictions: list, actuals: list,
                               n_bins: int = 10) -> dict:
    """
    Compute calibration curve data.

    Args:
        predictions: List of predicted probabilities
        actuals: List of actual outcomes (0 or 1)
        n_bins: Number of bins

    Returns:
        Dict with bin data for plotting calibration curve
    """
    if not predictions:
        return {"bins": [], "n_predictions": 0}

    preds = np.array(predictions)
    acts = np.array(actuals)

    bin_edges = np.linspace(0, 1, n_bins + 1)
    bins = []

    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        if i == n_bins - 1:
            mask = (preds >= lo) & (preds <= hi)
        else:
            mask = (preds >= lo) & (preds < hi)

        n_k = mask.sum()
        if n_k == 0:
            continue

        avg_predicted = float(preds[mask].mean())
        avg_actual = float(acts[mask].mean())

        bins.append({
            "bin_lower": float(lo),
            "bin_upper": float(hi),
            "bin_midpoint": float((lo + hi) / 2),
            "avg_predicted": avg_predicted,
            "avg_actual": avg_actual,
            "count": int(n_k),
            "calibration_error": abs(avg_predicted - avg_actual),
        })

    # Expected Calibration Error (ECE)
    total = len(preds)
    ece = sum(b["count"] * b["calibration_error"] for b in bins) / total if total > 0 else 0

    return {
        "bins": bins,
        "n_predictions": total,
        "expected_calibration_error": float(ece),
    }
