"""
Track B forecasting ensemble: 4 diverse LLM-based forecasting agents.

Each agent uses a different reasoning strategy:
1. Base Rate Adjustment (CHAMPS KNOW)
2. Historical Analogy
3. Question Decomposition
4. Devil's Advocate

All agents receive the Track A probability as their outside-view anchor.
"""

import json
from config.settings import load_prompt
from utils.api_client import generate
from utils.logger import logger


def _format_acled_summary(acled_data: dict) -> str:
    """Format ACLED summary data into readable text for prompts."""
    if not acled_data or acled_data.get("total_events", 0) == 0:
        return "No ACLED events recorded in the lookback period."

    lines = [
        f"Total events (last {acled_data['period_days']} days): {acled_data['total_events']}",
        f"Total fatalities: {acled_data['total_fatalities']}",
        "Breakdown by type:",
    ]
    for entry in acled_data.get("by_type", []):
        lines.append(
            f"  - {entry['event_type']}: {entry['count']} events, "
            f"{entry.get('total_fatalities', 0)} fatalities"
        )
    return "\n".join(lines)


def forecast_baserate(country_config: dict, track_a_result: dict,
                      reasoning_summary: str, acled_data: dict) -> dict:
    """Agent 1: Base Rate Adjustment using CHAMPS KNOW methodology."""
    template = load_prompt("champs_baserate")
    prob = track_a_result["probability"]
    components = track_a_result["components"]

    prompt = template.format(
        country_name=country_config["name"],
        track_a_probability=f"{prob * 100:.1f}",
        polity_category=country_config["polity_category"],
        polity_code=country_config["polity_code"],
        infant_mortality=country_config["infant_mortality_per_1000"],
        years_since_instability=country_config["years_since_last_instability"],
        neighborhood_risk=f"{components['neighborhood_contagion']:.2f}",
        gpr_trend=components["gpr_trend_description"],
        reasoning_chains_summary=reasoning_summary,
        acled_summary=_format_acled_summary(acled_data),
    )

    result = generate(prompt=prompt, model="sonnet", temperature=0.3)
    result["agent_type"] = "baserate"
    _validate_forecast(result)
    logger.info("Agent 1 (baserate) for %s: P=%.3f",
                country_config["iso3"], result["final_probability"])
    return result


def forecast_analogy(country_config: dict, track_a_result: dict,
                     reasoning_summary: str, acled_data: dict) -> dict:
    """Agent 2: Historical Analogy reasoning."""
    template = load_prompt("champs_analogy")
    prob = track_a_result["probability"]

    prompt = template.format(
        country_name=country_config["name"],
        track_a_probability=f"{prob * 100:.1f}",
        reasoning_chains_summary=reasoning_summary,
    )

    result = generate(prompt=prompt, model="sonnet", temperature=0.4)
    result["agent_type"] = "analogy"
    _validate_forecast(result)
    logger.info("Agent 2 (analogy) for %s: P=%.3f",
                country_config["iso3"], result["final_probability"])
    return result


def forecast_decomposition(country_config: dict, track_a_result: dict,
                           reasoning_summary: str, acled_data: dict) -> dict:
    """Agent 3: Question Decomposition."""
    template = load_prompt("champs_decomp")
    prob = track_a_result["probability"]

    prompt = template.format(
        country_name=country_config["name"],
        track_a_probability=f"{prob * 100:.1f}",
        reasoning_chains_summary=reasoning_summary,
    )

    result = generate(prompt=prompt, model="sonnet", temperature=0.3)
    result["agent_type"] = "decomposition"
    _validate_forecast(result)
    logger.info("Agent 3 (decomp) for %s: P=%.3f",
                country_config["iso3"], result["final_probability"])
    return result


def forecast_devil(country_config: dict, track_a_result: dict,
                   reasoning_summary: str, acled_data: dict,
                   preliminary_forecasts: list) -> dict:
    """Agent 4: Devil's Advocate (runs after agents 1-3 to challenge consensus)."""
    template = load_prompt("champs_devil")
    prob = track_a_result["probability"]

    # Compute preliminary average from first 3 agents
    probs = [f["final_probability"] for f in preliminary_forecasts]
    avg = sum(probs) / len(probs) if probs else prob
    direction = "elevated" if avg > 0.3 else "moderate" if avg > 0.15 else "low"

    # Build detailed agent reasoning for the devil to challenge
    agent_lines = []
    for f in preliminary_forecasts:
        atype = f.get("agent_type", "unknown")
        ap = f.get("final_probability", 0)
        agent_lines.append(f"Agent ({atype}): P={ap*100:.1f}%")
        if atype == "baserate":
            for adj in f.get("upward_adjustments", [])[:3]:
                agent_lines.append(f"  UP: {adj.get('factor','')} (+{adj.get('magnitude',0):.2f}): {adj.get('reasoning','')[:120]}")
            for adj in f.get("downward_adjustments", [])[:3]:
                agent_lines.append(f"  DOWN: {adj.get('factor','')} (-{adj.get('magnitude',0):.2f}): {adj.get('reasoning','')[:120]}")
        elif atype == "analogy":
            for a in f.get("analogies", [])[:3]:
                agent_lines.append(f"  Analogy: {a.get('country','?')} {a.get('year','?')} - {a.get('outcome','')[:100]}")
        elif atype == "decomposition":
            for sq in f.get("sub_questions", [])[:4]:
                agent_lines.append(f"  SubQ: {sq.get('question','')[:80]} -> P={sq.get('probability',0):.0%}")
    agent_reasoning = "\n".join(agent_lines) if agent_lines else "No detailed reasoning available."

    prompt = template.format(
        country_name=country_config["name"],
        track_a_probability=f"{prob * 100:.1f}",
        consensus_direction=direction,
        preliminary_average=f"{avg * 100:.1f}",
        agent_reasoning=agent_reasoning,
        reasoning_chains_summary=reasoning_summary,
    )

    result = generate(prompt=prompt, model="sonnet", temperature=0.5)
    result["agent_type"] = "devil"
    _validate_forecast(result)
    logger.info("Agent 4 (devil) for %s: P=%.3f",
                country_config["iso3"], result["final_probability"])
    return result


def _validate_forecast(result: dict) -> None:
    """Ensure forecast result has valid final_probability."""
    if "final_probability" not in result:
        result["final_probability"] = 0.15  # default fallback

    p = result["final_probability"]
    # Handle both 0-1 and 0-100 scales
    if p > 1.0:
        p = p / 100.0
    result["final_probability"] = max(0.01, min(0.99, p))

    if "key_uncertainties" not in result:
        result["key_uncertainties"] = []


def run_ensemble(country_config: dict, track_a_result: dict,
                 reasoning_summary: str, acled_data: dict) -> list:
    """
    Run all 4 forecasting agents and return their results.

    Agents 1-3 run first (could be parallelized), then Agent 4
    (devil's advocate) runs with knowledge of the preliminary consensus.
    """
    # Agents 1-3 (independent, could be parallelized in future)
    results = []
    results.append(forecast_baserate(country_config, track_a_result,
                                     reasoning_summary, acled_data))
    results.append(forecast_analogy(country_config, track_a_result,
                                    reasoning_summary, acled_data))
    results.append(forecast_decomposition(country_config, track_a_result,
                                          reasoning_summary, acled_data))

    # Agent 4 gets the preliminary results
    results.append(forecast_devil(country_config, track_a_result,
                                  reasoning_summary, acled_data, results[:3]))

    return results
