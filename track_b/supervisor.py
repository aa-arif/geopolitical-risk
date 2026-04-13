"""
Supervisor agent: reconciles 4 independent forecasting agent outputs
into a single Track B probability using Claude Opus.
"""

import json
from config.settings import load_prompt
from utils.api_client import generate
from utils.logger import logger


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
        f"ACLED monthly avg: {components.get('acled_avg_monthly', 0):.0f} events\n"
        f"ACLED floor applied: {components.get('acled_floor_applied', 0)*100:.0f}%"
    )

    # Format ACLED summary
    acled_text = "No ACLED data available."
    if acled_data and acled_data.get("total_events", 0) > 0:
        acled_text = (
            f"Total events (last {acled_data.get('period_days', 30)} days): "
            f"{acled_data['total_events']}, Fatalities: {acled_data.get('total_fatalities', 0)}"
        )
        for entry in acled_data.get("by_type", [])[:5]:
            acled_text += f"\n  {entry['event_type']}: {entry['count']} events"

    prompt = template.format(
        country_name=country_config["name"],
        track_a_probability=f"{track_a_result['probability'] * 100:.1f}",
        track_a_breakdown=track_a_breakdown,
        acled_summary=acled_text,
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

    result = generate(
        prompt=prompt,
        model="opus",
        temperature=0.2,
        max_tokens=4096,
    )

    # Validate
    if "final_probability" not in result:
        # Fallback: use median of agent probabilities (robust to outliers)
        import statistics
        probs = sorted([a["final_probability"] for a in agents[:4]])
        result["final_probability"] = statistics.median(probs)

    p = result["final_probability"]
    if p > 1.0:
        p = p / 100.0
    result["final_probability"] = max(0.01, min(0.99, p))

    if "confidence" not in result:
        result["confidence"] = "medium"

    if "executive_summary" not in result:
        result["executive_summary"] = ""

    logger.info(
        "Supervisor reconciled %s: P(B)=%.3f (confidence=%s)",
        country_config["iso3"],
        result["final_probability"],
        result["confidence"],
    )

    return result
