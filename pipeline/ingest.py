"""
Data ingestion pipeline.
Fetches data from ACLED (OAuth), NewsAPI, GDELT, RSS feeds (feedparser + trafilatura).
"""

import json
import time
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import trafilatura

from config.settings import (
    ACLED_API_BASE, ACLED_TOKEN_URL, ACLED_EMAIL, ACLED_PASSWORD,
    GDELT_API_BASE, NEWSAPI_KEY,
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


# ACLED API uses full country names, not ISO3 codes
_ISO3_TO_COUNTRY_NAME = {
    "NGA": "Nigeria",
    "BGD": "Bangladesh",
    "PAK": "Pakistan",
    "PHL": "Philippines",
    "TUR": "Turkey",
    "ETH": "Ethiopia",
    "MMR": "Myanmar",
    "IRQ": "Iraq",
    "COL": "Colombia",
    "SDN": "Sudan",
    "COD": "Democratic Republic of Congo",
    "EGY": "Egypt",
    "THA": "Thailand",
    "KEN": "Kenya",
    "UKR": "Ukraine",
    "SOM": "Somalia",
    "YEM": "Yemen",
    "AFG": "Afghanistan",
    "LBY": "Libya",
    "MLI": "Mali",
    "MOZ": "Mozambique",
    "VEN": "Venezuela",
    "HTI": "Haiti",
    "LBN": "Lebanon",
    "ZAF": "South Africa",
    "IND": "India",
    "MEX": "Mexico",
    "NER": "Niger",
    "CMR": "Cameroon",
    "TCD": "Chad",
}


def ingest_acled(country_iso3: str, days: int = 30) -> int:
    """
    Fetch recent ACLED conflict events for a country via OAuth API.

    Returns number of events ingested.
    """
    if not ACLED_EMAIL or not ACLED_PASSWORD:
        logger.warning("ACLED credentials not set. Skipping ACLED ingestion.")
        return 0

    country_name = _ISO3_TO_COUNTRY_NAME.get(country_iso3)
    if not country_name:
        logger.error("No ACLED country name mapping for %s", country_iso3)
        return 0

    try:
        token = _get_acled_token()
    except Exception as e:
        logger.error("ACLED OAuth failed: %s", e)
        return 0

    start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    # Paginated fetch -- ACLED caps at 5000 per request
    PAGE_SIZE = 5000
    MAX_EVENTS = 20000
    all_events = []
    page = 1

    while len(all_events) < MAX_EVENTS:
        params = {
            "country": country_name,
            "event_date": f"{start_date}|",
            "event_date_where": ">=",
            "limit": PAGE_SIZE,
            "page": page,
        }
        try:
            resp = requests.get(
                ACLED_API_BASE,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("ACLED fetch failed for %s (page %d): %s", country_iso3, page, e)
            break

        events = data.get("data", [])
        total_available = data.get("total_count", 0)
        all_events.extend(events)

        if not events:
            break

        logger.info("ACLED %s page %d: %d/%d fetched (total available: %s)",
                    country_iso3, page, len(all_events), total_available, total_available)

        if len(all_events) >= total_available or len(events) < PAGE_SIZE:
            break
        page += 1

    if not all_events:
        logger.info("No ACLED events for %s in last %d days.", country_iso3, days)
        return 0

    conn = get_connection()
    count = 0
    for ev in all_events:
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


def ingest_gdelt(country_config: dict, days: int = 7) -> int:
    """
    Fetch conflict-related articles from GDELT DOC 2.0 API.
    Stores in the articles table with full text via trafilatura.

    Returns number of articles ingested.
    """
    iso3 = country_config["iso3"]
    country_name = country_config["name"]

    query = f"{country_name} (protest OR conflict OR military OR attack OR violence OR coup)"
    params = urllib.parse.urlencode({
        "query": query,
        "mode": "ArtList",
        "maxrecords": 250,
        "format": "json",
        "timespan": f"{days}d",
    })

    url = f"{GDELT_API_BASE}?{params}"

    # Delay to avoid 429 when running many countries in parallel
    time.sleep(5)

    for attempt in range(2):
        try:
            raw = _http_get(url, timeout=120)
            data = json.loads(raw)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                logger.info("GDELT rate limited for %s, retrying in 30s...", iso3)
                time.sleep(30)
                continue
            logger.warning("GDELT DOC fetch failed for %s: %s", iso3, e)
            return 0
        except Exception as e:
            logger.warning("GDELT DOC fetch failed for %s: %s", iso3, e)
            return 0

    articles = data.get("articles", [])
    if not articles:
        logger.info("No GDELT articles for %s.", iso3)
        return 0

    conn = get_connection()
    count = 0
    scraped = 0

    for i, art in enumerate(articles):
        art_url = art.get("url", "")
        title = art.get("title", "")
        seen_date = art.get("seendate", "")[:10]
        domain = art.get("domain", "GDELT")

        if not art_url or not title:
            continue

        # Deduplicate by URL
        existing = conn.execute("SELECT id FROM articles WHERE url = ?", (art_url,)).fetchone()
        if existing:
            continue

        # Only scrape full text for the first 20 new articles
        full_text = ""
        if scraped < 20:
            full_text = fetch_full_text(art_url)
            scraped += 1
            time.sleep(2)
        if not full_text:
            full_text = title  # Store title as minimal text for the rest

        try:
            insert_article(conn, iso3, title[:500], f"GDELT/{domain}",
                           art_url[:1000], seen_date, full_text[:50000])
            count += 1
        except Exception as e:
            logger.debug("Skipping GDELT article: %s", e)

    conn.commit()
    conn.close()
    logger.info("Ingested %d GDELT articles for %s (%d with full text).", count, iso3, scraped)
    return count


def ingest_gdelt_events(country_config: dict, days: int = 7) -> int:
    """
    Fetch conflict event data from GDELT DOC API using artlist mode.
    Aggregates articles by date to produce daily conflict event counts.
    Stores in gdelt_conflict_events table as a supplement to ACLED.

    Returns number of daily records ingested.
    """
    iso3 = country_config["iso3"]
    country_name = country_config["name"]

    query = f"{country_name} (conflict OR protest OR attack OR violence OR coup)"
    params = urllib.parse.urlencode({
        "query": query,
        "mode": "artlist",
        "maxrecords": 250,
        "format": "json",
        "timespan": f"{days}d",
    })

    url = f"{GDELT_API_BASE}?{params}"

    try:
        raw = _http_get(url, timeout=120)
        data = json.loads(raw)
    except Exception as e:
        logger.warning("GDELT events fetch failed for %s: %s", iso3, e)
        return 0

    articles = data.get("articles", [])
    if not articles:
        logger.info("No GDELT event articles for %s.", iso3)
        return 0

    # Aggregate by date: count articles and average tone per day
    from collections import defaultdict
    daily = defaultdict(lambda: {"count": 0, "tones": [], "urls": []})
    for art in articles:
        seen = art.get("seendate", "")[:10]  # YYYYMMDD format
        if len(seen) == 8:
            seen = f"{seen[:4]}-{seen[4:6]}-{seen[6:8]}"
        elif len(seen) == 10 and "-" not in seen:
            seen = f"{seen[:4]}-{seen[4:6]}-{seen[6:8]}"
        if not seen or len(seen) < 10:
            continue
        daily[seen]["count"] += 1
        tone = art.get("tone", 0)
        if tone:
            daily[seen]["tones"].append(float(tone))
        if art.get("url"):
            daily[seen]["urls"].append(art["url"])

    conn = get_connection()
    count = 0

    for event_date, info in sorted(daily.items()):
        avg_tone = sum(info["tones"]) / len(info["tones"]) if info["tones"] else 0.0
        sample_url = info["urls"][0] if info["urls"] else ""

        try:
            conn.execute(
                """INSERT INTO gdelt_conflict_events
                   (country_iso3, event_date, event_type, num_articles,
                    avg_tone, latitude, longitude, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (iso3, event_date, "conflict_coverage", info["count"],
                 avg_tone, None, None, sample_url[:1000]),
            )
            count += 1
        except Exception as e:
            logger.debug("Skipping GDELT daily aggregate: %s", e)

    conn.commit()
    conn.close()
    logger.info("Ingested %d GDELT daily event records for %s (%d articles total).",
                count, iso3, len(articles))
    return count


def ingest_newsapi(country_config: dict, days: int = 7) -> int:
    """
    Fetch articles from NewsAPI for a country.
    Uses the 'everything' endpoint with country name + risk keywords.

    Returns number of articles ingested.
    """
    if not NEWSAPI_KEY:
        logger.warning("NewsAPI key not set. Skipping.")
        return 0

    iso3 = country_config["iso3"]
    country_name = country_config["name"]
    from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": f"{country_name} AND (politics OR conflict OR protest OR security OR military)",
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 20,
                "from": from_date,
            },
            headers={"X-Api-Key": NEWSAPI_KEY},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("NewsAPI fetch failed for %s: %s", country_name, e)
        return 0

    articles = data.get("articles", [])
    if not articles:
        logger.info("No NewsAPI articles for %s.", country_name)
        return 0

    conn = get_connection()
    count = 0

    for art in articles:
        title = art.get("title", "")
        url = art.get("url", "")
        published = art.get("publishedAt", "")
        content = art.get("content", "") or art.get("description", "") or ""
        source_name = art.get("source", {}).get("name", "NewsAPI")

        if not title or not url:
            continue

        # Deduplicate
        existing = conn.execute("SELECT id FROM articles WHERE url = ?", (url,)).fetchone()
        if existing:
            continue

        # Fetch full text via trafilatura (NewsAPI free tier truncates content)
        full_text = fetch_full_text(url) if url else ""
        time.sleep(2)
        if not full_text:
            full_text = content

        try:
            insert_article(conn, iso3, title[:500], source_name,
                           url[:1000], published[:100], full_text[:50000])
            count += 1
        except Exception as e:
            logger.debug("Skipping NewsAPI article: %s", e)

    conn.commit()
    conn.close()
    logger.info("Ingested %d NewsAPI articles for %s.", count, iso3)
    return count


def ingest_all(country_config: dict) -> dict:
    """Run all ingestion for a single country. Returns counts."""
    iso3 = country_config["iso3"]

    # NewsAPI first, fall back to RSS if NewsAPI returns < 5
    newsapi_count = ingest_newsapi(country_config)
    rss_count = 0
    if newsapi_count < 5:
        rss_count = ingest_rss(country_config)

    return {
        "acled": ingest_acled(iso3),
        "newsapi": newsapi_count,
        "rss": rss_count,
        "gdelt_articles": ingest_gdelt(country_config),
        "gdelt_events": ingest_gdelt_events(country_config),
    }
