"""
Contradiction monitor: checks whether new data contradicts the current
risk assessment. Uses heuristics first, LLM only when anomalous.

Queries the unified conflict_events table (source-agnostic).
"""

import json
from config.settings import load_prompt
from utils.api_client import generate
from utils.db import get_event_summary
from utils.logger import logger


def check_contradiction_heuristic(country_iso3: str, current_probability: float,
                                   db_conn) -> dict:
    """
    Fast heuristic check: compare recent event counts against baseline.
    Only triggers LLM analysis when counts are anomalous (>2x monthly average).

    Returns:
        Dict with assessment and whether to trigger full LLM analysis.
    """
    # Get last 7 days vs last 90 days baseline from unified conflict_events
    recent = get_event_summary(db_conn, country_iso3, days=7)
    baseline = get_event_summary(db_conn, country_iso3, days=90)

    recent_count = recent["total_events"]
    baseline_weekly_avg = baseline["total_events"] / 13.0 if baseline["total_events"] > 0 else 0

    if baseline_weekly_avg == 0:
        return {
            "assessment": "neutral",
            "explanation": "No baseline data available for comparison.",
            "trigger_reprediction": False,
            "recent_events": recent_count,
            "baseline_weekly_avg": 0,
        }

    ratio = recent_count / baseline_weekly_avg if baseline_weekly_avg > 0 else 0

    # Significant spike: >2x weekly average
    if ratio > 2.0:
        return {
            "assessment": "potential_contradiction",
            "explanation": (
                f"Event count ({recent_count}) is {ratio:.1f}x the weekly "
                f"baseline ({baseline_weekly_avg:.1f}). Triggering LLM analysis."
            ),
            "trigger_reprediction": True,
            "recent_events": recent_count,
            "baseline_weekly_avg": baseline_weekly_avg,
            "ratio": ratio,
        }

    # Significant drop: <0.3x weekly average when prediction is high
    if ratio < 0.3 and current_probability > 0.3:
        return {
            "assessment": "potential_contradiction",
            "explanation": (
                f"Event count ({recent_count}) is only {ratio:.1f}x the weekly "
                f"baseline despite high risk prediction ({current_probability:.0%}). "
                f"Triggering LLM analysis."
            ),
            "trigger_reprediction": True,
            "recent_events": recent_count,
            "baseline_weekly_avg": baseline_weekly_avg,
            "ratio": ratio,
        }

    return {
        "assessment": "neutral",
        "explanation": f"Event count ({recent_count}) within normal range ({ratio:.1f}x baseline).",
        "trigger_reprediction": False,
        "recent_events": recent_count,
        "baseline_weekly_avg": baseline_weekly_avg,
        "ratio": ratio,
    }


def check_contradiction_llm(country_config: dict, current_probability: float,
                             reasoning_summary: str, new_data_summary: str) -> dict:
    """
    Full LLM-powered contradiction analysis.
    Only called when heuristic check detects anomalous data.
    """
    template = load_prompt("contradiction")

    prompt = template.format(
        country_name=country_config["name"],
        current_probability=f"{current_probability * 100:.1f}",
        reasoning_summary=reasoning_summary,
        new_data_summary=new_data_summary,
    )

    result = generate(
        prompt=prompt,
        model="sonnet",
        temperature=0.2,
        max_tokens=1024,
    )

    # Validate
    valid_assessments = {"contradicts", "supports", "neutral"}
    if result.get("assessment") not in valid_assessments:
        result["assessment"] = "neutral"
    if "trigger_reprediction" not in result:
        result["trigger_reprediction"] = result.get("assessment") == "contradicts"

    logger.info(
        "Contradiction check for %s: %s (magnitude=%s, repredict=%s)",
        country_config["iso3"],
        result["assessment"],
        result.get("magnitude", "unknown"),
        result.get("trigger_reprediction", False),
    )

    return result
