"""
LLM-powered event classification for news articles.

Classifies articles into event categories (ACE, MCU, REC, PSS, CID)
using Claude Haiku in batches of 5. Stores classified events in the
unified conflict_events table with source='internal'.

This builds a proprietary event dataset that:
- Maps directly to our event taxonomy (no translation layer)
- Is real-time (articles classified as they're ingested)
- Cross-validates with GDELT keyword classifications
- Compounds into a dataset nobody else has
"""

import json
import sqlite3
from datetime import datetime, timedelta

from config.settings import EVENT_TYPE_RESOLUTION
from utils.api_client import generate_with_tool
from pipeline.ingest import _parse_publication_date
from utils.db import get_connection, insert_conflict_event
from utils.logger import logger


# --- Tool schema for batch classification ---

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "article_index": {
                        "type": "integer",
                        "description": "0-based index of the article in the batch",
                    },
                    "event_category": {
                        "type": "string",
                        "enum": ["ACE", "MCU", "REC", "PSS", "CID", "NONE"],
                        "description": "Primary event category, or NONE if not a conflict/risk event",
                    },
                    "sub_category": {
                        "type": "string",
                        "description": "Specific sub-category from the allowed list for this event type",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "fatalities_mentioned": {
                        "type": "boolean",
                        "description": "Whether the article mentions deaths or casualties",
                    },
                    "estimated_fatalities": {
                        "type": "integer",
                        "description": "Estimated fatality count if mentioned, 0 otherwise",
                    },
                    "key_actors": {
                        "type": "string",
                        "description": "Comma-separated list of key actors (governments, groups, individuals)",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Classification confidence 0.0-1.0",
                    },
                },
                "required": ["article_index", "event_category", "severity",
                             "fatalities_mentioned", "confidence"],
            },
        },
    },
    "required": ["classifications"],
}


def _build_classification_prompt(articles: list, country_name: str) -> str:
    """Build the prompt for batch article classification."""
    # Build sub-category reference from config
    subcategory_ref = []
    for et, cfg in EVENT_TYPE_RESOLUTION.items():
        cats = cfg.get("internal_categories", [])
        if cats:
            subcategory_ref.append(f"  {et} ({cfg['name']}): {', '.join(cats)}")

    articles_text = []
    for i, art in enumerate(articles):
        title = art.get("title", "No title")
        text = art.get("full_text", "") or art.get("title", "")
        # Truncate to ~500 chars to fit batch in context
        text = text[:500].strip()
        source = art.get("source", "")
        source_hint = f"\nSource: {source}" if source else ""
        articles_text.append(f"[Article {i}]\nTitle: {title}{source_hint}\nText: {text}")

    return f"""Classify each article below by its primary geopolitical risk event type for {country_name}.

Event categories and sub-categories:
{chr(10).join(subcategory_ref)}
  NONE: Not a geopolitical risk event (sports, entertainment, weather, etc.)

For each article, determine:
1. The primary event category (ACE/MCU/REC/PSS/CID/NONE)
2. A specific sub-category from the list above
3. Severity (low/medium/high) based on scale, impact, and escalation potential
4. Whether fatalities are mentioned and estimated count
5. Key actors involved
6. Your confidence in the classification (0.0-1.0)

{chr(10).join(articles_text)}

Classify all {len(articles)} articles. Return one classification per article."""


def classify_articles(articles: list, country_config: dict) -> list:
    """
    Classify a batch of articles (up to 5) into event categories.

    Args:
        articles: List of article dicts with 'id', 'title', 'full_text'
        country_config: Country configuration dict

    Returns:
        List of classification dicts with article_id and event details
    """
    if not articles:
        return []

    prompt = _build_classification_prompt(articles, country_config["name"])

    try:
        result = generate_with_tool(
            prompt=prompt,
            tool_name="submit_classifications",
            tool_schema=_CLASSIFY_SCHEMA,
            model="haiku",
            temperature=0.1,
            max_tokens=2048,
        )
    except Exception as e:
        logger.warning("Classification batch failed: %s", e)
        return []

    classifications = result.get("classifications", [])
    enriched = []
    for clf in classifications:
        idx = clf.get("article_index", -1)
        if 0 <= idx < len(articles):
            clf["article_id"] = articles[idx]["id"]
            clf["article_title"] = articles[idx].get("title", "")
            enriched.append(clf)

    return enriched


def classify_and_store(conn, country_config: dict, batch_size: int = 5,
                        max_articles: int = 50) -> int:
    """
    Classify recent unclassified articles and store as conflict events.

    Fetches articles that haven't been classified yet (no corresponding
    conflict_event with source='internal'), classifies in batches,
    and inserts into conflict_events.

    Args:
        conn: Database connection
        country_config: Country configuration
        batch_size: Articles per LLM call (default 5)
        max_articles: Max articles to process per invocation

    Returns:
        Number of conflict events created
    """
    iso3 = country_config["iso3"]
    cutoff = (datetime.now().astimezone() - timedelta(days=7)).strftime("%Y-%m-%d")

    # Find articles not yet classified (no matching conflict_event from 'internal')
    cursor = conn.execute(
        """SELECT a.id, a.title, a.full_text, a.published_date
           FROM articles a
           WHERE a.country_iso3 = ?
           AND a.pulled_at >= ?
           AND a.is_relevant = 1
           AND a.id NOT IN (
               SELECT DISTINCT source_article_id FROM conflict_events
               WHERE country_iso3 = ? AND source = 'internal'
               AND source_article_id IS NOT NULL
           )
           ORDER BY a.published_date DESC
           LIMIT ?""",
        (iso3, cutoff, iso3, max_articles),
    )
    articles = [dict(row) for row in cursor.fetchall()]

    if not articles:
        logger.debug("No unclassified articles for %s.", iso3)
        return 0

    total_events = 0

    # Process in batches
    for batch_start in range(0, len(articles), batch_size):
        batch = articles[batch_start:batch_start + batch_size]
        classifications = classify_articles(batch, country_config)

        for clf in classifications:
            category = clf.get("event_category", "NONE")
            if category == "NONE":
                continue

            article_id = clf.get("article_id")
            article = next((a for a in batch if a["id"] == article_id), None)
            if not article:
                continue

            event_date = _parse_publication_date(article.get("published_date") or "")
            if not event_date:
                logger.warning(
                    "Skipping article %d: unparseable published_date %r",
                    article.get("id"), article.get("published_date"),
                )
                continue

            try:
                insert_conflict_event(
                    conn, iso3, event_date, category, "internal",
                    event_type=clf.get("sub_category"),
                    fatalities=clf.get("estimated_fatalities", 0),
                    severity=clf.get("severity"),
                    source_article_id=article_id,
                    actors=clf.get("key_actors"),
                    confidence=clf.get("confidence"),
                    notes=clf.get("article_title", "")[:200],
                )
                total_events += 1
            except sqlite3.IntegrityError:
                pass

    conn.commit()
    logger.info("Classified %d/%d articles for %s -> %d conflict events.",
                len(articles), len(articles), iso3, total_events)
    return total_events
