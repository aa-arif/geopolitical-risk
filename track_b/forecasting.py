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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    _validate_forecast(result, track_a_prob=track_a_result["probability"])
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
    _validate_forecast(result, track_a_prob=track_a_result["probability"])
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
    _validate_forecast(result, track_a_prob=track_a_result["probability"])
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
            for adj in f.get("upward_adjustments", [])[:6]:
                agent_lines.append(f"  UP: {adj.get('factor','')} (+{adj.get('magnitude',0):.2f}): {adj.get('reasoning','')[:150]}")
            for adj in f.get("downward_adjustments", [])[:6]:
                agent_lines.append(f"  DOWN: {adj.get('factor','')} (-{adj.get('magnitude',0):.2f}): {adj.get('reasoning','')[:150]}")
        elif atype == "analogy":
            for a in f.get("analogies", [])[:4]:
                agent_lines.append(f"  Analogy: {a.get('country','?')} {a.get('year','?')} - {a.get('outcome','')[:120]}")
        elif atype == "decomposition":
            for sq in f.get("sub_questions", [])[:6]:
                agent_lines.append(f"  SubQ: {sq.get('question','')[:100]} -> P={sq.get('probability',0):.0%}")
    agent_reasoning = "\n".join(agent_lines) if agent_lines else "No detailed reasoning available."

    prompt = template.format(
        country_name=country_config["name"],
        track_a_probability=f"{prob * 100:.1f}",
        consensus_direction=direction,
        preliminary_average=f"{avg * 100:.1f}",
        agent_reasoning=agent_reasoning,
        reasoning_chains_summary=reasoning_summary,
        acled_summary=_format_acled_summary(acled_data),
    )

    result = generate(prompt=prompt, model="sonnet", temperature=0.5)
    result["agent_type"] = "devil"
    _validate_forecast(result, track_a_prob=track_a_result["probability"])
    logger.info("Agent 4 (devil) for %s: P=%.3f",
                country_config["iso3"], result["final_probability"])
    return result


def _validate_forecast(result: dict, track_a_prob: float = None) -> None:
    """Ensure forecast result has valid final_probability."""
    if "final_probability" not in result:
        result["final_probability"] = 0.15  # default fallback

    p = result["final_probability"]
    # Handle both 0-1 and 0-100 scales
    if p > 1.0:
        p = p / 100.0
    p = max(0.01, min(0.99, p))

    # Clamp to within 50pp of Track A if available.
    # Wide enough for Track B to signal genuine surprises the structural
    # model misses, while still preventing total hallucination.
    # The prompt-level constraint (20pp) handles normal anchoring;
    # this code clamp is a safety net for extreme cases only.
    if track_a_prob is not None:
        max_deviation = 0.50
        p = max(track_a_prob - max_deviation, min(track_a_prob + max_deviation, p))
        p = max(0.01, min(0.99, p))

    result["final_probability"] = p

    if "key_uncertainties" not in result:
        result["key_uncertainties"] = []


def run_ensemble(country_config: dict, track_a_result: dict,
                 reasoning_summary: str, acled_data: dict) -> list:
    """
    Run all 4 forecasting agents and return their results.
    Agents 1-3 run concurrently, then Agent 4 runs with their results.
    """
    agent_funcs = [
        ("baserate", forecast_baserate),
        ("analogy", forecast_analogy),
        ("decomposition", forecast_decomposition),
    ]

    results = [None, None, None]
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_idx = {}
        for idx, (name, func) in enumerate(agent_funcs):
            future = executor.submit(
                func, country_config, track_a_result,
                reasoning_summary, acled_data,
            )
            future_to_idx[future] = idx

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                logger.error("Agent %d failed: %s", idx + 1, e)
                results[idx] = {
                    "final_probability": track_a_result["probability"],
                    "agent_type": agent_funcs[idx][0],
                    "key_uncertainties": [f"Agent failed: {e}"],
                }

    # Agent 4 gets the preliminary results
    try:
        results.append(forecast_devil(country_config, track_a_result,
                                      reasoning_summary, acled_data, results[:3]))
    except Exception as e:
        logger.error("Agent 4 (devil) failed: %s", e)
        results.append({
            "final_probability": track_a_result["probability"],
            "agent_type": "devil",
            "key_uncertainties": [f"Agent failed: {e}"],
        })

    return results
