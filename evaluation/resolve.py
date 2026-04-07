"""
Automated prediction resolution.
When a 30-day prediction window closes, query ACLED to determine
if the instability threshold was exceeded.
"""

from datetime import datetime, timezone

from config.settings import load_country_config, COUNTRIES
from utils.db import compute_event_threshold
from evaluation.brier import brier_score
from utils.logger import logger


def resolve_expired_predictions(conn, country_iso3: str = None):
    """
    Check all expired prediction windows and resolve them
    by comparing ACLED event counts against the 90th percentile threshold.

    Args:
        conn: Database connection
        country_iso3: Optional filter by country. If None, resolves all.

    Returns:
        List of resolved prediction dicts.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if country_iso3:
        cursor = conn.execute(
            """SELECT id, country_iso3, prediction_date, window_end_date,
                      calibrated_probability
               FROM predictions
               WHERE resolved = FALSE AND window_end_date <= ?
               AND country_iso3 = ?""",
            (now, country_iso3),
        )
    else:
        cursor = conn.execute(
            """SELECT id, country_iso3, prediction_date, window_end_date,
                      calibrated_probability
               FROM predictions
               WHERE resolved = FALSE AND window_end_date <= ?""",
            (now,),
        )

    expired = [dict(row) for row in cursor.fetchall()]

    if not expired:
        logger.info("No expired prediction windows to resolve.")
        return []

    resolved = []
    for pred in expired:
        iso3 = pred["country_iso3"]
        pred_date = pred["prediction_date"]
        window_end = pred["window_end_date"]
        calibrated_p = pred["calibrated_probability"]

        # Compute event threshold for this country
        threshold = compute_event_threshold(conn, iso3, months=12)

        # Count ACLED events during the prediction window
        event_cursor = conn.execute(
            """SELECT COUNT(*) as cnt FROM acled_events
               WHERE country_iso3 = ?
               AND event_date >= ? AND event_date <= ?""",
            (iso3, pred_date, window_end),
        )
        event_count = event_cursor.fetchone()["cnt"]

        # Determine outcome: 1 if events exceeded threshold, 0 otherwise
        # If no threshold data (no ACLED history), leave unresolved
        if threshold <= 0:
            logger.warning(
                "No ACLED threshold for %s (pred %s). Skipping resolution.",
                iso3, pred["id"],
            )
            continue

        actual_outcome = 1 if event_count > threshold else 0
        bs = brier_score(calibrated_p, actual_outcome)

        # Update the prediction row
        conn.execute(
            """UPDATE predictions
               SET resolved = TRUE, actual_outcome = ?, brier_score = ?
               WHERE id = ?""",
            (actual_outcome, bs, pred["id"]),
        )

        logger.info(
            "Resolved prediction %d for %s (%s to %s): "
            "events=%d threshold=%.0f outcome=%d brier=%.4f",
            pred["id"], iso3, pred_date, window_end,
            event_count, threshold, actual_outcome, bs,
        )

        resolved.append({
            "id": pred["id"],
            "country_iso3": iso3,
            "prediction_date": pred_date,
            "window_end_date": window_end,
            "event_count": event_count,
            "threshold": threshold,
            "actual_outcome": actual_outcome,
            "brier_score": bs,
        })

    conn.commit()
    logger.info("Resolved %d expired predictions.", len(resolved))
    return resolved
