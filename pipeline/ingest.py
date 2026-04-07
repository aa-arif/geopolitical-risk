"""
Data ingestion pipeline.
Fetches data from ACLED, GDELT, RSS feeds, and World Bank APIs.
"""

import json
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from config.settings import (
    ACLED_API_BASE, ACLED_API_KEY, ACLED_EMAIL,
    GDELT_API_BASE, WORLD_BANK_API_BASE,
)
from utils.db import get_connection
from utils.logger import logger


def _http_get(url: str, timeout: int = 30) -> str:
    """Simple HTTP GET with timeout."""
    req = urllib.request.Request(url, headers={"User-Agent": "GeoRisk/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def ingest_acled(country_iso3: str, days: int = 30) -> int:
    """
    Fetch recent ACLED conflict events for a country.

    Returns number of events ingested.
    """
    if not ACLED_API_KEY or not ACLED_EMAIL:
        logger.warning("ACLED API credentials not set. Skipping ACLED ingestion.")
        return 0

    start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    params = urllib.parse.urlencode({
        "key": ACLED_API_KEY,
        "email": ACLED_EMAIL,
        "iso": country_iso3,
        "event_date": f"{start_date}|",
        "event_date_where": ">=",
        "limit": 500,
    })

    url = f"{ACLED_API_BASE}?{params}"

    try:
        raw = _http_get(url)
        data = json.loads(raw)
    except Exception as e:
        logger.error("ACLED fetch failed for %s: %s", country_iso3, e)
        return 0

    events = data.get("data", [])
    if not events:
        logger.info("No ACLED events for %s in last %d days.", country_iso3, days)
        return 0

    conn = get_connection()
    count = 0
    for ev in events:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO acled_events
                   (country_iso3, event_date, event_type, sub_event_type,
                    fatalities, latitude, longitude, source, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    country_iso3,
                    ev.get("event_date", ""),
                    ev.get("event_type", ""),
                    ev.get("sub_event_type", ""),
                    int(ev.get("fatalities", 0)),
                    float(ev.get("latitude", 0)) if ev.get("latitude") else None,
                    float(ev.get("longitude", 0)) if ev.get("longitude") else None,
                    ev.get("source", ""),
                    ev.get("notes", "")[:500],
                ),
            )
            count += 1
        except Exception as e:
            logger.debug("Skipping ACLED event: %s", e)

    conn.commit()
    conn.close()
    logger.info("Ingested %d ACLED events for %s.", count, country_iso3)
    return count


def ingest_rss(country_config: dict) -> int:
    """
    Fetch articles from RSS feeds for a country.

    Returns number of articles ingested.
    """
    feeds = country_config.get("news_sources_rss", [])
    if not feeds:
        return 0

    conn = get_connection()
    total = 0

    for feed_url in feeds:
        try:
            raw = _http_get(feed_url, timeout=15)
            root = ET.fromstring(raw)
        except Exception as e:
            logger.warning("RSS fetch failed for %s: %s", feed_url, e)
            continue

        # Handle both RSS and Atom formats
        items = root.findall(".//item") or root.findall(
            ".//{http://www.w3.org/2005/Atom}entry"
        )

        for item in items[:20]:  # Limit per feed
            title = _get_text(item, "title") or _get_text(
                item, "{http://www.w3.org/2005/Atom}title"
            )
            link = _get_text(item, "link") or ""
            if not link:
                link_el = item.find("{http://www.w3.org/2005/Atom}link")
                if link_el is not None:
                    link = link_el.get("href", "")

            description = (
                _get_text(item, "description")
                or _get_text(item, "{http://www.w3.org/2005/Atom}summary")
                or ""
            )
            pub_date = (
                _get_text(item, "pubDate")
                or _get_text(item, "{http://www.w3.org/2005/Atom}published")
                or ""
            )

            if not title:
                continue

            # Check for duplicates by URL
            existing = conn.execute(
                "SELECT id FROM articles WHERE url = ?", (link,)
            ).fetchone()
            if existing:
                continue

            try:
                conn.execute(
                    """INSERT INTO articles
                       (country_iso3, title, source, url, published_date, full_text)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        country_config["iso3"],
                        title[:500],
                        feed_url,
                        link[:1000],
                        pub_date[:100],
                        description[:5000],
                    ),
                )
                total += 1
            except Exception as e:
                logger.debug("Skipping article: %s", e)

    conn.commit()
    conn.close()
    logger.info("Ingested %d RSS articles for %s.", total, country_config["iso3"])
    return total


def ingest_gdelt(country_iso3: str, days: int = 7) -> int:
    """
    Fetch GDELT events for a country (via GDELT DOC API).

    Returns number of events stored.
    """
    country_names = {
        "NGA": "Nigeria", "BGD": "Bangladesh", "PAK": "Pakistan",
        "PHL": "Philippines", "TUR": "Turkey",
    }
    country_name = country_names.get(country_iso3, country_iso3)

    params = urllib.parse.urlencode({
        "query": f"{country_name} protest OR conflict OR instability",
        "mode": "ArtList",
        "maxrecords": 50,
        "format": "json",
        "timespan": f"{days}d",
    })

    url = f"{GDELT_API_BASE}?{params}"

    try:
        raw = _http_get(url)
        data = json.loads(raw)
    except Exception as e:
        logger.warning("GDELT fetch failed for %s: %s", country_iso3, e)
        return 0

    articles = data.get("articles", [])
    if not articles:
        return 0

    conn = get_connection()
    count = 0

    for art in articles:
        tone = art.get("tone", 0)
        try:
            conn.execute(
                """INSERT INTO gdelt_events
                   (country_iso3, event_date, event_code, goldstein_scale,
                    num_mentions, avg_tone, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    country_iso3,
                    art.get("seendate", "")[:10],
                    "",
                    float(tone) if tone else 0.0,
                    1,
                    float(tone) if tone else 0.0,
                    art.get("url", "")[:1000],
                ),
            )
            count += 1
        except Exception as e:
            logger.debug("Skipping GDELT event: %s", e)

    conn.commit()
    conn.close()
    logger.info("Ingested %d GDELT events for %s.", count, country_iso3)
    return count


def ingest_all(country_config: dict) -> dict:
    """Run all ingestion for a single country. Returns counts."""
    iso3 = country_config["iso3"]
    return {
        "acled": ingest_acled(iso3),
        "rss": ingest_rss(country_config),
        "gdelt": ingest_gdelt(iso3),
    }


def _get_text(element, tag: str) -> str:
    """Safely get text content from an XML element."""
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""
