"""
LLM-powered causal reasoning extraction from news articles.
Processes one article at a time and returns structured reasoning chains.
Uses extraction_v2 prompt with mechanism fields, structured counterarguments,
and data point extraction.
"""

import json
from config.settings import load_prompt, PROMPT_VERSIONS
from utils.api_client import generate, MODELS
from utils.db import insert_reasoning_chain
from utils.logger import logger


def extract_reasoning(article_text: str, country_config: dict) -> dict:
    """
    Extract structured causal reasoning from a single article.

    Args:
        article_text: Full text of the article
        country_config: Country configuration dict

    Returns:
        Structured reasoning chain dict
    """
    template = load_prompt("extraction")

    prompt = template.format(
        country_name=country_config["name"],
        country_context=country_config["risk_context"],
        article_text=article_text[:8000] + ("\n[TRUNCATED]" if len(article_text) > 8000 else ""),
    )

    result = generate(
        prompt=prompt,
        model="sonnet",
        temperature=0.2,
        max_tokens=3000,
    )

    _validate_extraction(result)
    return result


def _validate_extraction(result: dict) -> None:
    """Validate extraction result handles both v1 and v2 formats."""
    # Ensure required lists exist
    for field in ["causal_factors", "counterarguments", "key_actors"]:
        if field not in result:
            result[field] = []

    # Validate causal factors
    for factor in result.get("causal_factors", []):
        if "confidence" in factor:
            factor["confidence"] = max(0.0, min(1.0, float(factor["confidence"])))
        if "direction" not in factor:
            factor["direction"] = "increases_risk"
        # v2 fields: ensure they exist even if model omitted them
        if "mechanism" not in factor:
            factor["mechanism"] = ""
        if "evidence" not in factor:
            factor["evidence"] = ""

    # Normalize counterarguments: v2 returns list of dicts, v1 returns strings
    normalized = []
    for ca in result.get("counterarguments", []):
        if isinstance(ca, str):
            normalized.append({"argument": ca, "evidence": "", "strength": "moderate"})
        elif isinstance(ca, dict):
            ca.setdefault("argument", "")
            ca.setdefault("evidence", "")
            ca.setdefault("strength", "moderate")
            normalized.append(ca)
    result["counterarguments"] = normalized

    # v2 fields with defaults
    if "data_points_extracted" not in result:
        result["data_points_extracted"] = []
    if "source_reliability_tier" not in result:
        result["source_reliability_tier"] = 3
    if "timeframe" not in result:
        result["timeframe"] = "unspecified"


def extract_and_store(article_id: int, article_text: str,
                      country_config: dict, db_conn) -> dict:
    """
    Extract reasoning from an article and store it in the database.

    Returns the extracted reasoning chain.
    """
    reasoning = extract_reasoning(article_text, country_config)

    prompt_version = PROMPT_VERSIONS.get("extraction", "v2")
    model_version = MODELS["sonnet"]
    insert_reasoning_chain(
        conn=db_conn,
        country_iso3=country_config["iso3"],
        article_id=article_id,
        chain_json=json.dumps(reasoning),
        prompt_version=prompt_version,
        model_version=model_version,
    )

    logger.info(
        "Extracted %d causal factors from article %d for %s",
        len(reasoning.get("causal_factors", [])),
        article_id,
        country_config["iso3"],
    )

    return reasoning


def summarize_reasoning_chains(chains: list) -> str:
    """
    Summarize multiple reasoning chains into a concise text summary
    suitable for inclusion in forecasting prompts.
    """
    if not chains:
        return "No recent causal factors extracted from reporting."

    all_counterargs = []

    # Collect factors with their original confidence for proper sorting
    all_factors_with_conf = []
    for chain in chains:
        chain_data = (
            chain if isinstance(chain, dict)
            else json.loads(chain.get("chain_json", "{}"))
        )
        for factor in chain_data.get("causal_factors", []):
            confidence = factor.get("confidence", 0.5)
            direction = factor.get("direction", "increases_risk")
            mechanism = factor.get("mechanism", "")
            evidence = factor.get("evidence", "")
            symbol = "+" if direction == "increases_risk" else "-"
            line = f"  [{symbol}] {factor.get('factor', 'unknown')} (confidence: {confidence:.2f})"
            if mechanism:
                line += f"\n      Mechanism: {mechanism}"
            if evidence:
                line += f"\n      Evidence: {evidence}"
            all_factors_with_conf.append((confidence, line))

        for ca in chain_data.get("counterarguments", []):
            if isinstance(ca, dict):
                arg = ca.get("argument", "")
                strength = ca.get("strength", "")
                evidence = ca.get("evidence", "")
                line = f"  - [{strength}] {arg}"
                if evidence:
                    line += f" (evidence: {evidence})"
                all_counterargs.append(line)
            elif isinstance(ca, str):
                all_counterargs.append(f"  - {ca}")

    summary_parts = []
    if all_factors_with_conf:
        summary_parts.append("CAUSAL FACTORS (from recent reporting):")
        all_factors_with_conf.sort(key=lambda x: x[0], reverse=True)

        seen = set()
        unique_factors = []
        for conf, f in all_factors_with_conf:
            key = f[:80].lower()
            if key not in seen:
                seen.add(key)
                unique_factors.append(f)
        summary_parts.extend(unique_factors[:40])

    if all_counterargs:
        summary_parts.append("\nCOUNTERARGUMENTS:")
        summary_parts.extend(all_counterargs[:10])

    return "\n".join(summary_parts)
