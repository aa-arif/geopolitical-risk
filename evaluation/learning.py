"""
Learning feedback module.

Analyzes resolved predictions to identify which causal factors
and which agents are most predictive. Generates feedback text
that gets prepended to agent prompts so the system learns from
its own history.
"""

import json
from collections import Counter, defaultdict

from utils.db import get_resolved_predictions, get_agent_outputs
from evaluation.brier import brier_score
from utils.logger import logger


def analyze_factor_predictiveness(conn, country_iso3: str = None) -> dict:
    """
    For resolved predictions, analyze which causal factor keywords
    appear more often before actual instability events (outcome=1)
    vs non-events (outcome=0).

    Returns dict with predictive_factors, noise_factors, and lift ratios.
    """
    resolved = get_resolved_predictions(conn, country_iso3)
    if not resolved:
        return {"n_resolved": 0, "predictive_factors": [], "noise_factors": []}

    # Collect factor keywords by outcome
    factors_by_outcome = {0: Counter(), 1: Counter()}

    for pred in resolved:
        outcome = pred.get("actual_outcome")
        if outcome is None:
            continue

        reasoning = json.loads(pred["reasoning_summary"]) if pred.get("reasoning_summary") else {}
        # Look at stored reasoning chains via the prediction's reasoning summary
        for key_factor in reasoning.get("key_risk_factors", []):
            # Extract keywords (simplified)
            words = [w.lower().strip() for w in key_factor.split() if len(w) > 3]
            for word in words:
                factors_by_outcome[outcome][word] += 1

    # Compute lift: how much more likely a keyword appears before instability
    total_0 = sum(factors_by_outcome[0].values()) or 1
    total_1 = sum(factors_by_outcome[1].values()) or 1

    all_words = set(factors_by_outcome[0].keys()) | set(factors_by_outcome[1].keys())
    lift_scores = []

    for word in all_words:
        rate_0 = factors_by_outcome[0].get(word, 0) / total_0
        rate_1 = factors_by_outcome[1].get(word, 0) / total_1
        lift = rate_1 / rate_0 if rate_0 > 0 else (10.0 if rate_1 > 0 else 1.0)
        lift_scores.append({"factor": word, "lift": lift,
                            "count_instability": factors_by_outcome[1].get(word, 0),
                            "count_stable": factors_by_outcome[0].get(word, 0)})

    lift_scores.sort(key=lambda x: -x["lift"])
    predictive = [f for f in lift_scores if f["lift"] > 1.5 and f["count_instability"] >= 2][:10]
    noise = [f for f in lift_scores if f["lift"] < 0.5 and f["count_stable"] >= 2][:10]

    return {
        "n_resolved": len([r for r in resolved if r.get("actual_outcome") is not None]),
        "predictive_factors": predictive,
        "noise_factors": noise,
    }


def analyze_agent_accuracy(conn, country_iso3: str = None) -> dict:
    """
    For resolved predictions with agent outputs, compute each agent type's
    individual Brier score.

    Returns ranking of agents by accuracy (lower Brier = better).
    """
    resolved = get_resolved_predictions(conn, country_iso3)
    if not resolved:
        return {"n_resolved": 0, "agents": []}

    agent_scores = defaultdict(list)

    for pred in resolved:
        outcome = pred.get("actual_outcome")
        if outcome is None:
            continue

        agent_rows = get_agent_outputs(conn, pred["id"])
        for row in agent_rows:
            agent_type = row["agent_type"]
            prob = row["probability"]
            if prob is not None:
                bs = brier_score(prob, outcome)
                agent_scores[agent_type].append(bs)

    ranking = []
    for agent_type, scores in agent_scores.items():
        avg_brier = sum(scores) / len(scores)
        ranking.append({
            "agent_type": agent_type,
            "avg_brier": avg_brier,
            "n_predictions": len(scores),
        })

    ranking.sort(key=lambda x: x["avg_brier"])

    return {
        "n_resolved": len([r for r in resolved if r.get("actual_outcome") is not None]),
        "agents": ranking,
    }


def generate_prompt_feedback(conn) -> str:
    """
    Generate feedback text to prepend to forecasting agent prompts.
    Based on analysis of resolved predictions.

    Returns empty string if insufficient data.
    """
    factor_analysis = analyze_factor_predictiveness(conn)
    agent_analysis = analyze_agent_accuracy(conn)

    if factor_analysis["n_resolved"] < 15:
        return ""  # Not enough data for meaningful feedback

    parts = [f"HISTORICAL FEEDBACK (based on {factor_analysis['n_resolved']} resolved predictions):"]

    if factor_analysis["predictive_factors"]:
        top = factor_analysis["predictive_factors"][:5]
        parts.append("Most predictive factor keywords: " +
                      ", ".join(f"{f['factor']} (lift {f['lift']:.1f}x)" for f in top))

    if factor_analysis["noise_factors"]:
        noise = factor_analysis["noise_factors"][:3]
        parts.append("Low-signal factor keywords (often present but rarely predictive): " +
                      ", ".join(f["factor"] for f in noise))

    if agent_analysis["agents"]:
        parts.append("Agent accuracy ranking: " +
                      ", ".join(f"{a['agent_type']} (Brier {a['avg_brier']:.3f})"
                               for a in agent_analysis["agents"]))

    return "\n".join(parts) + "\n"
