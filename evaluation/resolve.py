"""
Automated prediction resolution.
When a 30-day prediction window closes, query ACLED (primary) or
GDELT conflict events (fallback) to determine if the instability
threshold was exceeded.
"""

from datetime import datetime, timezone

from utils.db import compute_event_threshold
from evaluation.brier import brier_score
from utils.logger import logger

# GDELT threshold: if no ACLED data, use GDELT conflict event count.
# GDELT events are less curated, so threshold is higher.
GDELT_INSTABILITY_THRESHOLD = 100


def resolve_expired_predictions(conn, country_iso3: str = None):
    """
    Check all expired prediction windows and resolve them using
    ACLED data (primary) or GDELT conflict events (fallback).

    Returns list of resolved prediction dicts.
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

        # Try ACLED first
        threshold = compute_event_threshold(conn, iso3, months=12)
        acled_cursor = conn.execute(
            """SELECT COUNT(*) as cnt FROM acled_events
               WHERE country_iso3 = ?
               AND event_date >= ? AND event_date <= ?""",
            (iso3, pred_date, window_end),
        )
        acled_count = acled_cursor.fetchone()["cnt"]

        # Try GDELT conflict events as fallback
        gdelt_cursor = conn.execute(
            """SELECT COUNT(*) as cnt FROM gdelt_conflict_events
               WHERE country_iso3 = ?
               AND event_date >= ? AND event_date <= ?""",
            (iso3, pred_date, window_end),
        )
        gdelt_count = gdelt_cursor.fetchone()["cnt"]

        # Determine resolution source and outcome
        resolution_source = None
        actual_outcome = None
        event_count = 0

        if acled_count > 0 and threshold > 0:
            # Primary: ACLED
            resolution_source = "acled"
            event_count = acled_count
            actual_outcome = 1 if acled_count > threshold else 0
        elif gdelt_count > 0:
            # Fallback: GDELT conflict events
            resolution_source = "gdelt"
            event_count = gdelt_count
            actual_outcome = 1 if gdelt_count > GDELT_INSTABILITY_THRESHOLD else 0
            logger.info(
                "Using GDELT fallback for %s pred %d (ACLED count=%d, GDELT count=%d)",
                iso3, pred["id"], acled_count, gdelt_count,
            )
        else:
            logger.warning(
                "No ACLED or GDELT data for %s (pred %d, %s to %s). Skipping.",
                iso3, pred["id"], pred_date, window_end,
            )
            continue

        bs = brier_score(calibrated_p, actual_outcome)

        conn.execute(
            """UPDATE predictions
               SET resolved = TRUE, actual_outcome = ?, brier_score = ?
               WHERE id = ?""",
            (actual_outcome, bs, pred["id"]),
        )

        logger.info(
            "Resolved prediction %d for %s (%s to %s) via %s: "
            "events=%d threshold=%s outcome=%d brier=%.4f",
            pred["id"], iso3, pred_date, window_end, resolution_source,
            event_count,
            f"{threshold:.0f}" if resolution_source == "acled" else f"{GDELT_INSTABILITY_THRESHOLD}",
            actual_outcome, bs,
        )

        resolved.append({
            "id": pred["id"],
            "country_iso3": iso3,
            "prediction_date": pred_date,
            "window_end_date": window_end,
            "event_count": event_count,
            "threshold": threshold if resolution_source == "acled" else GDELT_INSTABILITY_THRESHOLD,
            "actual_outcome": actual_outcome,
            "brier_score": bs,
            "resolution_source": resolution_source,
        })

    conn.commit()
    logger.info("Resolved %d expired predictions.", len(resolved))
    return resolved
