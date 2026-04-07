"""
Daily digest generator.
Produces a human-readable summary of all country predictions.
"""

import json
from datetime import datetime, timezone

from config.settings import COUNTRIES, load_country_config
from utils.db import get_connection, get_prediction_history


def generate_digest() -> str:
    """Generate a daily digest of all predictions."""
    conn = get_connection()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        f"# Geopolitical Risk Digest - {now}",
        "",
    ]

    for country_name in COUNTRIES:
        config = load_country_config(country_name)
        iso3 = config["iso3"]

        history = get_prediction_history(conn, iso3, limit=1)
        if not history:
            lines.append(f"## {config['name']} ({iso3}): No predictions yet.")
            lines.append("")
            continue

        latest = history[0]
        prob = latest["calibrated_probability"]
        reasoning = json.loads(latest["reasoning_summary"]) if latest["reasoning_summary"] else {}

        # Risk level label
        if prob >= 0.5:
            level = "CRITICAL"
        elif prob >= 0.3:
            level = "HIGH"
        elif prob >= 0.15:
            level = "ELEVATED"
        else:
            level = "LOW"

        lines.append(f"## {config['name']} ({iso3}) - {level} ({prob:.0%})")
        lines.append(f"  Track A: {latest['track_a_probability']:.0%} | "
                      f"Track B: {latest['track_b_probability']:.0%} | "
                      f"Fused: {latest['fused_probability']:.0%}")

        if reasoning.get("key_risk_factors"):
            lines.append("  Risk factors: " + "; ".join(reasoning["key_risk_factors"][:3]))
        if reasoning.get("key_stabilizing_factors"):
            lines.append("  Stabilizers: " + "; ".join(reasoning["key_stabilizing_factors"][:3]))

        lines.append(f"  Confidence: {reasoning.get('confidence', 'N/A')}")
        lines.append("")

    conn.close()
    return "\n".join(lines)


if __name__ == "__main__":
    print(generate_digest())
