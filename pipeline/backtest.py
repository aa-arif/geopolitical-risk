"""
Backtesting framework.
Replays historical data through the prediction pipeline to evaluate
accuracy on known outcomes.
"""

import json
from datetime import datetime, timedelta, timezone

from config.settings import load_country_config
from utils.db import get_connection, get_resolved_predictions
from evaluation.brier import brier_score, brier_score_aggregate
from evaluation.decompose import decompose_brier
from evaluation.calibration_curve import compute_calibration_curve
from utils.logger import logger


def backtest_country(country_name: str) -> dict:
    """
    Run backtesting analysis for a country using resolved predictions.

    Returns backtest results with Brier scores and calibration data.
    """
    conn = get_connection()
    config = load_country_config(country_name)
    iso3 = config["iso3"]

    resolved = get_resolved_predictions(conn, iso3)
    conn.close()

    if not resolved:
        return {"country": iso3, "n_resolved": 0, "message": "No resolved predictions."}

    preds = [r["calibrated_probability"] for r in resolved if r["actual_outcome"] is not None]
    actuals = [r["actual_outcome"] for r in resolved if r["actual_outcome"] is not None]

    if not preds:
        return {"country": iso3, "n_resolved": 0, "message": "No outcomes recorded."}

    aggregate = brier_score_aggregate(preds, actuals)
    decomposition = decompose_brier(preds, actuals)
    calibration = compute_calibration_curve(preds, actuals)

    result = {
        "country": iso3,
        "n_resolved": len(preds),
        "brier_aggregate": aggregate,
        "brier_decomposition": decomposition,
        "calibration": calibration,
        "base_rate": sum(actuals) / len(actuals),
    }

    logger.info(
        "Backtest %s: %d predictions, Brier=%.4f, base_rate=%.2f",
        iso3, len(preds), aggregate, result["base_rate"],
    )

    return result
