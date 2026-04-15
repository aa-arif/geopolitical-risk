"""
Daily brief generator.

Produces formatted risk briefs per country from supervisor reasoning
and event-type scores. Stores in the briefs table. These briefs become:
1. The body of alert emails
2. The content of the weekly Substack digest
3. The detail view on the dashboard when you click a country
4. The raw material for the monthly assessment report
"""

import json
import sqlite3
from datetime import datetime

from config.settings import EVENT_TYPE_RESOLUTION, CALIBRATED_EVENT_TYPES
from utils.api_client import generate_with_tool
from utils.logger import logger


_BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "One sentence: dominant risk and level (e.g. 'Iran faces ELEVATED armed conflict risk (68%)')",
        },
        "what_changed": {
            "type": "string",
            "description": "1-2 sentences on what moved since yesterday, or 'No significant changes'",
        },
        "key_drivers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2-3 bullet points with specific events, data, or developments",
        },
        "sector_watch": {
            "type": "object",
            "description": "One sentence each for relevant sectors",
            "properties": {
                "energy": {"type": "string"},
                "security": {"type": "string"},
                "finance": {"type": "string"},
                "logistics": {"type": "string"},
            },
        },
    },
    "required": ["headline", "what_changed", "key_drivers"],
}


def _risk_label(p):
    if p >= 0.60:
        return "CRITICAL"
    if p >= 0.40:
        return "HIGH"
    if p >= 0.20:
        return "ELEVATED"
    return "LOW"


def _build_brief_prompt(country_name, event_type_scores, reasoning,
                         alerts_today):
    """Build the LLM prompt for daily brief generation."""
    # Format scores
    score_lines = []
    for et in ["ACE", "MCU", "REC", "PSS", "CID"]:
        p = event_type_scores.get(et, 0)
        name = EVENT_TYPE_RESOLUTION.get(et, {}).get("name", et)
        score_lines.append(f"- {name}: {p:.0%}")

    # Determine composite and dominant
    calibrated_scores = {k: v for k, v in event_type_scores.items()
                          if k in CALIBRATED_EVENT_TYPES}
    dominant = max(calibrated_scores, key=calibrated_scores.get) if calibrated_scores else "ACE"
    composite = max(event_type_scores.values()) if event_type_scores else 0

    # Format alerts
    alert_text = "None" if not alerts_today else "\n".join(
        f"- {a}" for a in alerts_today[:5]
    )

    # Extract reasoning details
    narrative = reasoning.get("supervisor", "") or reasoning.get("narrative_summary", "")
    exec_summary = reasoning.get("executive_summary", "")
    risk_factors = reasoning.get("key_risk_factors", [])
    stabilizers = reasoning.get("key_stabilizing_factors", [])
    drivers = reasoning.get("event_type_drivers", {})

    return f"""You are a senior geopolitical analyst at a risk consultancy. Write a concise daily risk brief for {country_name}.

COMPOSITE RISK: {composite:.0%} ({_risk_label(composite)})

EVENT TYPE SCORES:
{chr(10).join(score_lines)}

DOMINANT RISK: {EVENT_TYPE_RESOLUTION.get(dominant, {}).get("name", dominant)} ({event_type_scores.get(dominant, 0):.0%})

TODAY'S ALERTS:
{alert_text}

ASSESSMENT REASONING:
{exec_summary}

{narrative[:500] if narrative else "No detailed narrative available."}

KEY RISK FACTORS: {', '.join(risk_factors[:4]) if risk_factors else 'None identified'}
KEY STABILIZERS: {', '.join(stabilizers[:3]) if stabilizers else 'None identified'}

EVENT TYPE DRIVERS:
{json.dumps(drivers, indent=2) if drivers else 'None'}

Write a brief with:
1. headline: one sentence with the dominant risk type, probability, and level
2. what_changed: what moved since yesterday (use alerts if available, otherwise "No significant changes")
3. key_drivers: 2-3 bullet points with SPECIFIC events, names, dates, numbers
4. sector_watch: one sentence each for Energy, Security, Finance, Logistics (only include relevant sectors)

Keep it under 200 words total. No filler. Every sentence must contain a specific fact."""


def generate_daily_brief(conn, country_iso3, country_name,
                          event_type_scores, reasoning,
                          alerts_today=None):
    """
    Generate and store a daily brief for a country.

    Args:
        conn: Database connection
        country_iso3: ISO3 country code
        country_name: Country name for display
        event_type_scores: Dict of {ACE: 0.35, MCU: 0.12, ...}
        reasoning: Reasoning dict from supervisor result
        alerts_today: Optional list of alert text strings

    Returns:
        Brief dict with headline, body, etc.
    """
    prompt = _build_brief_prompt(
        country_name, event_type_scores, reasoning,
        alerts_today or [],
    )

    try:
        result = generate_with_tool(
            prompt=prompt,
            tool_name="submit_brief",
            tool_schema=_BRIEF_SCHEMA,
            model="sonnet",
            temperature=0.3,
            max_tokens=1024,
        )
    except Exception as e:
        logger.warning("Brief generation failed for %s: %s", country_iso3, e)
        result = {
            "headline": f"{country_name}: Risk assessment updated",
            "what_changed": "Brief generation unavailable.",
            "key_drivers": [],
        }

    # Format as markdown
    headline = result.get("headline", "")
    what_changed = result.get("what_changed", "")
    drivers = result.get("key_drivers", [])
    sectors = result.get("sector_watch", {})

    body_parts = [
        f"## {country_name} -- {datetime.now().astimezone().strftime('%Y-%m-%d')}",
        f"**{headline}**",
        "",
        f"**What changed:** {what_changed}",
        "",
        "**Key drivers:**",
    ]
    for d in drivers:
        body_parts.append(f"- {d}")

    if sectors:
        body_parts.append("")
        body_parts.append("**Sector watch:**")
        for sector, text in sectors.items():
            if text:
                body_parts.append(f"- **{sector.title()}:** {text}")

    body_markdown = "\n".join(body_parts)

    # Store
    brief_date = datetime.now().astimezone().strftime("%Y-%m-%d")
    try:
        conn.execute(
            """INSERT INTO briefs
               (country_iso3, brief_date, brief_type, headline,
                body_markdown, event_type_scores, key_drivers,
                sector_implications)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (country_iso3, brief_date, "daily_summary", headline,
             body_markdown, json.dumps(event_type_scores),
             json.dumps(drivers),
             json.dumps(sectors) if sectors else None),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass

    logger.info("Brief for %s: %s", country_iso3, headline[:80])

    return {
        "headline": headline,
        "what_changed": what_changed,
        "key_drivers": drivers,
        "sector_watch": sectors,
        "body_markdown": body_markdown,
    }
