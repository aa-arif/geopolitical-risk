"""
Automated prediction resolution.

When a 30-day prediction window closes, determine if the predicted
event type occurred by checking the unified conflict_events table.

Source-agnostic: resolution queries conflict_events (which aggregates
GDELT, internal LLM-classified events, and any future sources) using
the EVENT_TYPE_RESOLUTION config to determine per-type thresholds
and counting rules.

For 'composite' predictions (V1 backward compat), resolves using
ACE events with fatalities (the closest match to the original
"significant political instability" criterion).
"""

from datetime import datetime, timedelta

import numpy as np

from config.settings import EVENT_TYPE_RESOLUTION, CALIBRATED_EVENT_TYPES
from utils.db import (
    get_event_count, compute_event_threshold_by_category,
)
from evaluation.brier import brier_score
from utils.logger import logger


def _resolve_by_threshold(conn, iso3, pred_date, window_end,
                           event_category, config,
                           stored_threshold=None):
    """
    Resolve a prediction using the percentile_90 threshold method.

    Uses stored_threshold if available (committed at prediction time
    to prevent look-ahead). Falls back to recomputation for older
    predictions that predate threshold storage.

    Returns resolution dict or None if no data.
    """
    require_fatalities = config.get("require_fatalities", False)

    event_count = get_event_count(
        conn, iso3, pred_date, window_end,
        event_category=event_category,
        require_fatalities=require_fatalities,
    )

    # Prefer stored threshold (no look-ahead); recompute as fallback
    if stored_threshold is not None and stored_threshold > 0:
        threshold = stored_threshold
    else:
        threshold = compute_event_threshold_by_category(
            conn, iso3, event_category,
            require_fatalities=require_fatalities,
            months=12, before_date=pred_date,
        )

    if threshold <= 0 and event_count == 0:
        return None  # no data to resolve against

    actual_outcome = 1 if event_count > threshold else 0
    return {
        "actual_outcome": actual_outcome,
        "event_count": event_count,
        "threshold": threshold,
        "resolution_source": f"conflict_events_{event_category}",
    }


def _resolve_by_occurrence(conn, iso3, pred_date, window_end,
                            event_category, config):
    """
    Resolve a prediction using the occurrence method (binary: did it happen?).
    Returns resolution dict or None.
    """
    event_count = get_event_count(
        conn, iso3, pred_date, window_end,
        event_category=event_category,
    )

    actual_outcome = 1 if event_count > 0 else 0
    return {
        "actual_outcome": actual_outcome,
        "event_count": event_count,
        "threshold": 0,
        "resolution_source": f"conflict_events_{event_category}",
    }


def compute_ingest_deviation(conn, iso3, pred_date, window_end, event_category):
    """
    Measure how much the ingest volume during the prediction window
    deviates from the 90-day baseline prior to the window.

    Uses pulled_at (when our pipeline recorded the event) not event_date
    (when the event actually happened), so we detect pipeline outages
    rather than real-world quiet periods.

    Returns (sigma, confidence_flag) where:
      sigma = (window_mean - baseline_mean) / baseline_std
      confidence_flag in {'high', 'low', 'unknown'}

    Rules:
    - If baseline has fewer than 60 days with any ingest, return (None, 'unknown')
    - If |sigma| <= 2.0, return (sigma, 'high')
    - Otherwise return (sigma, 'low')
    """
    pred_dt = datetime.strptime(pred_date, "%Y-%m-%d")
    window_dt = datetime.strptime(window_end, "%Y-%m-%d")
    baseline_start = (pred_dt - timedelta(days=90)).strftime("%Y-%m-%d")

    # Baseline: pulled_at in [pred_date - 90, pred_date)
    baseline_rows = conn.execute(
        """SELECT date(pulled_at) as d, COUNT(*) as cnt
           FROM conflict_events
           WHERE country_iso3 = ? AND event_category = ?
           AND date(pulled_at) >= ? AND date(pulled_at) < ?
           GROUP BY date(pulled_at)""",
        (iso3, event_category, baseline_start, pred_date),
    ).fetchall()
    baseline_by_day = {r["d"]: r["cnt"] for r in baseline_rows}

    # "60 days with any ingest" check: distinct non-zero days in the 90-day span
    if len(baseline_by_day) < 60:
        return (None, "unknown")

    # Fill in zero-ingest days across the full 90-day baseline span
    baseline_counts = []
    for offset in range(90):
        d = (pred_dt - timedelta(days=90 - offset)).strftime("%Y-%m-%d")
        baseline_counts.append(baseline_by_day.get(d, 0))

    baseline_arr = np.array(baseline_counts, dtype=float)
    baseline_mean = baseline_arr.mean()
    baseline_std = baseline_arr.std()

    if baseline_std == 0:
        return (None, "unknown")

    # Window: pulled_at in [pred_date, window_end]
    window_rows = conn.execute(
        """SELECT date(pulled_at) as d, COUNT(*) as cnt
           FROM conflict_events
           WHERE country_iso3 = ? AND event_category = ?
           AND date(pulled_at) >= ? AND date(pulled_at) <= ?
           GROUP BY date(pulled_at)""",
        (iso3, event_category, pred_date, window_end),
    ).fetchall()
    window_by_day = {r["d"]: r["cnt"] for r in window_rows}

    window_span = (window_dt - pred_dt).days + 1
    window_counts = []
    for offset in range(window_span):
        d = (pred_dt + timedelta(days=offset)).strftime("%Y-%m-%d")
        window_counts.append(window_by_day.get(d, 0))

    window_mean = float(np.array(window_counts, dtype=float).mean())
    sigma = float((window_mean - baseline_mean) / baseline_std)

    if abs(sigma) <= 2.0:
        return (sigma, "high")
    return (sigma, "low")


def _resolve_composite_legacy(conn, iso3, pred_date, window_end, stored_threshold):
    """
    Resolve a composite/V1 prediction using conflict_events (ACE with fatalities).

    Source-agnostic: counts ACE events from all sources in the unified
    conflict_events table (GDELT, internal LLM-classified, etc.) as the
    closest match to the original V1 "significant political instability"
    criterion.
    """
    ace_count = get_event_count(
        conn, iso3, pred_date, window_end,
        event_category="ACE", require_fatalities=True,
    )

    if ace_count > 0 and stored_threshold and stored_threshold > 0:
        return {
            "actual_outcome": 1 if ace_count > stored_threshold else 0,
            "event_count": ace_count,
            "threshold": stored_threshold,
            "resolution_source": "conflict_events_ACE",
        }

    # Last resort: GDELT conflict events
    gdelt_cursor = conn.execute(
        """SELECT COALESCE(SUM(num_articles), 0) as cnt
           FROM gdelt_conflict_events
           WHERE country_iso3 = ?
           AND event_date >= ? AND event_date <= ?""",
        (iso3, pred_date, window_end),
    )
    gdelt_count = gdelt_cursor.fetchone()["cnt"]
    if gdelt_count > 0:
        return {
            "actual_outcome": 1 if gdelt_count > 100 else 0,
            "event_count": gdelt_count,
            "threshold": 100,
            "resolution_source": "gdelt_legacy",
        }

    return None


def resolve_expired_predictions(conn, country_iso3: str = None):
    """
    Check all expired prediction windows and resolve them.

    Supports both:
    - V2 per-event-type predictions (resolved against EVENT_TYPE_RESOLUTION config)
    - V1 composite predictions (resolved using ACE events from conflict_events,
      with a final GDELT fallback for very old predictions)

    Returns list of resolved prediction dicts.
    """
    now = datetime.now().astimezone().strftime("%Y-%m-%d")

    params = [now]
    country_filter = ""
    if country_iso3:
        country_filter = "AND country_iso3 = ?"
        params.append(country_iso3)

    cursor = conn.execute(
        f"""SELECT id, country_iso3, prediction_date, window_end_date,
                   calibrated_probability, event_threshold,
                   COALESCE(event_type, 'composite') as event_type
            FROM predictions
            WHERE resolved = FALSE AND window_end_date <= ?
            {country_filter}""",
        params,
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
        event_type = pred["event_type"]

        resolution = None

        if event_type == "composite":
            # V1 backward compat
            resolution = _resolve_composite_legacy(
                conn, iso3, pred_date, window_end, pred.get("event_threshold"),
            )
        elif event_type in EVENT_TYPE_RESOLUTION:
            config = EVENT_TYPE_RESOLUTION[event_type]
            method = config.get("threshold_method")

            if method == "percentile_90":
                resolution = _resolve_by_threshold(
                    conn, iso3, pred_date, window_end, event_type, config,
                    stored_threshold=pred.get("event_threshold"),
                )
            elif method == "occurrence":
                resolution = _resolve_by_occurrence(
                    conn, iso3, pred_date, window_end, event_type, config,
                )
            else:
                # PSS, CID: no automated resolution
                logger.debug(
                    "Skipping resolution for %s/%s (no threshold_method).",
                    iso3, event_type,
                )
                continue
        else:
            logger.warning("Unknown event_type '%s' for prediction %d.", event_type, pred["id"])
            continue

        if resolution is None:
            logger.warning(
                "No event data for %s/%s (pred %d, %s to %s). Skipping.",
                iso3, event_type, pred["id"], pred_date, window_end,
            )
            continue

        actual_outcome = resolution["actual_outcome"]
        bs = brier_score(calibrated_p, actual_outcome)

        # Ingest-volume confidence flag (P0.2): composite predictions
        # use ACE as the ingest proxy to match _resolve_composite_legacy.
        ingest_category = "ACE" if event_type == "composite" else event_type
        sigma, confidence = compute_ingest_deviation(
            conn, iso3, pred_date, window_end, ingest_category,
        )

        conn.execute(
            """UPDATE predictions
               SET resolved = TRUE, actual_outcome = ?, brier_score = ?,
                   ingest_confidence = ?, ingest_deviation_sigma = ?
               WHERE id = ?""",
            (actual_outcome, bs, confidence, sigma, pred["id"]),
        )

        sigma_str = f"{sigma:.2f}" if sigma is not None else "n/a"
        logger.info(
            "Resolved prediction %d for %s/%s (%s to %s) via %s: "
            "events=%d threshold=%s outcome=%d brier=%.4f "
            "ingest_confidence=%s sigma=%s",
            pred["id"], iso3, event_type, pred_date, window_end,
            resolution["resolution_source"],
            resolution["event_count"], resolution["threshold"],
            actual_outcome, bs, confidence, sigma_str,
        )

        resolved.append({
            "id": pred["id"],
            "country_iso3": iso3,
            "event_type": event_type,
            "prediction_date": pred_date,
            "window_end_date": window_end,
            "event_count": resolution["event_count"],
            "threshold": resolution["threshold"],
            "actual_outcome": actual_outcome,
            "brier_score": bs,
            "resolution_source": resolution["resolution_source"],
            "ingest_confidence": confidence,
            "ingest_deviation_sigma": sigma,
        })

    conn.commit()
    logger.info("Resolved %d expired predictions.", len(resolved))
    return resolved
