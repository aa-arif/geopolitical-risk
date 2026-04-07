"""
Compare Track A, Track B, and fused prediction accuracy.
For each resolved prediction, computes per-track Brier scores.
"""

from evaluation.brier import brier_score, brier_score_aggregate
from utils.db import get_resolved_predictions
from utils.logger import logger


def compare_tracks(db_conn, country_iso3: str = None) -> dict:
    """
    Compare accuracy of Track A, Track B, and fused predictions.

    Args:
        db_conn: Database connection
        country_iso3: Optional filter by country

    Returns:
        Dict with per-track Brier scores and comparison
    """
    resolved = get_resolved_predictions(db_conn, country_iso3)

    if not resolved:
        return {
            "n_resolved": 0,
            "track_a_brier": None,
            "track_b_brier": None,
            "fused_brier": None,
            "best_track": None,
        }

    track_a_preds = []
    track_b_preds = []
    fused_preds = []
    actuals = []

    for pred in resolved:
        if pred["actual_outcome"] is None:
            continue
        actual = int(pred["actual_outcome"])
        actuals.append(actual)
        track_a_preds.append(pred["track_a_probability"])
        track_b_preds.append(pred["track_b_probability"])
        fused_preds.append(pred["fused_probability"])

    if not actuals:
        return {"n_resolved": 0, "track_a_brier": None,
                "track_b_brier": None, "fused_brier": None, "best_track": None}

    a_brier = brier_score_aggregate(track_a_preds, actuals)
    b_brier = brier_score_aggregate(track_b_preds, actuals)
    f_brier = brier_score_aggregate(fused_preds, actuals)

    scores = {"track_a": a_brier, "track_b": b_brier, "fused": f_brier}
    best = min(scores, key=scores.get)

    result = {
        "n_resolved": len(actuals),
        "track_a_brier": a_brier,
        "track_b_brier": b_brier,
        "fused_brier": f_brier,
        "best_track": best,
        "per_prediction": [
            {
                "country": pred["country_iso3"],
                "date": pred["prediction_date"],
                "track_a": pred["track_a_probability"],
                "track_b": pred["track_b_probability"],
                "fused": pred["fused_probability"],
                "actual": pred["actual_outcome"],
                "brier_a": brier_score(pred["track_a_probability"], pred["actual_outcome"]),
                "brier_b": brier_score(pred["track_b_probability"], pred["actual_outcome"]),
                "brier_fused": brier_score(pred["fused_probability"], pred["actual_outcome"]),
            }
            for pred in resolved
            if pred["actual_outcome"] is not None
        ],
    }

    logger.info(
        "Track comparison (%d resolved): A=%.4f, B=%.4f, Fused=%.4f, Best=%s",
        len(actuals), a_brier, b_brier, f_brier, best,
    )

    return result
