"""
Data ingestion pipeline.
Fetches data from ACLED (OAuth), NewsAPI, GDELT, RSS feeds (feedparser + trafilatura).
"""

import json
import socket
import sqlite3
import time
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import requests
import trafilatura

from config.settings import (
    ACLED_API_BASE, ACLED_TOKEN_URL, ACLED_EMAIL, ACLED_PASSWORD,
    GDELT_API_BASE, NEWSAPI_KEY,
)
from utils.db import get_connection, insert_article, insert_conflict_event
from utils.logger import logger


# --- GDELT article -> event category keyword mapping ---
# Used for lightweight heuristic classification of GDELT articles into
# event categories. The full LLM classification (pipeline/classify.py)
# produces higher-quality labels; this is a fast first pass.
_GDELT_CATEGORY_KEYWORDS = {
    "ACE": ["battle", "airstrike", "bombing", "shelling", "attack", "military",
            "clash", "offensive", "missile", "drone strike", "ambush", "killed",
            "soldier", "troops", "combat", "artillery", "war ", "fighting"],
    "MCU": ["protest", "riot", "demonstrat", "rally", "march", "strike",
            "unrest", "dissent", "tear gas", "water cannon", "crowd"],
    "REC": ["coup", "overthrow", "resign", "impeach", "succession",
            "government change", "martial law", "emergency decree",
            "power transfer", "junta"],
}


def _classify_gdelt_article(title: str) -> str:
    """
    Classify a GDELT article title into an event category.
    Returns 'ACE', 'MCU', 'REC', or 'ACE' as default (conflict-related
    articles that don't clearly match MCU/REC are assumed ACE since the
    GDELT query already filters for conflict terms).
    """
    title_lower = title.lower()
    scores = {}
    for category, keywords in _GDELT_CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for kw in keywords if kw in title_lower)

    if scores.get("REC", 0) > 0:
        return "REC"
    if scores.get("MCU", 0) > scores.get("ACE", 0):
        return "MCU"
    return "ACE"  # default for conflict-query articles


def _http_get(url: str, timeout: int = 30) -> str:
    """Simple HTTP GET with reliable timeout via requests."""
    resp = requests.get(url, headers={"User-Agent": "GeoRisk/1.0"}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _gdelt_fetch_with_backoff(url: str, iso3: str, label: str = "GDELT") -> dict:
    """
    Fetch a GDELT URL with exponential backoff on 429/timeout.
    Returns parsed JSON dict, or empty dict on failure.
    """
    MAX_RETRIES = 4
    for attempt in range(MAX_RETRIES):
        try:
            raw = _http_get(url, timeout=60)
            return json.loads(raw)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429 and attempt < MAX_RETRIES - 1:
                delay = 30 * (2 ** attempt)  # 30s, 60s, 120s
                logger.info("%s rate limited for %s, retrying in %ds (attempt %d/%d)...",
                            label, iso3, delay, attempt + 1, MAX_RETRIES)
                time.sleep(delay)
                continue
            logger.warning("%s fetch failed for %s: %s", label, iso3, e)
            return {}
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < MAX_RETRIES - 1:
                delay = 20 * (2 ** attempt)  # 20s, 40s, 80s
                logger.info("%s timeout for %s, retrying in %ds (attempt %d/%d)...",
                            label, iso3, delay, attempt + 1, MAX_RETRIES)
                time.sleep(delay)
                continue
            logger.warning("%s fetch failed for %s: %s", label, iso3, e)
            return {}
        except Exception as e:
            logger.warning("%s fetch failed for %s: %s", label, iso3, e)
            return {}
    return {}


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


def _extract_domain(url: str) -> str:
    """Extract domain from a URL for per-domain rate limiting."""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc
    except Exception:
        return "unknown"


def fetch_full_texts_parallel(urls: list, max_workers: int = 5,
                                per_domain_delay: float = 1.0) -> dict:
    """
    Fetch full article text for multiple URLs concurrently.

    Rate-limits per domain (1s delay between requests to the same domain)
    but fetches from different domains in parallel.

    Args:
        urls: List of URLs to scrape
        max_workers: Max concurrent threads
        per_domain_delay: Seconds between requests to the same domain

    Returns:
        Dict mapping URL -> extracted text (empty string if failed)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from collections import defaultdict
    import threading

    if not urls:
        return {}

    # Per-domain lock and last-request timestamp
    domain_locks = defaultdict(threading.Lock)
    domain_last_request = defaultdict(float)

    def _fetch_with_domain_limit(url):
        domain = _extract_domain(url)
        with domain_locks[domain]:
            # Wait if we hit this domain recently
            elapsed = time.time() - domain_last_request[domain]
            if elapsed < per_domain_delay:
                time.sleep(per_domain_delay - elapsed)
            domain_last_request[domain] = time.time()

        return url, fetch_full_text(url)

    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_with_domain_limit, url): url for url in urls}
        for future in as_completed(futures):
            try:
                url, text = future.result()
                results[url] = text
            except Exception as e:
                url = futures[future]
                logger.warning("Parallel fetch failed for %s: %s", url, e)
                results[url] = ""

    return results


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


# ACLED API uses full country names, not ISO3 codes.
# Built dynamically from country configs; uses acled_country_name if present.
_ISO3_TO_COUNTRY_NAME = None


def _get_iso3_mapping():
    """Build ISO3 -> ACLED country name mapping from all available configs."""
    global _ISO3_TO_COUNTRY_NAME
    if _ISO3_TO_COUNTRY_NAME is None:
        from config.settings import load_all_available_country_configs
        mapping = {}
        for iso3, cfg in load_all_available_country_configs().items():
            mapping[iso3] = cfg.get("acled_country_name", cfg["name"])
        _ISO3_TO_COUNTRY_NAME = mapping
    return _ISO3_TO_COUNTRY_NAME


def ingest_acled(country_iso3: str, days: int = 30) -> int:
    """
    DEPRECATED: ACLED has prohibited predictive/early warning use cases.
    See: "The development of predictive or early warning systems using
    ACLED data is not permitted under our Terms & Conditions."

    This function is retained for backward compatibility but skips execution.
    Use GDELT (ingest_gdelt_events) + LLM classification (pipeline/classify.py)
    as the primary event data sources.

    Returns 0 (no events ingested).
    """
    logger.info("ACLED ingestion skipped (deprecated: ToS prohibits predictive use).")
    return 0

    # --- Original implementation preserved below for reference ---
    if not ACLED_EMAIL or not ACLED_PASSWORD:
        logger.warning("ACLED credentials not set. Skipping ACLED ingestion.")
        return 0

    country_name = _get_iso3_mapping().get(country_iso3)
    if not country_name:
        logger.error("No ACLED country name mapping for %s", country_iso3)
        return 0

    try:
        token = _get_acled_token()
    except requests.exceptions.RequestException as e:
        logger.error("ACLED OAuth failed: %s", e)
        return 0

    start_date = (datetime.now().astimezone() - timedelta(days=days)).strftime("%Y-%m-%d")

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
        except requests.exceptions.RequestException as e:
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
    try:
        count = 0
        for ev in all_events:
            try:
                conn.execute(
                    """INSERT INTO acled_events
                       (country_iso3, event_date, event_type, sub_event_type,
                        fatalities, latitude, longitude, source, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(country_iso3, event_date, event_type,
                                   sub_event_type, latitude, longitude)
                       DO UPDATE SET
                           fatalities = excluded.fatalities,
                           notes = excluded.notes,
                           source = excluded.source""",
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
            except sqlite3.IntegrityError as e:
                logger.debug("Skipping ACLED event: %s", e)

        conn.commit()
        logger.info("Ingested %d ACLED events for %s.", count, country_iso3)
        return count
    finally:
        conn.close()


def ingest_rss(country_config: dict) -> int:
    """
    Fetch articles from RSS feeds using feedparser.
    Extracts full article text via parallel trafilatura scraping.

    Returns number of articles ingested.
    """
    feeds = country_config.get("news_sources_rss", [])
    if not feeds:
        return 0

    iso3 = country_config["iso3"]
    conn = get_connection()
    try:
        # Phase 1: Parse feeds and collect new article metadata
        pending = []  # list of (title, link, published, description, feed_url)
        for feed_url in feeds:
            try:
                old_timeout = socket.getdefaulttimeout()
                socket.setdefaulttimeout(30)
                try:
                    feed = feedparser.parse(feed_url,
                                            request_headers={"User-Agent": "GeoRisk/1.0"})
                finally:
                    socket.setdefaulttimeout(old_timeout)
            except Exception as e:
                logger.warning("RSS fetch failed for %s: %s", feed_url, e)
                continue

            for entry in feed.entries[:20]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                if not title:
                    continue
                existing = conn.execute(
                    "SELECT id FROM articles WHERE url = ?", (link,)
                ).fetchone()
                if existing:
                    continue
                pending.append((title, link, entry.get("published", ""),
                                entry.get("summary", ""), feed_url))

        if not pending:
            return 0

        # Phase 2: Fetch full text in parallel (per-domain rate limited)
        urls_to_fetch = [p[1] for p in pending if p[1]]
        fetched_texts = fetch_full_texts_parallel(urls_to_fetch) if urls_to_fetch else {}

        # Phase 3: Insert articles
        total = 0
        for title, link, published, description, feed_url in pending:
            full_text = fetched_texts.get(link, "") or description
            try:
                insert_article(
                    conn, iso3, title[:500], feed_url,
                    link[:1000], published[:100], full_text[:50000],
                )
                total += 1
            except sqlite3.IntegrityError:
                pass

        conn.commit()
        logger.info("Ingested %d RSS articles for %s.", total, iso3)
        return total
    finally:
        conn.close()


def _parse_gdelt_date(raw) -> Optional[str]:
    """Parse a GDELT seendate value into YYYY-MM-DD. Returns None if unparseable.

    Accepts full ISO seendate (YYYYMMDDTHHMMSSZ), classic 8-char (YYYYMMDD),
    already-ISO prefix (YYYY-MM-DD...), and already-truncated 10-char values
    like YYYYMMDDTN that leaked into the DB before this parser existed.
    Returns None for empty, non-string, or values whose first 8 digits aren't
    a valid calendar date.
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        iso = s[:10]
    elif len(s) >= 8 and s[:8].isdigit():
        iso = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    else:
        return None
    try:
        datetime.strptime(iso, "%Y-%m-%d")
    except ValueError:
        return None
    return iso


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

    # Delay to avoid 429 when running many countries sequentially
    time.sleep(30)

    data = _gdelt_fetch_with_backoff(url, iso3, "GDELT DOC")

    articles = data.get("articles", [])
    if not articles:
        logger.info("No GDELT articles for %s.", iso3)
        return 0

    conn = get_connection()
    try:
        # Phase 1: Collect new article metadata
        pending = []  # (art_url, title, seen_date, domain)
        for art in articles:
            art_url = art.get("url", "")
            title = art.get("title", "")
            seen_date = _parse_gdelt_date(art.get("seendate", "")) or ""
            domain = art.get("domain", "GDELT")

            if not art_url or not title:
                continue
            existing = conn.execute("SELECT id FROM articles WHERE url = ?", (art_url,)).fetchone()
            if existing:
                continue
            pending.append((art_url, title, seen_date, domain))

        if not pending:
            return 0

        # Phase 2: Parallel full-text scraping (cap at 30 articles)
        urls_to_scrape = [p[0] for p in pending[:30]]
        fetched_texts = fetch_full_texts_parallel(urls_to_scrape)

        # Phase 3: Insert articles
        count = 0
        for art_url, title, seen_date, domain in pending:
            full_text = fetched_texts.get(art_url, "") or title
            try:
                insert_article(conn, iso3, title[:500], f"GDELT/{domain}",
                               art_url[:1000], seen_date, full_text[:50000])
                count += 1
            except sqlite3.IntegrityError:
                pass

        conn.commit()
        scraped = sum(1 for u in urls_to_scrape if fetched_texts.get(u))
        logger.info("Ingested %d GDELT articles for %s (%d with full text).", count, iso3, scraped)
        return count
    finally:
        conn.close()


def ingest_gdelt_events(country_config: dict, days: int = 7) -> int:
    """
    Fetch conflict event data from GDELT DOC API using artlist mode.
    Classifies articles by event category and stores in both legacy
    gdelt_conflict_events table AND the unified conflict_events table.

    Returns number of event records ingested into conflict_events.
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

    # Delay to avoid 429 when running many countries sequentially
    time.sleep(30)

    data = _gdelt_fetch_with_backoff(url, iso3, "GDELT events")

    articles = data.get("articles", [])
    if not articles:
        logger.info("No GDELT event articles for %s.", iso3)
        return 0

    # Classify each article and aggregate by (date, category)
    from collections import defaultdict
    daily_legacy = defaultdict(lambda: {"count": 0, "tones": [], "urls": []})
    daily_by_category = defaultdict(lambda: {"count": 0, "tones": [], "urls": []})

    for art in articles:
        seen = _parse_gdelt_date(art.get("seendate", ""))
        if not seen:
            continue

        title = art.get("title", "")
        category = _classify_gdelt_article(title)
        tone = art.get("tone", 0)

        # Legacy aggregation (all categories lumped)
        daily_legacy[seen]["count"] += 1
        if tone:
            daily_legacy[seen]["tones"].append(float(tone))
        if art.get("url"):
            daily_legacy[seen]["urls"].append(art["url"])

        # Category-specific aggregation
        key = (seen, category)
        daily_by_category[key]["count"] += 1
        if tone:
            daily_by_category[key]["tones"].append(float(tone))
        if art.get("url"):
            daily_by_category[key]["urls"].append(art["url"])

    conn = get_connection()
    try:
        # Write to legacy gdelt_conflict_events (backward compat)
        for event_date, info in sorted(daily_legacy.items()):
            avg_tone = sum(info["tones"]) / len(info["tones"]) if info["tones"] else 0.0
            sample_url = info["urls"][0] if info["urls"] else ""
            try:
                conn.execute(
                    """INSERT INTO gdelt_conflict_events
                       (country_iso3, event_date, event_type, num_articles,
                        avg_tone, latitude, longitude, source_url)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(country_iso3, event_date, event_type, latitude, longitude)
                       DO UPDATE SET
                           num_articles = excluded.num_articles,
                           avg_tone = excluded.avg_tone,
                           source_url = excluded.source_url""",
                    (iso3, event_date, "conflict_coverage", info["count"],
                     avg_tone, None, None, sample_url[:1000]),
                )
            except sqlite3.IntegrityError:
                pass

        # Write to unified conflict_events (categorized)
        count = 0
        for (event_date, category), info in sorted(daily_by_category.items()):
            avg_tone = sum(info["tones"]) / len(info["tones"]) if info["tones"] else 0.0
            sample_url = info["urls"][0] if info["urls"] else ""
            try:
                insert_conflict_event(
                    conn, iso3, event_date, category, "gdelt",
                    event_type="gdelt_keyword_match",
                    num_articles=info["count"],
                    avg_tone=avg_tone,
                    source_url=sample_url[:1000],
                )
                count += 1
            except sqlite3.IntegrityError:
                pass

        conn.commit()
        logger.info("Ingested %d GDELT event records for %s (%d articles, %d categories).",
                    count, iso3, len(articles), len(daily_by_category))
        return count
    finally:
        conn.close()


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
    from_date = (datetime.now().astimezone() - timedelta(days=days)).strftime("%Y-%m-%d")

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
    except requests.exceptions.SSLError:
        logger.warning("NewsAPI SSL cert invalid -- skipping (external issue).")
        return 0
    except requests.exceptions.RequestException as e:
        logger.warning("NewsAPI fetch failed for %s: %s", country_name, e)
        return 0

    articles = data.get("articles", [])
    if not articles:
        logger.info("No NewsAPI articles for %s.", country_name)
        return 0

    conn = get_connection()
    try:
        # Phase 1: Collect new article metadata
        pending = []
        for art in articles:
            title = art.get("title", "")
            url = art.get("url", "")
            if not title or not url:
                continue
            existing = conn.execute("SELECT id FROM articles WHERE url = ?", (url,)).fetchone()
            if existing:
                continue
            pending.append({
                "title": title, "url": url,
                "published": art.get("publishedAt", ""),
                "content": art.get("content", "") or art.get("description", "") or "",
                "source_name": art.get("source", {}).get("name", "NewsAPI"),
            })

        if not pending:
            return 0

        # Phase 2: Parallel full-text scraping
        urls_to_scrape = [p["url"] for p in pending]
        fetched_texts = fetch_full_texts_parallel(urls_to_scrape)

        # Phase 3: Insert articles
        count = 0
        for p in pending:
            full_text = fetched_texts.get(p["url"], "") or p["content"]
            try:
                insert_article(conn, iso3, p["title"][:500], p["source_name"],
                               p["url"][:1000], p["published"][:100], full_text[:50000])
                count += 1
            except sqlite3.IntegrityError:
                pass

        conn.commit()
        logger.info("Ingested %d NewsAPI articles for %s.", count, iso3)
        return count
    finally:
        conn.close()


def ingest_all(country_config: dict, skip_gdelt: bool = False) -> dict:
    """Run all ingestion for a single country. Returns counts."""
    iso3 = country_config["iso3"]

    # NewsAPI first, fall back to RSS if NewsAPI returns < 5
    newsapi_count = ingest_newsapi(country_config)
    rss_count = 0
    if newsapi_count < 5:
        rss_count = ingest_rss(country_config)

    gdelt_art = 0
    gdelt_ev = 0
    if not skip_gdelt:
        gdelt_art = ingest_gdelt(country_config)
        gdelt_ev = ingest_gdelt_events(country_config)

    return {
        "acled": ingest_acled(iso3),
        "newsapi": newsapi_count,
        "rss": rss_count,
        "gdelt_articles": gdelt_art,
        "gdelt_events": gdelt_ev,
    }
