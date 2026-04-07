"""
Platt scaling calibration for prediction probabilities.

Uses a logistic regression fitted on (raw_probability, actual_outcome)
pairs from resolved predictions. If insufficient data exists, returns
the input probability unchanged.
"""

import pickle
from pathlib import Path
from sklearn.linear_model import LogisticRegression
import numpy as np

CALIBRATION_MODEL_PATH = Path(__file__).parent.parent / "data" / "calibration_model.pkl"

# Minimum resolved predictions needed before applying calibration
MIN_CALIBRATION_SAMPLES = 30


def calibrate(p: float, model: LogisticRegression = None) -> float:
    """
    Apply Platt scaling calibration if a fitted model exists.

    Args:
        p: Raw probability
        model: Optional pre-loaded calibration model

    Returns:
        Calibrated probability (or original if no model)
    """
    if model is None:
        model = _load_model()

    if model is None:
        return p

    # Platt scaling: logistic regression on log-odds
    X = np.array([[p]])
    calibrated = model.predict_proba(X)[0][1]
    return float(max(0.01, min(0.99, calibrated)))


def fit_calibration_model(predictions: list, outcomes: list) -> LogisticRegression:
    """
    Fit a Platt scaling model on resolved predictions.

    Args:
        predictions: List of raw probability values
        outcomes: List of actual outcomes (0 or 1)

    Returns:
        Fitted LogisticRegression model, or None if insufficient data
    """
    if len(predictions) < MIN_CALIBRATION_SAMPLES:
        return None

    X = np.array(predictions).reshape(-1, 1)
    y = np.array(outcomes)

    model = LogisticRegression(C=1.0)
    model.fit(X, y)

    # Save model
    CALIBRATION_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CALIBRATION_MODEL_PATH, "wb") as f:
        pickle.dump(model, f)

    return model


def _load_model() -> LogisticRegression:
    """Load calibration model from disk if it exists."""
    if not CALIBRATION_MODEL_PATH.exists():
        return None
    try:
        with open(CALIBRATION_MODEL_PATH, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None
