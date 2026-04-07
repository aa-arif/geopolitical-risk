"""
Data ingestion pipeline.
Fetches data from ACLED, GDELT, RSS feeds (via feedparser + trafilatura).
"""

import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import trafilatura

from config.settings import (
    ACLED_API_BASE, ACLED_TOKEN_URL, ACLED_EMAIL, ACLED_PASSWORD,
    GDELT_API_BASE,
)
from utils.db import get_connection, insert_article
from utils.logger import logger


def _http_get(url: str, timeout: int = 30) -> str:
    """Simple HTTP GET with timeout."""
    req = urllib.request.Request(url, headers={"User-Agent": "GeoRisk/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_full_text(url: str) -> str:
    """Fetch and extract full article text from a URL using trafilatura."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded)
            return text or ""
    except Exception as e:
        logger.warning("Failed to fetch full text from %s: %s", url, e)
    return ""


# --- ACLED OAuth Token Cache ---
_acled_token_cache = {
    "token": None,
    "expires_at": 0.0,  # unix timestamp
}


def _get_acled_token() -> str:
    """
    Get a valid ACLED OAuth access token, refreshing if expired.
    Tokens are valid for 24 hours; we refresh at 23 hours to be safe.
    """
    now = time.time()
    if _acled_token_cache["token"] and now < _acled_token_cache["expires_at"]:
        return _acled_token_cache["token"]

    logger.info("Requesting new ACLED OAuth token...")
    resp = requests.post(
        ACLED_TOKEN_URL,
        data={
            "username": ACLED_EMAIL,
            "password": ACLED_PASSWORD,
            "grant_type": "password",
            "client_id": "acled",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]

    # Cache with 23-hour expiry (actual is 24h)
    _acled_token_cache["token"] = token
    _acled_token_cache["expires_at"] = now + 23 * 3600

    logger.info("ACLED OAuth token acquired (expires in 23h).")
    return token


def ingest_acled(country_iso3: str, days: int = 30) -> int:
    """
    Fetch recent ACLED conflict events for a country via OAuth API.

    Returns number of events ingested.
    """
    if not ACLED_EMAIL or not ACLED_PASSWORD:
        logger.warning("ACLED credentials not set. Skipping ACLED ingestion.")
        return 0

    try:
        token = _get_acled_token()
    except Exception as e:
        logger.error("ACLED OAuth failed: %s", e)
        return 0

    start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    params = {
        "iso": country_iso3,
        "event_date": f"{start_date}|",
        "event_date_where": ">=",
        "limit": 500,
    }

    try:
        resp = requests.get(
            ACLED_API_BASE,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
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
    Fetch articles from RSS feeds using feedparser.
    Extracts full article text via trafilatura.

    Returns number of articles ingested.
    """
    feeds = country_config.get("news_sources_rss", [])
    if not feeds:
        return 0

    iso3 = country_config["iso3"]
    conn = get_connection()
    total = 0

    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            logger.warning("RSS fetch failed for %s: %s", feed_url, e)
            continue

        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            link = entry.get("link", "")
            published = entry.get("published", "")
            description = entry.get("summary", "")

            if not title:
                continue

            # Check for duplicates by URL
            existing = conn.execute(
                "SELECT id FROM articles WHERE url = ?", (link,)
            ).fetchone()
            if existing:
                continue

            # Fetch full text; fall back to RSS description
            full_text = ""
            if link:
                full_text = fetch_full_text(link)
                time.sleep(2)  # Be polite to servers
            if not full_text:
                full_text = description

            try:
                insert_article(
                    conn, iso3, title[:500], feed_url,
                    link[:1000], published[:100], full_text[:50000],
                )
                total += 1
            except Exception as e:
                logger.debug("Skipping article: %s", e)

    conn.commit()
    conn.close()
    logger.info("Ingested %d RSS articles for %s.", total, iso3)
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
