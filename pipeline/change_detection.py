"""
Change detection: compares current predictions against previous cycle
to identify significant probability shifts and generate alerts.
"""

import json
from datetime import datetime, timezone

from config.settings import COUNTRIES, load_country_config
from utils.db import get_connection, get_prediction_history, get_recent_reasoning_chains
from track_b.extraction import summarize_reasoning_chains
from utils.logger import logger


def detect_changes(conn, country_iso3: str, threshold_pct: float = 5.0) -> dict:
    """
    Compare current prediction against the previous one for a country.

    Returns dict with delta info and significance flag.
    """
    history = get_prediction_history(conn, country_iso3, limit=2)

    if len(history) < 2:
        return {
            "country_iso3": country_iso3,
            "previous_probability": None,
            "current_probability": history[0]["calibrated_probability"] if history else None,
            "delta": 0.0,
            "delta_pct": 0.0,
            "direction": "stable",
            "is_significant": False,
        }

    current = history[0]["calibrated_probability"]
    previous = history[1]["calibrated_probability"]
    delta = current - previous
    delta_pct = delta * 100

    if abs(delta_pct) > threshold_pct:
        direction = "rising" if delta > 0 else "falling"
        is_significant = True
    else:
        direction = "stable"
        is_significant = False

    return {
        "country_iso3": country_iso3,
        "previous_probability": previous,
        "current_probability": current,
        "delta": delta,
        "delta_pct": delta_pct,
        "direction": direction,
        "is_significant": is_significant,
    }


def detect_all_changes(conn, threshold_pct: float = 2.0) -> list:
    """
    Run change detection for all active countries.
    Returns list sorted by absolute delta descending.
    """
    changes = []
    for country_name in COUNTRIES:
        config = load_country_config(country_name)
        iso3 = config["iso3"]
        change = detect_changes(conn, iso3, threshold_pct)
        change["name"] = config["name"]
        changes.append(change)

    changes.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return changes


def generate_change_alert(change_data: dict, country_config: dict,
                          conn) -> str:
    """
    Generate a human-readable alert for a significant change.
    Looks at recent reasoning chains to explain what drove the shift.
    """
    iso3 = change_data["country_iso3"]
    prev = change_data["previous_probability"]
    curr = change_data["current_probability"]
    delta_pp = change_data["delta_pct"]
    direction = "rose" if delta_pp > 0 else "fell"

    # Get recent reasoning chains for new factors
    chains = get_recent_reasoning_chains(conn, iso3, days=3)
    new_factors = []
    for chain in chains[:5]:
        try:
            chain_data = json.loads(chain["chain_json"]) if isinstance(chain.get("chain_json"), str) else chain
            for f in chain_data.get("causal_factors", [])[:3]:
                factor_text = f.get("factor", "")
                if factor_text and factor_text not in new_factors:
                    new_factors.append(factor_text)
        except (json.JSONDecodeError, KeyError):
            pass

    factors_text = ""
    if new_factors:
        factors_text = " Key factors: " + "; ".join(new_factors[:3]) + "."

    alert = (
        f"{country_config['name']} {direction} from {prev:.0%} to {curr:.0%} "
        f"({delta_pp:+.1f}pp).{factors_text}"
    )
    return alert


def store_alerts(conn, changes: list):
    """Store significant change alerts in the database."""
    today = datetime.now().astimezone().strftime("%Y-%m-%d")

    for change in changes:
        if not change["is_significant"]:
            continue

        config = load_country_config(
            next(c for c in COUNTRIES
                 if load_country_config(c)["iso3"] == change["country_iso3"])
        )
        alert_text = generate_change_alert(change, config, conn)

        conn.execute(
            """INSERT INTO change_alerts
               (country_iso3, alert_date, previous_probability,
                current_probability, delta, alert_text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (change["country_iso3"], today,
             change["previous_probability"], change["current_probability"],
             change["delta"], alert_text),
        )
        logger.info("Change alert: %s", alert_text)

    conn.commit()
