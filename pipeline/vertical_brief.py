"""
Vertical brief generator for the Iran-Gulf Energy Corridor.

After all Gulf countries are processed, generates a corridor-level brief
covering: Hormuz transit assessment, top risk shifts across 9 countries,
energy sector implications. This brief is the anchor content for:
- Weekly Substack digest
- Sales collateral / design partner outreach
- YC demo day material
"""

import json
import sqlite3
from datetime import datetime

from config.settings import VERTICALS, load_country_config, EVENT_TYPE_RESOLUTION
from utils.api_client import generate_with_tool
from utils.db import (
    get_connection, get_latest_predictions_all,
    get_event_type_predictions, get_recent_alerts_v2,
)
from utils.logger import logger


_VERTICAL_BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "hormuz_assessment": {
            "type": "string",
            "description": "2-3 sentences on Strait of Hormuz transit risk status",
        },
        "corridor_headline": {
            "type": "string",
            "description": "One sentence headline for the corridor risk status",
        },
        "top_risk_shifts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "country": {"type": "string"},
                    "event_type": {"type": "string"},
                    "direction": {"type": "string", "enum": ["rising", "falling", "stable"]},
                    "summary": {"type": "string"},
                },
                "required": ["country", "summary"],
            },
            "description": "Top 3-5 risk shifts across corridor countries this cycle",
        },
        "energy_outlook": {
            "type": "string",
            "description": "2-3 sentences on energy supply chain implications for the week ahead",
        },
        "shipping_outlook": {
            "type": "string",
            "description": "1-2 sentences on shipping/insurance implications",
        },
        "action_items": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2-3 specific actionable items for energy sector operators",
        },
    },
    "required": ["hormuz_assessment", "corridor_headline", "top_risk_shifts",
                  "energy_outlook"],
}


def _risk_label(p):
    if p >= 0.60:
        return "CRITICAL"
    if p >= 0.40:
        return "HIGH"
    if p >= 0.20:
        return "ELEVATED"
    return "LOW"


def generate_vertical_brief(conn, vertical_name="iran_gulf"):
    """
    Generate a corridor-level brief for a vertical.

    Aggregates predictions, alerts, and event-type scores across all
    countries in the vertical, then produces a sector-focused brief.

    Returns brief dict and stores in briefs table.
    """
    vertical = VERTICALS.get(vertical_name)
    if not vertical:
        logger.warning("Unknown vertical: %s", vertical_name)
        return None

    country_names = vertical["countries"]
    sectors = vertical["sectors"]

    # Gather data for all corridor countries
    country_summaries = []
    all_alerts = []

    for country_name in country_names:
        try:
            config = load_country_config(country_name)
        except (FileNotFoundError, KeyError):
            logger.debug("Config not found for %s, skipping.", country_name)
            continue

        iso3 = config["iso3"]

        # Get latest composite prediction
        composites = get_latest_predictions_all(conn, event_type="composite")
        composite = next((c for c in composites if c["country_iso3"] == iso3), None)
        if not composite:
            continue

        prob = composite["calibrated_probability"]

        # Get event-type predictions
        et_preds = get_event_type_predictions(conn, iso3)
        et_scores = {p["event_type"]: p["calibrated_probability"] for p in et_preds}

        # Get recent alerts
        alerts = get_recent_alerts_v2(conn, iso3, days=7)
        all_alerts.extend(alerts)

        # Parse reasoning
        reasoning = {}
        try:
            reasoning = json.loads(composite["reasoning_summary"]) if composite.get("reasoning_summary") else {}
        except (json.JSONDecodeError, TypeError):
            pass

        country_summaries.append({
            "name": config["name"],
            "iso3": iso3,
            "composite": prob,
            "risk_level": _risk_label(prob),
            "event_type_scores": et_scores,
            "executive_summary": reasoning.get("executive_summary", ""),
            "alerts_7d": len(alerts),
        })

    if not country_summaries:
        logger.warning("No data for vertical %s.", vertical_name)
        return None

    # Sort by composite risk descending
    country_summaries.sort(key=lambda x: x["composite"], reverse=True)

    # Build prompt
    country_lines = []
    for cs in country_summaries:
        et_str = ", ".join(f"{k}={v:.0%}" for k, v in cs["event_type_scores"].items())
        alert_str = f" [{cs['alerts_7d']} alerts]" if cs["alerts_7d"] else ""
        country_lines.append(
            f"  {cs['name']} ({cs['iso3']}): {cs['composite']:.0%} {cs['risk_level']}"
            f" | {et_str}{alert_str}"
        )
        if cs["executive_summary"]:
            country_lines.append(f"    Summary: {cs['executive_summary'][:150]}")

    alert_lines = []
    for a in sorted(all_alerts, key=lambda x: abs(x.get("delta", 0)), reverse=True)[:5]:
        alert_lines.append(
            f"  {a['country_iso3']} {a['event_type']}: {a.get('brief_text', '')[:100]}"
        )

    prompt = f"""You are a senior energy sector risk analyst. Write a Gulf Energy Corridor brief covering
{len(country_summaries)} countries spanning the Persian Gulf and Strait of Hormuz.

CORRIDOR COUNTRIES (sorted by risk):
{chr(10).join(country_lines)}

SIGNIFICANT ALERTS (last 7 days):
{chr(10).join(alert_lines) if alert_lines else '  No significant alerts this week.'}

SECTOR CONTEXT:
- Energy: {sectors['energy']}
- Shipping: {sectors['shipping']}
- Finance: {sectors['finance']}
- Security: {sectors['security']}

Write a corridor brief with:
1. hormuz_assessment: Current Strait of Hormuz transit risk (2-3 sentences, specific)
2. corridor_headline: One sentence headline
3. top_risk_shifts: 3-5 notable risk shifts across the corridor (country, event type, direction, summary)
4. energy_outlook: Energy supply chain implications for the week ahead (2-3 sentences)
5. shipping_outlook: Shipping/insurance implications (1-2 sentences)
6. action_items: 2-3 specific actionable items for energy sector operators

Be specific. Use the country data above. Name countries, cite probabilities, reference alerts.
Keep the total brief under 350 words."""

    try:
        result = generate_with_tool(
            prompt=prompt,
            tool_name="submit_corridor_brief",
            tool_schema=_VERTICAL_BRIEF_SCHEMA,
            model="sonnet",
            temperature=0.3,
            max_tokens=2048,
        )
    except Exception as e:
        logger.warning("Vertical brief generation failed: %s", e)
        result = {
            "corridor_headline": f"Gulf Energy Corridor: {len(country_summaries)} countries assessed",
            "hormuz_assessment": "Assessment unavailable.",
            "top_risk_shifts": [],
            "energy_outlook": "Brief generation unavailable.",
        }

    # Format as markdown
    headline = result.get("corridor_headline", "")
    hormuz = result.get("hormuz_assessment", "")
    shifts = result.get("top_risk_shifts", [])
    energy = result.get("energy_outlook", "")
    shipping = result.get("shipping_outlook", "")
    actions = result.get("action_items", [])

    body_parts = [
        f"# Gulf Energy Corridor Brief -- {datetime.now().astimezone().strftime('%Y-%m-%d')}",
        f"**{headline}**",
        "",
        "## Strait of Hormuz",
        hormuz,
        "",
        "## Risk Shifts This Week",
    ]
    for s in shifts:
        direction = s.get("direction", "")
        arrow = {"rising": "^", "falling": "v", "stable": "-"}.get(direction, "")
        body_parts.append(f"- **{s.get('country', '')}** [{arrow}] {s.get('summary', '')}")

    body_parts.extend([
        "",
        "## Energy Sector Outlook",
        energy,
    ])
    if shipping:
        body_parts.extend(["", "## Shipping & Insurance", shipping])
    if actions:
        body_parts.extend(["", "## Action Items"])
        for a in actions:
            body_parts.append(f"- {a}")

    # Country risk table
    body_parts.extend(["", "## Corridor Risk Summary", "| Country | Composite | ACE | MCU | Level |",
                        "|---------|-----------|-----|-----|-------|"])
    for cs in country_summaries:
        ace = cs["event_type_scores"].get("ACE", 0)
        mcu = cs["event_type_scores"].get("MCU", 0)
        body_parts.append(
            f"| {cs['name']} | {cs['composite']:.0%} | {ace:.0%} | {mcu:.0%} | {cs['risk_level']} |"
        )

    body_markdown = "\n".join(body_parts)

    # Build aggregated event-type scores (max across corridor)
    agg_scores = {}
    for et in ["ACE", "MCU", "REC", "PSS", "CID"]:
        vals = [cs["event_type_scores"].get(et, 0) for cs in country_summaries]
        agg_scores[et] = max(vals) if vals else 0

    # Store
    brief_date = datetime.now().astimezone().strftime("%Y-%m-%d")
    try:
        conn.execute(
            """INSERT INTO briefs
               (country_iso3, brief_date, brief_type, headline,
                body_markdown, event_type_scores, key_drivers)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("GULF", brief_date, "vertical_iran_gulf", headline,
             body_markdown, json.dumps(agg_scores),
             json.dumps([s.get("summary", "") for s in shifts])),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass

    logger.info("Vertical brief: %s", headline[:80])

    return {
        "corridor_headline": headline,
        "hormuz_assessment": hormuz,
        "top_risk_shifts": shifts,
        "energy_outlook": energy,
        "shipping_outlook": shipping,
        "action_items": actions,
        "body_markdown": body_markdown,
        "countries": country_summaries,
    }
