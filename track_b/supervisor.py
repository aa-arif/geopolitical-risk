"""
Supervisor agent: reconciles 4 independent forecasting agent outputs
into a single Track B probability using Claude Opus.

Uses Anthropic tool_use for structured output.
"""

import json
from config.settings import load_prompt
from utils.api_client import generate_with_tool
from utils.logger import logger
from utils.validation import validate_probability, validate_event_type_scores


# --- Tool schema for supervisor structured output ---

_SUPERVISOR_SCHEMA = {
    "type": "object",
    "properties": {
        "agreements": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Points where agents agree",
        },
        "disagreements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "agent_positions": {"type": "object"},
                    "resolution": {"type": "string"},
                    "stronger_argument": {"type": "string"},
                },
                "required": ["issue", "resolution"],
            },
        },
        "errors_identified": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string"},
                    "error_type": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["agent", "error_type", "explanation"],
            },
        },
        "information_gaps": {
            "type": "array",
            "items": {"type": "string"},
        },
        "final_probability": {
            "type": "number",
            "description": "Reconciled probability as decimal between 0.0 and 1.0 (e.g. 0.23 for 23%)",
        },
        "narrative_summary": {
            "type": "string",
            "description": "Coherent narrative synthesizing the key reasoning",
        },
        "key_risk_factors": {
            "type": "array",
            "items": {"type": "string"},
        },
        "key_stabilizing_factors": {
            "type": "array",
            "items": {"type": "string"},
        },
        "confidence": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
        "executive_summary": {
            "type": "string",
            "description": "2-3 sentence plain-English summary. State the risk level, primary driver, and key stabilizing factor.",
        },
        "event_type_scores": {
            "type": "object",
            "description": "Reconciled per-event-type probabilities (0.0-1.0 each)",
            "properties": {
                "ACE": {"type": "number", "description": "Armed Conflict Escalation"},
                "MCU": {"type": "number", "description": "Mass Civil Unrest"},
                "REC": {"type": "number", "description": "Regime/Executive Change"},
                "PSS": {"type": "number", "description": "Policy/Sanctions Shift"},
                "CID": {"type": "number", "description": "Critical Infrastructure Disruption"},
            },
            "required": ["ACE", "MCU", "REC", "PSS", "CID"],
        },
        "event_type_drivers": {
            "type": "object",
            "description": "Key driver for each event type with P > 0.10 (1 sentence each)",
            "properties": {
                "ACE": {"type": "string"},
                "MCU": {"type": "string"},
                "REC": {"type": "string"},
                "PSS": {"type": "string"},
                "CID": {"type": "string"},
            },
        },
    },
    "required": ["agreements", "disagreements", "final_probability",
                  "narrative_summary", "key_risk_factors", "key_stabilizing_factors",
                  "confidence", "executive_summary", "event_type_scores"],
}


def _extract_reasoning_text(agent_result: dict) -> str:
    """Extract a concise reasoning summary from an agent's result."""
    agent_type = agent_result.get("agent_type", "unknown")

    if agent_type == "baserate":
        ups = agent_result.get("upward_adjustments", [])
        downs = agent_result.get("downward_adjustments", [])
        parts = []
        for adj in ups[:3]:
            parts.append(f"UP: {adj.get('factor', '')} (+{adj.get('magnitude', 0):.2f})")
        for adj in downs[:3]:
            parts.append(f"DOWN: {adj.get('factor', '')} (-{adj.get('magnitude', 0):.2f})")
        return "; ".join(parts) if parts else "No significant adjustments from base rate."

    elif agent_type == "analogy":
        analogies = agent_result.get("analogies", [])
        parts = [f"{a.get('country', '?')} {a.get('year', '?')}: {a.get('outcome', '?')[:80]}"
                 for a in analogies[:3]]
        synthesis = agent_result.get("synthesis", "")[:200]
        return "; ".join(parts) + f" | Synthesis: {synthesis}"

    elif agent_type == "decomposition":
        subs = agent_result.get("sub_questions", [])
        parts = [f"Q: {s.get('question', '?')[:60]} -> P={s.get('probability', 0):.0%}"
                 for s in subs[:5]]
        logic = agent_result.get("combination_logic", "")[:150]
        return "; ".join(parts) + f" | Logic: {logic}"

    elif agent_type == "devil":
        args = agent_result.get("contrarian_arguments", [])
        parts = [f"[{a.get('strength', '?')}] {a.get('argument', '?')[:80]}" for a in args[:3]]
        return "; ".join(parts) if parts else "No strong contrarian arguments."

    return json.dumps(agent_result, default=str)[:300]


def reconcile(country_config: dict, track_a_result: dict,
              ensemble_results: list, reasoning_summary: str = "",
              acled_data: dict = None) -> dict:
    """
    Reconcile 4 agent forecasts into a single Track B probability.

    Uses Claude Opus for higher-quality reasoning.

    Args:
        country_config: Country configuration
        track_a_result: Track A structural prediction
        ensemble_results: List of 4 agent forecast dicts
        reasoning_summary: Full reasoning chain summary text
        acled_data: ACLED event summary dict

    Returns:
        Supervisor reconciliation result with final_probability
    """
    template = load_prompt("supervisor")

    # Map agents to their results (handle fewer than 4 gracefully)
    agents = ensemble_results + [{"final_probability": 0.15, "agent_type": "missing"}] * 4
    agents = agents[:4]

    # Format Track A breakdown
    components = track_a_result.get("components", {})
    track_a_breakdown = (
        f"PITF base probability: {components.get('pitf_base', 0)*100:.1f}%\n"
        f"Structural vulnerability: {components.get('structural_vulnerability', 0):.3f}\n"
        f"Neighborhood contagion: {components.get('neighborhood_contagion', 0):.3f}\n"
        f"GPR trend: {components.get('gpr_trend', 0):+.2f} ({components.get('gpr_trend_description', 'N/A')})\n"
        f"Conflict event monthly avg: {components.get('conflict_avg_monthly', components.get('acled_avg_monthly', 0)):.0f} events\n"
        f"Conflict floor applied: {components.get('conflict_floor_applied', components.get('acled_floor_applied', 0))*100:.0f}%"
    )

    # Format event summary (source-agnostic: supports both old by_type and new by_category)
    event_text = "No conflict event data available."
    if acled_data and acled_data.get("total_events", 0) > 0:
        event_text = (
            f"Total events (last {acled_data.get('period_days', 30)} days): "
            f"{acled_data['total_events']}, Fatalities: {acled_data.get('total_fatalities', 0)}"
        )
        for entry in acled_data.get("by_category", acled_data.get("by_type", []))[:5]:
            label = entry.get("event_category", entry.get("event_type", "unknown"))
            event_text += f"\n  {label}: {entry['count']} events"

    # Build agent event-type score summary for supervisor
    agent_et_lines = []
    for i, a in enumerate(agents[:4]):
        scores = a.get("event_type_scores", {})
        if scores:
            parts = [f"{k}={v:.0%}" for k, v in sorted(scores.items())]
            agent_et_lines.append(f"  Agent {i+1}: {', '.join(parts)}")
    agent_et_summary = "\n".join(agent_et_lines) if agent_et_lines else "  No per-type scores available."

    prompt = template.format(
        country_name=country_config["name"],
        track_a_probability=f"{track_a_result['probability'] * 100:.1f}",
        track_a_breakdown=track_a_breakdown,
        acled_summary=event_text,
        reasoning_chains_summary=reasoning_summary or "No causal factors extracted.",
        agent_1_probability=f"{agents[0]['final_probability'] * 100:.1f}",
        agent_1_reasoning=_extract_reasoning_text(agents[0]),
        agent_2_probability=f"{agents[1]['final_probability'] * 100:.1f}",
        agent_2_reasoning=_extract_reasoning_text(agents[1]),
        agent_3_probability=f"{agents[2]['final_probability'] * 100:.1f}",
        agent_3_reasoning=_extract_reasoning_text(agents[2]),
        agent_4_probability=f"{agents[3]['final_probability'] * 100:.1f}",
        agent_4_reasoning=_extract_reasoning_text(agents[3]),
    )

    # Append event-type decomposition request
    prompt += f"""

EVENT-TYPE DECOMPOSITION:
The four agents provided these per-event-type scores:
{agent_et_summary}

Reconcile these into final per-event-type probabilities. For each type, assess which agents' scores are most credible and produce a reconciled estimate:
- ACE (Armed Conflict Escalation): military clashes, bombings, targeted violence
- MCU (Mass Civil Unrest): large-scale protests, riots, strikes
- REC (Regime/Executive Change): coup, forced resignation, contested transfer
- PSS (Policy/Sanctions Shift): new sanctions, trade restrictions, regulatory changes
- CID (Critical Infrastructure Disruption): attacks on energy, transport, communications

Your overall final_probability should be consistent with your event_type_scores.
For each type with P > 0.10, provide a one-sentence key driver in event_type_drivers.
"""

    result = generate_with_tool(
        prompt=prompt,
        tool_name="submit_reconciliation",
        tool_schema=_SUPERVISOR_SCHEMA,
        model="opus",
        temperature=0.2,
        max_tokens=4096,
    )

    # Validate final_probability
    if "final_probability" not in result:
        import statistics
        probs = sorted([a["final_probability"] for a in agents[:4]])
        result["final_probability"] = statistics.median(probs)

    validate_probability(result)
    validate_event_type_scores(result)

    if "confidence" not in result:
        result["confidence"] = "medium"

    if "executive_summary" not in result:
        result["executive_summary"] = ""

    logger.info(
        "Supervisor reconciled %s: P(B)=%.3f (confidence=%s) ET=[ACE=%.2f MCU=%.2f REC=%.2f PSS=%.2f CID=%.2f]",
        country_config["iso3"],
        result["final_probability"],
        result["confidence"],
        result["event_type_scores"].get("ACE", 0),
        result["event_type_scores"].get("MCU", 0),
        result["event_type_scores"].get("REC", 0),
        result["event_type_scores"].get("PSS", 0),
        result["event_type_scores"].get("CID", 0),
    )

    return result
