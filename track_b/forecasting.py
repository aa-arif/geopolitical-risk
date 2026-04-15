"""
Track B forecasting ensemble: 4 diverse LLM-based forecasting agents.

Each agent uses a different reasoning strategy:
1. Base Rate Adjustment (CHAMPS KNOW)
2. Historical Analogy
3. Question Decomposition
4. Devil's Advocate

All agents receive the Track A probability as their outside-view anchor.
Uses Anthropic tool_use for structured output (guaranteed valid types/ranges).
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from config.settings import load_prompt
from utils.api_client import generate_with_tool
from utils.logger import logger
from utils.validation import validate_probability, validate_event_type_scores


# --- Shared event-type score schema fragment ---
# Injected into each agent schema for V2 event-type decomposition.

_EVENT_TYPE_SCORES_PROPERTY = {
    "event_type_scores": {
        "type": "object",
        "description": "Per-event-type probability decomposition (0.0-1.0 each)",
        "properties": {
            "ACE": {"type": "number", "description": "Armed Conflict Escalation probability"},
            "MCU": {"type": "number", "description": "Mass Civil Unrest probability"},
            "REC": {"type": "number", "description": "Regime/Executive Change probability"},
            "PSS": {"type": "number", "description": "Policy/Sanctions Shift probability"},
            "CID": {"type": "number", "description": "Critical Infrastructure Disruption probability"},
        },
        "required": ["ACE", "MCU", "REC", "PSS", "CID"],
    },
    "event_type_drivers": {
        "type": "object",
        "description": "Key driver for each event type (1 sentence each, only for types with P > 0.10)",
        "properties": {
            "ACE": {"type": "string"},
            "MCU": {"type": "string"},
            "REC": {"type": "string"},
            "PSS": {"type": "string"},
            "CID": {"type": "string"},
        },
    },
}


# --- Tool schemas for structured output ---

_BASERATE_SCHEMA = {
    "type": "object",
    "properties": {
        "base_rate_acknowledged": {
            "type": "number",
            "description": "The base rate probability (0.0-1.0) you are anchoring to",
        },
        "upward_adjustments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "factor": {"type": "string"},
                    "magnitude": {"type": "number", "description": "Adjustment magnitude as decimal (e.g. 0.05 for 5pp)"},
                    "reasoning": {"type": "string"},
                },
                "required": ["factor", "magnitude", "reasoning"],
            },
        },
        "downward_adjustments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "factor": {"type": "string"},
                    "magnitude": {"type": "number", "description": "Adjustment magnitude as decimal (e.g. 0.03 for 3pp)"},
                    "reasoning": {"type": "string"},
                },
                "required": ["factor", "magnitude", "reasoning"],
            },
        },
        "final_probability": {
            "type": "number",
            "description": "Final probability as decimal between 0.0 and 1.0 (e.g. 0.23 for 23%)",
        },
        "confidence_in_estimate": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
        "key_uncertainties": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["base_rate_acknowledged", "upward_adjustments", "downward_adjustments",
                  "final_probability", "confidence_in_estimate", "key_uncertainties",
                  "event_type_scores"],
}

_ANALOGY_SCHEMA = {
    "type": "object",
    "properties": {
        "analogies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "country": {"type": "string"},
                    "year": {"type": "integer"},
                    "situation": {"type": "string"},
                    "similarity": {"type": "string"},
                    "difference": {"type": "string"},
                    "outcome": {"type": "string"},
                    "implied_probability": {"type": "number"},
                },
                "required": ["country", "year", "situation", "similarity",
                             "difference", "outcome", "implied_probability"],
            },
        },
        "synthesis": {"type": "string"},
        "final_probability": {
            "type": "number",
            "description": "Final probability as decimal between 0.0 and 1.0",
        },
        "key_uncertainties": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["analogies", "synthesis", "final_probability", "key_uncertainties",
                  "event_type_scores"],
}

_DECOMP_SCHEMA = {
    "type": "object",
    "properties": {
        "sub_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "probability": {"type": "number", "description": "Sub-question probability as decimal 0.0-1.0"},
                    "reasoning": {"type": "string"},
                },
                "required": ["question", "probability", "reasoning"],
            },
        },
        "combination_logic": {"type": "string"},
        "final_probability": {
            "type": "number",
            "description": "Final probability as decimal between 0.0 and 1.0",
        },
        "key_uncertainties": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["sub_questions", "combination_logic", "final_probability", "key_uncertainties",
                  "event_type_scores"],
}

_DEVIL_SCHEMA = {
    "type": "object",
    "properties": {
        "consensus_challenged": {"type": "string"},
        "contrarian_arguments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "argument": {"type": "string"},
                    "evidence": {"type": "string"},
                    "strength": {"type": "string", "enum": ["weak", "moderate", "strong"]},
                },
                "required": ["argument", "evidence", "strength"],
            },
        },
        "what_consensus_overweights": {
            "type": "array",
            "items": {"type": "string"},
        },
        "what_consensus_underweights": {
            "type": "array",
            "items": {"type": "string"},
        },
        "final_probability": {
            "type": "number",
            "description": "Final probability as decimal between 0.0 and 1.0",
        },
        "key_uncertainties": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["consensus_challenged", "contrarian_arguments",
                  "what_consensus_overweights", "what_consensus_underweights",
                  "final_probability", "key_uncertainties",
                  "event_type_scores"],
}


# Inject event_type_scores into all agent schemas
for _schema in [_BASERATE_SCHEMA, _ANALOGY_SCHEMA, _DECOMP_SCHEMA, _DEVIL_SCHEMA]:
    _schema["properties"].update(_EVENT_TYPE_SCORES_PROPERTY)


# --- Event-type decomposition prompt addendum ---
# Appended to each agent prompt for V2 event-type scoring.
_EVENT_TYPE_ADDENDUM = """

EVENT-TYPE DECOMPOSITION:
In addition to your overall assessment, decompose the risk into these five event types. For each, estimate the probability (0.00 to 1.00) that it will occur in {country_name} within 30 days:
- ACE (Armed Conflict Escalation): military clashes, bombings, targeted violence increasing beyond baseline
- MCU (Mass Civil Unrest): large-scale protests, riots, strikes disrupting normal operations
- REC (Regime/Executive Change): coup, forced resignation, emergency succession, contested transfer of power
- PSS (Policy/Sanctions Shift): new sanctions, trade restrictions, regulatory changes affecting foreign operations
- CID (Critical Infrastructure Disruption): attacks on or failures of energy, transport, communications, or financial systems

Your overall final_probability should be consistent with your event_type_scores (typically close to the maximum of the individual scores).
For each type with P > 0.10, provide a one-sentence key driver in event_type_drivers.
"""


def _format_acled_summary(acled_data: dict) -> str:
    """Format conflict event summary data into readable text for prompts."""
    if not acled_data or acled_data.get("total_events", 0) == 0:
        return "No conflict events recorded in the lookback period."

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

    prompt += _EVENT_TYPE_ADDENDUM.format(country_name=country_config["name"])

    result = generate_with_tool(
        prompt=prompt, tool_name="submit_forecast",
        tool_schema=_BASERATE_SCHEMA, model="sonnet", temperature=0.3,
    )
    result["agent_type"] = "baserate"
    _validate_forecast(result, track_a_prob=track_a_result["probability"])
    validate_event_type_scores(result, track_a_prob=track_a_result["probability"])
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
    prompt += _EVENT_TYPE_ADDENDUM.format(country_name=country_config["name"])

    result = generate_with_tool(
        prompt=prompt, tool_name="submit_forecast",
        tool_schema=_ANALOGY_SCHEMA, model="sonnet", temperature=0.4,
    )
    result["agent_type"] = "analogy"
    _validate_forecast(result, track_a_prob=track_a_result["probability"])
    validate_event_type_scores(result, track_a_prob=track_a_result["probability"])
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
    prompt += _EVENT_TYPE_ADDENDUM.format(country_name=country_config["name"])

    result = generate_with_tool(
        prompt=prompt, tool_name="submit_forecast",
        tool_schema=_DECOMP_SCHEMA, model="sonnet", temperature=0.3,
    )
    result["agent_type"] = "decomposition"
    _validate_forecast(result, track_a_prob=track_a_result["probability"])
    validate_event_type_scores(result, track_a_prob=track_a_result["probability"])
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
    prompt += _EVENT_TYPE_ADDENDUM.format(country_name=country_config["name"])

    result = generate_with_tool(
        prompt=prompt, tool_name="submit_forecast",
        tool_schema=_DEVIL_SCHEMA, model="sonnet", temperature=0.5,
    )
    result["agent_type"] = "devil"
    _validate_forecast(result, track_a_prob=track_a_result["probability"])
    validate_event_type_scores(result, track_a_prob=track_a_result["probability"])
    logger.info("Agent 4 (devil) for %s: P=%.3f",
                country_config["iso3"], result["final_probability"])
    return result


def _validate_forecast(result: dict, track_a_prob: float = None) -> None:
    """Ensure forecast result has valid final_probability."""
    validate_probability(result, track_a_prob=track_a_prob)
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
                logger.error("Agent %d failed: %s", idx + 1, e, exc_info=True)
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
        logger.error("Agent 4 (devil) failed: %s", e, exc_info=True)
        results.append({
            "final_probability": track_a_result["probability"],
            "agent_type": "devil",
            "key_uncertainties": [f"Agent failed: {e}"],
        })

    return results
