"""
Orchestrator for the ask-a-question feature.

Runs the same 4-agent + Opus supervisor pipeline as the daily dashboard,
but retargeted to a user-specified scenario and deadline. Reuses
Track A, event summary, and reasoning chain helpers from the daily path.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date

from config.settings import load_prompt
from track_a.predict import predict_track_a
from track_b.extraction import summarize_reasoning_chains
from track_b.forecasting import (
    _format_event_summary, _validate_forecast,
    _ASK_BASERATE_SCHEMA, _ASK_ANALOGY_SCHEMA,
    _ASK_DECOMP_SCHEMA, _ASK_DEVIL_SCHEMA,
)
from track_b.supervisor import reconcile_ask
from utils.api_client import generate_with_tool
from utils.db import get_event_summary, get_recent_reasoning_chains
from utils.logger import logger
from utils.validation import validate_probability


def _ask_shared_prompt_kwargs(country_config: dict, track_a_result: dict,
                               reasoning_summary: str, event_data: dict,
                               scenario: str, user_deadline: str,
                               horizon_days: int) -> dict:
    """Prompt variables common to the base-rate, analogy, and decomp agents."""
    components = track_a_result["components"]
    return {
        "country_name": country_config["name"],
        "custom_scenario": scenario,
        "user_deadline": user_deadline,
        "horizon_days": horizon_days,
        "track_a_probability": f"{track_a_result['probability'] * 100:.1f}",
        "polity_category": country_config["polity_category"],
        "polity_code": country_config["polity_code"],
        "infant_mortality": country_config["infant_mortality_per_1000"],
        "years_since_instability": country_config["years_since_last_instability"],
        "neighborhood_risk": f"{components['neighborhood_contagion']:.2f}",
        "gpr_trend": components["gpr_trend_description"],
        "reasoning_chains_summary": reasoning_summary,
        "event_summary": _format_event_summary(event_data),
    }


def ask_baserate(country_config, track_a_result, reasoning_summary,
                 event_data, scenario, user_deadline, horizon_days):
    kwargs = _ask_shared_prompt_kwargs(country_config, track_a_result,
                                        reasoning_summary, event_data,
                                        scenario, user_deadline, horizon_days)
    prompt = load_prompt("ask_baserate").format(**kwargs)
    result = generate_with_tool(
        prompt=prompt, tool_name="submit_forecast",
        tool_schema=_ASK_BASERATE_SCHEMA, model="sonnet", temperature=0.3,
    )
    result["agent_type"] = "baserate"
    _validate_forecast(result)
    return result


def ask_analogy(country_config, track_a_result, reasoning_summary,
                event_data, scenario, user_deadline, horizon_days):
    kwargs = _ask_shared_prompt_kwargs(country_config, track_a_result,
                                        reasoning_summary, event_data,
                                        scenario, user_deadline, horizon_days)
    prompt = load_prompt("ask_analogy").format(**kwargs)
    result = generate_with_tool(
        prompt=prompt, tool_name="submit_forecast",
        tool_schema=_ASK_ANALOGY_SCHEMA, model="sonnet", temperature=0.4,
    )
    result["agent_type"] = "analogy"
    _validate_forecast(result)
    return result


def ask_decomp(country_config, track_a_result, reasoning_summary,
               event_data, scenario, user_deadline, horizon_days):
    kwargs = _ask_shared_prompt_kwargs(country_config, track_a_result,
                                        reasoning_summary, event_data,
                                        scenario, user_deadline, horizon_days)
    prompt = load_prompt("ask_decomp").format(**kwargs)
    result = generate_with_tool(
        prompt=prompt, tool_name="submit_forecast",
        tool_schema=_ASK_DECOMP_SCHEMA, model="sonnet", temperature=0.3,
    )
    result["agent_type"] = "decomposition"
    _validate_forecast(result)
    return result


def ask_devil(country_config, track_a_result, reasoning_summary,
              event_data, scenario, user_deadline, horizon_days,
              preliminary_forecasts):
    probs = [f["final_probability"] for f in preliminary_forecasts]
    avg = sum(probs) / len(probs) if probs else track_a_result["probability"]
    direction = "high" if avg > 0.5 else "moderate" if avg > 0.2 else "low"

    agent_lines = []
    for f in preliminary_forecasts:
        atype = f.get("agent_type", "unknown")
        ap = f.get("final_probability", 0)
        agent_lines.append(f"Agent ({atype}): P={ap*100:.1f}%")
        if atype == "baserate":
            for adj in f.get("upward_adjustments", [])[:6]:
                agent_lines.append(
                    f"  UP: {adj.get('factor','')} (+{adj.get('magnitude',0):.2f}): "
                    f"{adj.get('reasoning','')[:150]}"
                )
            for adj in f.get("downward_adjustments", [])[:6]:
                agent_lines.append(
                    f"  DOWN: {adj.get('factor','')} (-{adj.get('magnitude',0):.2f}): "
                    f"{adj.get('reasoning','')[:150]}"
                )
        elif atype == "analogy":
            for a in f.get("analogies", [])[:4]:
                agent_lines.append(
                    f"  Analogy: {a.get('country','?')} {a.get('year','?')} - "
                    f"{a.get('outcome','')[:120]}"
                )
        elif atype == "decomposition":
            for sq in f.get("sub_questions", [])[:6]:
                agent_lines.append(
                    f"  SubQ: {sq.get('question','')[:100]} -> "
                    f"P={sq.get('probability',0):.0%}"
                )
    agent_reasoning = "\n".join(agent_lines) if agent_lines else "No detailed reasoning available."

    kwargs = _ask_shared_prompt_kwargs(country_config, track_a_result,
                                        reasoning_summary, event_data,
                                        scenario, user_deadline, horizon_days)
    kwargs["consensus_direction"] = direction
    kwargs["preliminary_average"] = f"{avg * 100:.1f}"
    kwargs["agent_reasoning"] = agent_reasoning

    prompt = load_prompt("ask_devil").format(**kwargs)
    result = generate_with_tool(
        prompt=prompt, tool_name="submit_forecast",
        tool_schema=_ASK_DEVIL_SCHEMA, model="sonnet", temperature=0.5,
    )
    result["agent_type"] = "devil"
    _validate_forecast(result)
    return result


def _run_ask_ensemble(country_config, track_a_result, reasoning_summary,
                      event_data, scenario, user_deadline, horizon_days):
    """Run the 4 ask agents: 1-3 in parallel, then devil with their outputs."""
    agent_funcs = [
        ("baserate", ask_baserate),
        ("analogy", ask_analogy),
        ("decomposition", ask_decomp),
    ]
    results = [None, None, None]
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_idx = {}
        for idx, (name, func) in enumerate(agent_funcs):
            future = executor.submit(
                func, country_config, track_a_result, reasoning_summary,
                event_data, scenario, user_deadline, horizon_days,
            )
            future_to_idx[future] = idx
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                logger.error("Ask agent %d failed: %s", idx + 1, e, exc_info=True)
                results[idx] = {
                    "final_probability": track_a_result["probability"],
                    "agent_type": agent_funcs[idx][0],
                    "key_uncertainties": [f"Agent failed: {e}"],
                }

    try:
        results.append(ask_devil(country_config, track_a_result,
                                  reasoning_summary, event_data,
                                  scenario, user_deadline, horizon_days,
                                  results[:3]))
    except Exception as e:
        logger.error("Ask devil failed: %s", e, exc_info=True)
        results.append({
            "final_probability": track_a_result["probability"],
            "agent_type": "devil",
            "key_uncertainties": [f"Agent failed: {e}"],
        })
    return results


def generate_ask_forecast(country_config: dict, scenario: str,
                           user_deadline: str, user_email: str,
                           conn) -> dict:
    """
    Produce a probability forecast for a user-specified scenario.

    Args:
        country_config: Country configuration dict (from load_country_config
            or load_all_available_country_configs()[iso3]).
        scenario: Free-text scenario description (10-500 chars).
        user_deadline: ISO date string (YYYY-MM-DD).
        user_email: Submitter email (used for logging only here).
        conn: SQLite connection.
    """
    iso3 = country_config["iso3"]
    today = datetime.now().astimezone().date()
    deadline_date = date.fromisoformat(user_deadline)
    horizon_days = (deadline_date - today).days

    logger.info("Ask forecast start: %s scenario=%r deadline=%s horizon=%dd",
                iso3, scenario[:60], user_deadline, horizon_days)

    track_a = predict_track_a(country_config, conn)
    event_data = get_event_summary(conn, iso3, days=30)
    chains = get_recent_reasoning_chains(conn, iso3, days=7)
    if chains:
        reasoning_summary = summarize_reasoning_chains(chains)
    else:
        reasoning_summary = (
            f"No recent articles available. Country context: "
            f"{country_config.get('risk_context', '')}"
        )

    ensemble_results = _run_ask_ensemble(
        country_config, track_a, reasoning_summary, event_data,
        scenario, user_deadline, horizon_days,
    )

    supervisor_result = reconcile_ask(
        country_config, track_a, ensemble_results,
        scenario=scenario, user_deadline=user_deadline,
        horizon_days=horizon_days,
        reasoning_summary=reasoning_summary, event_data=event_data,
    )

    agent_breakdown = {}
    for a in ensemble_results:
        atype = a.get("agent_type", "unknown")
        agent_breakdown[atype] = {
            "probability": a.get("final_probability"),
            "reasoning": _agent_reasoning_snippet(a),
        }

    return {
        "probability": supervisor_result["final_probability"],
        "confidence": supervisor_result.get("confidence", "medium"),
        "track_a_baseline": track_a["probability"],
        "executive_summary": supervisor_result.get("executive_summary", ""),
        "narrative_summary": supervisor_result.get("narrative_summary", ""),
        "key_risk_factors": supervisor_result.get("key_risk_factors", []),
        "key_stabilizing_factors": supervisor_result.get("key_stabilizing_factors", []),
        "agent_breakdown": agent_breakdown,
        "supervisor_synthesis": supervisor_result.get("narrative_summary", ""),
        "agreements": supervisor_result.get("agreements", []),
        "disagreements": supervisor_result.get("disagreements", []),
        "ensemble_results": ensemble_results,
        "generated_at": datetime.now().astimezone().isoformat(),
    }


def _agent_reasoning_snippet(agent_result: dict) -> str:
    """Short reasoning string for the agent_breakdown response field."""
    atype = agent_result.get("agent_type", "unknown")
    if atype == "baserate":
        ups = agent_result.get("upward_adjustments", [])
        downs = agent_result.get("downward_adjustments", [])
        parts = [f"UP: {a.get('factor','')}" for a in ups[:2]]
        parts += [f"DOWN: {a.get('factor','')}" for a in downs[:2]]
        return "; ".join(parts) or "No significant adjustments."
    if atype == "analogy":
        return (agent_result.get("synthesis") or "")[:400]
    if atype == "decomposition":
        return (agent_result.get("combination_logic") or "")[:400]
    if atype == "devil":
        args = agent_result.get("contrarian_arguments", [])
        return "; ".join(
            f"[{a.get('strength','?')}] {a.get('argument','')[:120]}"
            for a in args[:2]
        ) or "No strong contrarian arguments."
    return ""
