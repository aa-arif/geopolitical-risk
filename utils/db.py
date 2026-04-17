"""
Database schema and operations for the geopolitical risk system.
"""

import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "geopolitical.db"


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def initialize_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS acled_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_type TEXT,
            sub_event_type TEXT,
            fatalities INTEGER DEFAULT 0,
            latitude REAL,
            longitude REAL,
            source TEXT,
            notes TEXT,
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS gdelt_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_code TEXT,
            goldstein_scale REAL,
            num_mentions INTEGER,
            avg_tone REAL,
            source_url TEXT,
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            title TEXT,
            source TEXT,
            url TEXT,
            published_date TEXT,
            full_text TEXT,
            is_relevant BOOLEAN DEFAULT NULL,
            reliability_tier INTEGER DEFAULT NULL,
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS structural_vars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            variable_name TEXT NOT NULL,
            value REAL,
            source TEXT,
            as_of_date TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS reasoning_chains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            article_id INTEGER REFERENCES articles(id),
            chain_json TEXT NOT NULL,
            prompt_version TEXT,
            model_version TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            prediction_date TEXT NOT NULL,
            window_end_date TEXT NOT NULL,
            event_type TEXT DEFAULT 'composite',
            track_a_probability REAL,
            track_b_probability REAL,
            fused_probability REAL,
            extremized_probability REAL,
            calibrated_probability REAL,
            reasoning_summary TEXT,
            prompt_versions_json TEXT,
            model_versions_json TEXT,
            data_snapshot_hash TEXT,
            event_threshold REAL DEFAULT NULL,
            resolved BOOLEAN DEFAULT FALSE,
            actual_outcome INTEGER DEFAULT NULL,
            brier_score REAL DEFAULT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id INTEGER REFERENCES predictions(id),
            country_iso3 TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            probability REAL,
            reasoning_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS gdelt_conflict_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_type TEXT,
            num_articles INTEGER DEFAULT 1,
            avg_tone REAL,
            latitude REAL,
            longitude REAL,
            source_url TEXT,
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_gdelt_conflict_country_date
            ON gdelt_conflict_events(country_iso3, event_date);

        CREATE TABLE IF NOT EXISTS conflict_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_category TEXT NOT NULL,
            event_type TEXT,
            sub_event_type TEXT,
            source TEXT NOT NULL,
            fatalities INTEGER DEFAULT 0,
            severity TEXT,
            num_articles INTEGER DEFAULT 1,
            avg_tone REAL,
            latitude REAL,
            longitude REAL,
            source_url TEXT,
            source_article_id INTEGER,
            actors TEXT,
            confidence REAL,
            notes TEXT,
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_conflict_events_country_date
            ON conflict_events(country_iso3, event_date);
        CREATE INDEX IF NOT EXISTS idx_conflict_events_category
            ON conflict_events(country_iso3, event_category, event_date);

        CREATE TABLE IF NOT EXISTS change_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            alert_date TEXT NOT NULL,
            previous_probability REAL,
            current_probability REAL,
            delta REAL,
            alert_text TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            event_type TEXT NOT NULL,
            alert_date TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            previous_probability REAL,
            current_probability REAL,
            delta REAL,
            brief_text TEXT,
            reasoning_summary TEXT,
            sector_implications TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_alerts_country_date
            ON alerts(country_iso3, alert_date);

        CREATE TABLE IF NOT EXISTS briefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            brief_date TEXT NOT NULL,
            brief_type TEXT NOT NULL,
            headline TEXT,
            body_markdown TEXT,
            event_type_scores TEXT,
            key_drivers TEXT,
            sector_implications TEXT,
            source_prediction_ids TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_briefs_country_date
            ON briefs(country_iso3, brief_date);

        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            name TEXT,
            organization TEXT,
            countries TEXT,
            event_types TEXT,
            sectors TEXT,
            tier TEXT DEFAULT 'free',
            active BOOLEAN DEFAULT TRUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evaluation_date TEXT NOT NULL,
            country_iso3 TEXT,
            brier_aggregate REAL,
            brier_uncertainty REAL,
            brier_reliability REAL,
            brier_resolution REAL,
            track_a_brier REAL,
            track_b_brier REAL,
            fused_brier REAL,
            fusion_weight_used REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_acled_country_date
            ON acled_events(country_iso3, event_date);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_acled_unique
            ON acled_events(country_iso3, event_date, event_type, sub_event_type, latitude, longitude);
        CREATE INDEX IF NOT EXISTS idx_articles_country
            ON articles(country_iso3);
        CREATE INDEX IF NOT EXISTS idx_predictions_country
            ON predictions(country_iso3, prediction_date);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_url
            ON articles(url) WHERE url IS NOT NULL AND url != '';

        CREATE INDEX IF NOT EXISTS idx_agent_outputs_prediction
            ON agent_outputs(prediction_id);
        CREATE INDEX IF NOT EXISTS idx_change_alerts_date
            ON change_alerts(alert_date);
        CREATE INDEX IF NOT EXISTS idx_reasoning_chains_country_date
            ON reasoning_chains(country_iso3, created_at);
        CREATE INDEX IF NOT EXISTS idx_agent_outputs_type_country
            ON agent_outputs(agent_type, country_iso3);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_gdelt_conflict_unique
            ON gdelt_conflict_events(country_iso3, event_date, event_type, latitude, longitude);
    """)

    # --- Migration: add event_type column if missing (V2 schema) ---
    try:
        conn.execute("SELECT event_type FROM predictions LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE predictions ADD COLUMN event_type TEXT DEFAULT 'composite'")
        conn.commit()

    # --- Migration: ingest confidence flag (P0.2) ---
    try:
        conn.execute("SELECT ingest_confidence FROM predictions LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE predictions ADD COLUMN ingest_confidence TEXT DEFAULT NULL")
        conn.commit()

    try:
        conn.execute("SELECT ingest_deviation_sigma FROM predictions LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE predictions ADD COLUMN ingest_deviation_sigma REAL DEFAULT NULL")
        conn.commit()

    # Deduplicate predictions before creating unique index (keep latest id per group)
    # Groups by (country, date, event_type) to support V2 per-type predictions
    conn.execute("""
        DELETE FROM predictions WHERE id NOT IN (
            SELECT MAX(id) FROM predictions
            GROUP BY country_iso3, prediction_date, COALESCE(event_type, 'composite')
        )
    """)
    # Deduplicate gdelt_conflict_events before creating unique index
    conn.execute("""
        DELETE FROM gdelt_conflict_events WHERE id NOT IN (
            SELECT MAX(id) FROM gdelt_conflict_events
            GROUP BY country_iso3, event_date, event_type, latitude, longitude
        )
    """)
    # Deduplicate conflict_events before creating unique index
    conn.execute("""
        DELETE FROM conflict_events WHERE id NOT IN (
            SELECT MAX(id) FROM conflict_events
            GROUP BY country_iso3, event_date, event_category, source, latitude, longitude
        )
    """)

    # Migrate predictions unique index: drop old (country, date) and create
    # new (country, date, event_type) to support V2 per-type predictions.
    conn.executescript("""
        DROP INDEX IF EXISTS idx_predictions_unique;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_unique
            ON predictions(country_iso3, prediction_date, COALESCE(event_type, 'composite'));
        CREATE UNIQUE INDEX IF NOT EXISTS idx_conflict_events_unique
            ON conflict_events(country_iso3, event_date, event_category, source, latitude, longitude);
    """)
    conn.commit()
    conn.close()


def insert_article(conn, country_iso3: str, title: str, source: str,
                   url: str, published_date: str, full_text: str) -> int:
    """Insert an article and return its ID. Caller must commit."""
    cursor = conn.execute(
        """INSERT INTO articles (country_iso3, title, source, url,
           published_date, full_text)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (country_iso3, title, source, url, published_date, full_text)
    )
    return cursor.lastrowid


def insert_reasoning_chain(conn, country_iso3: str, article_id: int,
                           chain_json: str, prompt_version: str,
                           model_version: str) -> int:
    """Insert a reasoning chain and return its ID. Caller must commit."""
    cursor = conn.execute(
        """INSERT INTO reasoning_chains
           (country_iso3, article_id, chain_json, prompt_version, model_version)
           VALUES (?, ?, ?, ?, ?)""",
        (country_iso3, article_id, chain_json, prompt_version, model_version)
    )
    return cursor.lastrowid


def get_recent_articles(conn, country_iso3: str, days: int = 7) -> list:
    """Get articles from the last N days for a country."""
    cutoff = (datetime.now().astimezone() - timedelta(days=days)).strftime("%Y-%m-%d")
    cursor = conn.execute(
        """SELECT * FROM articles
           WHERE country_iso3 = ?
           AND pulled_at >= ?
           ORDER BY published_date DESC""",
        (country_iso3, cutoff)
    )
    return [dict(row) for row in cursor.fetchall()]


def get_recent_reasoning_chains(conn, country_iso3: str, days: int = 7) -> list:
    """Get reasoning chains from the last N days for a country."""
    cutoff = (datetime.now().astimezone() - timedelta(days=days)).strftime("%Y-%m-%d")
    cursor = conn.execute(
        """SELECT * FROM reasoning_chains
           WHERE country_iso3 = ?
           AND created_at >= ?
           ORDER BY created_at DESC""",
        (country_iso3, cutoff)
    )
    return [dict(row) for row in cursor.fetchall()]


def get_acled_summary(conn, country_iso3: str, days: int = 30) -> dict:
    """
    Get ACLED event summary for a country over the most recent N days
    of available data (not the last N calendar days from today).
    """
    # Find the latest event date for this country
    latest_row = conn.execute(
        "SELECT MAX(event_date) as d FROM acled_events WHERE country_iso3 = ?",
        (country_iso3,)
    ).fetchone()
    latest_date = latest_row["d"] if latest_row else None

    if not latest_date:
        return {"total_events": 0, "total_fatalities": 0, "by_type": [], "period_days": days}

    cursor = conn.execute(
        """SELECT event_type, COUNT(*) as count, SUM(fatalities) as total_fatalities
           FROM acled_events
           WHERE country_iso3 = ?
           AND event_date >= date(?, ? || ' days')
           GROUP BY event_type
           ORDER BY count DESC""",
        (country_iso3, latest_date, f"-{days}")
    )
    rows = [dict(r) for r in cursor.fetchall()]
    total_events = sum(r["count"] for r in rows)
    total_fatalities = sum(r["total_fatalities"] or 0 for r in rows)
    return {
        "total_events": total_events,
        "total_fatalities": total_fatalities,
        "by_type": rows,
        "period_days": days,
    }


def get_event_summary(conn, country_iso3: str, days: int = 30,
                      event_category: str = None) -> dict:
    """
    Get conflict event summary from the unified conflict_events table.

    Source-agnostic: queries all sources (GDELT, internal, etc.)
    over the most recent N days of available data.

    Args:
        event_category: Optional filter (ACE, MCU, REC, PSS, CID).
                        If None, returns all categories.
    """
    # Find the latest event date for this country
    latest_row = conn.execute(
        "SELECT MAX(event_date) as d FROM conflict_events WHERE country_iso3 = ?",
        (country_iso3,)
    ).fetchone()
    latest_date = latest_row["d"] if latest_row else None

    if not latest_date:
        return {"total_events": 0, "total_fatalities": 0,
                "by_category": [], "period_days": days}

    params = [country_iso3, latest_date, f"-{days}"]
    category_filter = ""
    if event_category:
        category_filter = "AND event_category = ?"
        params.append(event_category)

    cursor = conn.execute(
        f"""SELECT event_category, COUNT(*) as count,
                   SUM(fatalities) as total_fatalities
            FROM conflict_events
            WHERE country_iso3 = ?
            AND event_date >= date(?, ? || ' days')
            {category_filter}
            GROUP BY event_category
            ORDER BY count DESC""",
        params,
    )
    rows = [dict(r) for r in cursor.fetchall()]
    total_events = sum(r["count"] for r in rows)
    total_fatalities = sum(r["total_fatalities"] or 0 for r in rows)
    return {
        "total_events": total_events,
        "total_fatalities": total_fatalities,
        "by_category": rows,
        "period_days": days,
    }


def get_event_count(conn, country_iso3: str, start_date: str,
                    end_date: str, event_category: str = None,
                    require_fatalities: bool = False) -> int:
    """
    Count conflict events in a date range. Used by resolution.

    Args:
        event_category: Filter by ACE/MCU/REC/PSS/CID.
        require_fatalities: If True, only count events with fatalities > 0.
    """
    params = [country_iso3, start_date, end_date]
    filters = ["country_iso3 = ?", "event_date >= ?", "event_date <= ?"]

    if event_category:
        filters.append("event_category = ?")
        params.append(event_category)
    if require_fatalities:
        filters.append("fatalities > 0")

    where = " AND ".join(filters)
    cursor = conn.execute(
        f"SELECT COUNT(*) as cnt FROM conflict_events WHERE {where}",
        params,
    )
    return cursor.fetchone()["cnt"]


def compute_event_threshold_by_category(conn, country_iso3: str,
                                         event_category: str,
                                         require_fatalities: bool = False,
                                         months: int = 12,
                                         before_date: str = None) -> float:
    """
    Compute 90th percentile of monthly event counts for a specific
    event category from the unified conflict_events table.

    Source-agnostic replacement for compute_event_threshold().
    """
    if before_date:
        end_date = before_date
    else:
        end_date = datetime.now().astimezone().strftime("%Y-%m-%d")

    cutoff = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    params = [country_iso3, cutoff, end_date, event_category]
    fatality_filter = "AND fatalities > 0" if require_fatalities else ""

    cursor = conn.execute(
        f"""SELECT strftime('%Y-%m', event_date) as month, COUNT(*) as cnt
            FROM conflict_events
            WHERE country_iso3 = ?
            AND event_date >= ? AND event_date < ?
            AND event_category = ?
            {fatality_filter}
            GROUP BY month
            ORDER BY month""",
        params,
    )
    rows = cursor.fetchall()
    if not rows:
        return 0.0

    import numpy as np
    counts = [r["cnt"] for r in rows]
    return float(np.percentile(counts, 90))


def insert_conflict_event(conn, country_iso3: str, event_date: str,
                           event_category: str, source: str,
                           event_type: str = None, sub_event_type: str = None,
                           fatalities: int = 0, severity: str = None,
                           num_articles: int = 1, avg_tone: float = None,
                           latitude: float = None, longitude: float = None,
                           source_url: str = None, source_article_id: int = None,
                           actors: str = None, confidence: float = None,
                           notes: str = None) -> int:
    """Insert a conflict event into the unified table. Caller must commit."""
    cursor = conn.execute(
        """INSERT INTO conflict_events
           (country_iso3, event_date, event_category, event_type, sub_event_type,
            source, fatalities, severity, num_articles, avg_tone,
            latitude, longitude, source_url, source_article_id,
            actors, confidence, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(country_iso3, event_date, event_category, source,
                       latitude, longitude)
           DO UPDATE SET
               fatalities = MAX(conflict_events.fatalities, excluded.fatalities),
               num_articles = excluded.num_articles,
               avg_tone = excluded.avg_tone,
               notes = excluded.notes""",
        (country_iso3, event_date, event_category, event_type, sub_event_type,
         source, fatalities, severity, num_articles, avg_tone,
         latitude, longitude, source_url, source_article_id,
         actors, confidence, notes),
    )
    return cursor.lastrowid


def get_prediction_history(conn, country_iso3: str, limit: int = 90) -> list:
    """Get prediction history for a country."""
    cursor = conn.execute(
        """SELECT * FROM predictions
           WHERE country_iso3 = ?
           ORDER BY prediction_date DESC
           LIMIT ?""",
        (country_iso3, limit)
    )
    return [dict(row) for row in cursor.fetchall()]


def get_resolved_predictions(conn, country_iso3: str = None) -> list:
    """Get all resolved predictions for evaluation."""
    if country_iso3:
        cursor = conn.execute(
            """SELECT * FROM predictions
               WHERE resolved = TRUE AND country_iso3 = ?
               ORDER BY prediction_date""",
            (country_iso3,)
        )
    else:
        cursor = conn.execute(
            """SELECT * FROM predictions
               WHERE resolved = TRUE
               ORDER BY prediction_date"""
        )
    return [dict(row) for row in cursor.fetchall()]


def get_resolved_predictions_by_confidence(conn, confidence_tier: str) -> list:
    """
    Get resolved predictions filtered by ingest_confidence tier.

    Args:
        confidence_tier: One of 'high', 'low', 'unknown'. The 'unknown'
                         bucket also matches NULL values (predictions resolved
                         before the confidence flag was introduced).
    """
    if confidence_tier == "unknown":
        cursor = conn.execute(
            """SELECT * FROM predictions
               WHERE resolved = TRUE
               AND (ingest_confidence = 'unknown' OR ingest_confidence IS NULL)
               ORDER BY prediction_date"""
        )
    else:
        cursor = conn.execute(
            """SELECT * FROM predictions
               WHERE resolved = TRUE AND ingest_confidence = ?
               ORDER BY prediction_date""",
            (confidence_tier,),
        )
    return [dict(row) for row in cursor.fetchall()]


def compute_event_threshold(conn, country_iso3: str, months: int = 12,
                             before_date: str = None) -> float:
    """
    Compute the 90th percentile of monthly violent ACLED event counts.

    Only counts events that constitute political violence:
    Battles, Explosions/Remote violence, Violence against civilians
    -- with at least 1 fatality. This decouples the resolution criterion
    from the broader ACLED data used as model input (which includes
    protests, strategic developments, etc.).

    Args:
        before_date: If set, only use data before this date (prevents
                     look-ahead contamination when computing at prediction time).
    """
    if before_date:
        end_date = before_date
    else:
        end_date = datetime.now().astimezone().strftime("%Y-%m-%d")

    cutoff = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    cursor = conn.execute(
        """SELECT strftime('%Y-%m', event_date) as month, COUNT(*) as cnt
           FROM acled_events
           WHERE country_iso3 = ?
           AND event_date >= ? AND event_date < ?
           AND event_type IN ('Battles', 'Explosions/Remote violence',
                              'Violence against civilians')
           AND fatalities > 0
           GROUP BY month
           ORDER BY month""",
        (country_iso3, cutoff, end_date)
    )
    rows = cursor.fetchall()
    if not rows:
        return 0.0

    import numpy as np
    counts = [r["cnt"] for r in rows]
    return float(np.percentile(counts, 90))


def get_latest_event_date(conn, country_iso3: str) -> str:
    """Get the most recent ACLED event date for a country."""
    row = conn.execute(
        "SELECT MAX(event_date) as d FROM acled_events WHERE country_iso3 = ?",
        (country_iso3,)
    ).fetchone()
    return row["d"] or "" if row else ""


def get_latest_article_id(conn, country_iso3: str) -> int:
    """Get the most recent article ID for a country."""
    row = conn.execute(
        "SELECT MAX(id) as mid FROM articles WHERE country_iso3 = ?",
        (country_iso3,)
    ).fetchone()
    return row["mid"] or 0 if row else 0


def insert_agent_outputs(conn, prediction_id: int, country_iso3: str,
                         ensemble_results: list):
    """Store individual agent outputs for a prediction."""
    import json
    for result in ensemble_results:
        agent_type = result.get("agent_type", "unknown")
        probability = result.get("final_probability", 0.0)
        # Store full result minus _meta for reasoning
        reasoning = {k: v for k, v in result.items()
                     if k not in ("_meta", "agent_type", "final_probability")}
        conn.execute(
            """INSERT INTO agent_outputs
               (prediction_id, country_iso3, agent_type, probability, reasoning_json)
               VALUES (?, ?, ?, ?, ?)""",
            (prediction_id, country_iso3, agent_type, probability,
             json.dumps(reasoning, default=str)),
        )
    conn.commit()


def get_agent_outputs(conn, prediction_id: int) -> list:
    """Get agent outputs for a specific prediction."""
    cursor = conn.execute(
        """SELECT agent_type, probability, reasoning_json
           FROM agent_outputs WHERE prediction_id = ?
           ORDER BY id""",
        (prediction_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_latest_predictions_all(conn, event_type: str = "composite") -> list:
    """
    Get the most recent prediction for every country, in one query.
    Replaces the N+1 loop in list_countries().
    """
    cursor = conn.execute(
        """SELECT * FROM predictions
           WHERE id IN (
               SELECT MAX(id) FROM predictions
               WHERE event_type = ?
               GROUP BY country_iso3
           )
           ORDER BY calibrated_probability DESC""",
        (event_type,),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_previous_predictions_all(conn, event_type: str = "composite") -> list:
    """
    Get the second-most-recent prediction for every country, in one query.
    Used to compute deltas for the dashboard trend arrows.
    """
    cursor = conn.execute(
        """WITH ranked AS (
               SELECT *, ROW_NUMBER() OVER (
                   PARTITION BY country_iso3
                   ORDER BY prediction_date DESC, id DESC
               ) as rn
               FROM predictions
               WHERE event_type = ?
           )
           SELECT * FROM ranked WHERE rn = 2""",
        (event_type,),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_event_type_predictions(conn, country_iso3: str,
                                 prediction_date: str = None) -> list:
    """
    Get all event-type predictions for a country on a given date.
    If no date, uses the most recent prediction date.
    """
    if not prediction_date:
        row = conn.execute(
            """SELECT MAX(prediction_date) as d FROM predictions
               WHERE country_iso3 = ? AND event_type = 'composite'""",
            (country_iso3,),
        ).fetchone()
        prediction_date = row["d"] if row and row["d"] else None
        if not prediction_date:
            return []

    cursor = conn.execute(
        """SELECT event_type, calibrated_probability, track_a_probability,
                  track_b_probability, fused_probability, reasoning_summary
           FROM predictions
           WHERE country_iso3 = ? AND prediction_date = ?
           AND event_type != 'composite'
           ORDER BY calibrated_probability DESC""",
        (country_iso3, prediction_date),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_latest_brief(conn, country_iso3: str) -> dict:
    """Get the most recent daily brief for a country."""
    cursor = conn.execute(
        """SELECT * FROM briefs
           WHERE country_iso3 = ?
           ORDER BY brief_date DESC
           LIMIT 1""",
        (country_iso3,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def get_latest_briefs_all(conn, brief_type: str = "daily_summary") -> list:
    """Get today's briefs for all countries, one query."""
    cursor = conn.execute(
        """SELECT * FROM briefs
           WHERE id IN (
               SELECT MAX(id) FROM briefs
               WHERE brief_type = ?
               GROUP BY country_iso3
           )
           ORDER BY brief_date DESC""",
        (brief_type,),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_recent_alerts_v2(conn, country_iso3: str = None,
                          days: int = 30) -> list:
    """Get alerts from the V2 alerts table (per-event-type)."""
    cutoff = (datetime.now().astimezone() - timedelta(days=days)).strftime("%Y-%m-%d")
    if country_iso3:
        cursor = conn.execute(
            """SELECT * FROM alerts
               WHERE country_iso3 = ? AND alert_date >= ?
               ORDER BY alert_date DESC, ABS(delta) DESC""",
            (country_iso3, cutoff),
        )
    else:
        cursor = conn.execute(
            """SELECT * FROM alerts
               WHERE alert_date >= ?
               ORDER BY alert_date DESC, ABS(delta) DESC""",
            (cutoff,),
        )
    return [dict(row) for row in cursor.fetchall()]


# --- Subscriber management ---

def insert_subscriber(conn, email: str, name: str = None,
                       organization: str = None,
                       countries: list = None, event_types: list = None,
                       sectors: list = None, tier: str = "free") -> int:
    """Insert a subscriber. Returns subscriber ID."""
    cursor = conn.execute(
        """INSERT INTO subscribers
           (email, name, organization, countries, event_types, sectors, tier)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (email, name, organization,
         json.dumps(countries or ["all"]),
         json.dumps(event_types or ["all"]),
         json.dumps(sectors or ["all"]),
         tier),
    )
    conn.commit()
    return cursor.lastrowid


def get_active_subscribers(conn) -> list:
    """Get all active subscribers."""
    cursor = conn.execute(
        "SELECT * FROM subscribers WHERE active = TRUE ORDER BY created_at",
    )
    rows = []
    for r in cursor.fetchall():
        row = dict(r)
        for field in ("countries", "event_types", "sectors"):
            try:
                row[field] = json.loads(row[field]) if row[field] else ["all"]
            except (json.JSONDecodeError, TypeError):
                row[field] = ["all"]
        rows.append(row)
    return rows


def get_matched_subscribers(conn, country_iso3: str,
                              event_type: str) -> list:
    """
    Get subscribers whose preferences match a specific alert.

    A subscriber matches if:
    - Their countries list contains the ISO3 code or "all"
    - Their event_types list contains the event type or "all"
    - They are active
    """
    subscribers = get_active_subscribers(conn)
    matched = []
    for sub in subscribers:
        country_match = "all" in sub["countries"] or country_iso3 in sub["countries"]
        et_match = "all" in sub["event_types"] or event_type in sub["event_types"]
        if country_match and et_match:
            matched.append(sub)
    return matched


def delete_subscriber(conn, subscriber_id: int) -> bool:
    """Deactivate a subscriber. Returns True if found."""
    cursor = conn.execute(
        "UPDATE subscribers SET active = FALSE WHERE id = ?",
        (subscriber_id,),
    )
    conn.commit()
    return cursor.rowcount > 0
