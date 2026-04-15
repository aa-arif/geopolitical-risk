"""
Change detection and alert generation for per-event-type predictions.

An alert IS a brief triggered by a change. The two are built together:
- detect_changes() identifies significant probability shifts per event type
- generate_alert_brief() produces a formatted brief explaining the shift
- Both stored in the alerts table for downstream delivery (email, dashboard)

Trigger conditions:
1. Absolute delta > 0.10 (10 percentage points in one day)
2. Probability crosses a level boundary (0.20, 0.40, 0.60)
3. New dominant event type (highest-probability type changed)
"""

import json
import sqlite3
from datetime import datetime

from config.settings import EVENT_TYPE_RESOLUTION, CALIBRATED_EVENT_TYPES
from utils.db import get_connection
from utils.logger import logger


LEVEL_BOUNDARIES = [0.20, 0.40, 0.60]
DELTA_THRESHOLD = 0.10


def _get_latest_predictions(conn, country_iso3, event_type=None):
    """Get the two most recent predictions for a country+type pair."""
    et_filter = "AND event_type = ?" if event_type else "AND event_type = 'composite'"
    params = [country_iso3]
    if event_type:
        params.append(event_type)

    cursor = conn.execute(
        f"""SELECT prediction_date, calibrated_probability, event_type,
                   reasoning_summary
            FROM predictions
            WHERE country_iso3 = ? {et_filter}
            ORDER BY prediction_date DESC
            LIMIT 2""",
        params,
    )
    return [dict(r) for r in cursor.fetchall()]


def detect_changes_for_country(conn, country_iso3):
    """
    Compare today's predictions to yesterday's across all event types.

    Returns list of trigger dicts, one per triggered alert.
    """
    triggers = []
    event_types = ["composite"] + list(EVENT_TYPE_RESOLUTION.keys())

    for et in event_types:
        rows = _get_latest_predictions(conn, country_iso3, et)
        if len(rows) < 2:
            continue

        today = rows[0]
        yesterday = rows[1]

        if today["prediction_date"] == yesterday["prediction_date"]:
            continue  # same day, no change to detect

        curr = today["calibrated_probability"]
        prev = yesterday["calibrated_probability"]
        delta = curr - prev

        # Trigger 1: Absolute delta spike
        if abs(delta) > DELTA_THRESHOLD:
            triggers.append({
                "country_iso3": country_iso3,
                "event_type": et,
                "trigger_type": "delta_spike",
                "previous_probability": prev,
                "current_probability": curr,
                "delta": delta,
                "direction": "up" if delta > 0 else "down",
                "reasoning_summary": today.get("reasoning_summary", ""),
            })

        # Trigger 2: Level boundary crossing
        for boundary in LEVEL_BOUNDARIES:
            crossed_up = prev < boundary <= curr
            crossed_down = curr < boundary <= prev
            if crossed_up or crossed_down:
                triggers.append({
                    "country_iso3": country_iso3,
                    "event_type": et,
                    "trigger_type": "threshold_cross",
                    "previous_probability": prev,
                    "current_probability": curr,
                    "delta": delta,
                    "direction": "up" if crossed_up else "down",
                    "boundary": boundary,
                    "reasoning_summary": today.get("reasoning_summary", ""),
                })

    # Trigger 3: Dominant event type changed
    today_scores = {}
    yesterday_scores = {}
    for et in CALIBRATED_EVENT_TYPES:
        rows = _get_latest_predictions(conn, country_iso3, et)
        if len(rows) >= 1:
            today_scores[et] = rows[0]["calibrated_probability"]
        if len(rows) >= 2:
            yesterday_scores[et] = rows[1]["calibrated_probability"]

    if today_scores and yesterday_scores:
        today_dominant = max(today_scores, key=today_scores.get)
        yesterday_dominant = max(yesterday_scores, key=yesterday_scores.get)
        if today_dominant != yesterday_dominant:
            triggers.append({
                "country_iso3": country_iso3,
                "event_type": today_dominant,
                "trigger_type": "dominant_shift",
                "previous_probability": yesterday_scores.get(today_dominant, 0),
                "current_probability": today_scores[today_dominant],
                "delta": today_scores[today_dominant] - yesterday_scores.get(today_dominant, 0),
                "direction": "shift",
                "previous_dominant": yesterday_dominant,
                "new_dominant": today_dominant,
            })

    return triggers


def store_alerts(conn, triggers):
    """Store alert triggers in the alerts table."""
    alert_date = datetime.now().astimezone().strftime("%Y-%m-%d")

    for t in triggers:
        brief = _format_trigger_text(t)
        try:
            conn.execute(
                """INSERT INTO alerts
                   (country_iso3, event_type, alert_date, trigger_type,
                    previous_probability, current_probability, delta,
                    brief_text, reasoning_summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (t["country_iso3"], t["event_type"], alert_date,
                 t["trigger_type"], t["previous_probability"],
                 t["current_probability"], t["delta"], brief,
                 t.get("reasoning_summary", "")),
            )
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    return len(triggers)


def _format_trigger_text(trigger):
    """Format a trigger dict into a human-readable alert line."""
    iso3 = trigger["country_iso3"]
    et = trigger["event_type"]
    et_name = EVENT_TYPE_RESOLUTION.get(et, {}).get("name", et)
    prev = trigger["previous_probability"]
    curr = trigger["current_probability"]
    delta = trigger["delta"]

    if trigger["trigger_type"] == "delta_spike":
        direction = "rose" if delta > 0 else "fell"
        return (f"{iso3} {et_name}: {direction} from {prev:.0%} to {curr:.0%} "
                f"({delta:+.1%} in one day)")

    elif trigger["trigger_type"] == "threshold_cross":
        boundary = trigger.get("boundary", 0)
        level_names = {0.20: "MEDIUM", 0.40: "HIGH", 0.60: "CRITICAL"}
        level = level_names.get(boundary, f"{boundary:.0%}")
        direction = "crossed into" if trigger["direction"] == "up" else "dropped below"
        return f"{iso3} {et_name}: {direction} {level} ({prev:.0%} -> {curr:.0%})"

    elif trigger["trigger_type"] == "dominant_shift":
        old = EVENT_TYPE_RESOLUTION.get(trigger.get("previous_dominant", ""), {}).get("name", trigger.get("previous_dominant", ""))
        new = EVENT_TYPE_RESOLUTION.get(trigger.get("new_dominant", ""), {}).get("name", trigger.get("new_dominant", ""))
        return f"{iso3}: Dominant risk shifted from {old} to {new} ({curr:.0%})"

    return f"{iso3} {et}: {prev:.0%} -> {curr:.0%} ({delta:+.1%})"


def detect_and_store_all(conn, countries_iso3_list):
    """
    Run change detection for all countries, store alerts.
    Returns list of all triggers.
    """
    all_triggers = []
    for iso3 in countries_iso3_list:
        triggers = detect_changes_for_country(conn, iso3)
        if triggers:
            all_triggers.extend(triggers)
            for t in triggers:
                logger.info("Alert: %s", _format_trigger_text(t))

    if all_triggers:
        store_alerts(conn, all_triggers)
        logger.info("Stored %d alerts across %d countries.",
                    len(all_triggers),
                    len(set(t["country_iso3"] for t in all_triggers)))
    else:
        logger.info("No significant changes detected.")

    return all_triggers
