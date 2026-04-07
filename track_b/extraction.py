"""
LLM-powered causal reasoning extraction from news articles.
Processes one article at a time and returns structured reasoning chains.
"""

import json
from config.settings import load_prompt
from utils.api_client import generate, MODELS
from utils.db import insert_reasoning_chain
from utils.logger import logger


PROMPT_VERSION = "v1"


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
        article_text=article_text[:8000],  # Truncate very long articles
    )

    result = generate(
        prompt=prompt,
        model="sonnet",
        temperature=0.2,
        max_tokens=2048,
    )

    # Validate expected fields
    _validate_extraction(result)

    return result


def _validate_extraction(result: dict) -> None:
    """Validate that extraction result has expected structure."""
    required_fields = ["causal_factors", "counterarguments", "key_actors"]
    for field in required_fields:
        if field not in result:
            result[field] = []

    # Validate causal factors structure
    for factor in result.get("causal_factors", []):
        if "confidence" in factor:
            factor["confidence"] = max(0.0, min(1.0, float(factor["confidence"])))
        if "direction" not in factor:
            factor["direction"] = "increases_risk"

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

    model_version = MODELS["sonnet"]
    insert_reasoning_chain(
        conn=db_conn,
        country_iso3=country_config["iso3"],
        article_id=article_id,
        chain_json=json.dumps(reasoning),
        prompt_version=PROMPT_VERSION,
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

    all_factors = []
    all_counterargs = []

    for chain in chains:
        chain_data = chain if isinstance(chain, dict) else json.loads(chain.get("chain_json", "{}"))
        for factor in chain_data.get("causal_factors", []):
            direction = factor.get("direction", "increases_risk")
            confidence = factor.get("confidence", 0.5)
            symbol = "+" if direction == "increases_risk" else "-"
            all_factors.append(
                f"  [{symbol}] {factor.get('factor', 'unknown')} "
                f"(confidence: {confidence:.1f}, evidence: {factor.get('evidence', 'N/A')[:100]})"
            )
        for ca in chain_data.get("counterarguments", []):
            all_counterargs.append(f"  - {ca[:150]}")

    summary_parts = []
    if all_factors:
        summary_parts.append("CAUSAL FACTORS (from recent reporting):")
        # Deduplicate similar factors, keep top 10
        seen = set()
        unique_factors = []
        for f in all_factors:
            key = f[:50].lower()
            if key not in seen:
                seen.add(key)
                unique_factors.append(f)
        summary_parts.extend(unique_factors[:10])

    if all_counterargs:
        summary_parts.append("\nCOUNTERARGUMENTS:")
        summary_parts.extend(all_counterargs[:5])

    return "\n".join(summary_parts)
