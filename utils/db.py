"""
Database schema and operations for the geopolitical risk system.
"""

import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "geopolitical.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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
            track_a_probability REAL,
            track_b_probability REAL,
            fused_probability REAL,
            extremized_probability REAL,
            calibrated_probability REAL,
            reasoning_summary TEXT,
            prompt_versions_json TEXT,
            model_versions_json TEXT,
            data_snapshot_hash TEXT,
            resolved BOOLEAN DEFAULT FALSE,
            actual_outcome INTEGER DEFAULT NULL,
            brier_score REAL DEFAULT NULL,
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
        CREATE INDEX IF NOT EXISTS idx_articles_country
            ON articles(country_iso3);
        CREATE INDEX IF NOT EXISTS idx_predictions_country
            ON predictions(country_iso3, prediction_date);
    """)
    conn.commit()
    conn.close()


def insert_article(conn, country_iso3: str, title: str, source: str,
                   url: str, published_date: str, full_text: str) -> int:
    """Insert an article and return its ID."""
    cursor = conn.execute(
        """INSERT INTO articles (country_iso3, title, source, url,
           published_date, full_text)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (country_iso3, title, source, url, published_date, full_text)
    )
    conn.commit()
    return cursor.lastrowid


def insert_reasoning_chain(conn, country_iso3: str, article_id: int,
                           chain_json: str, prompt_version: str,
                           model_version: str) -> int:
    """Insert a reasoning chain and return its ID."""
    cursor = conn.execute(
        """INSERT INTO reasoning_chains
           (country_iso3, article_id, chain_json, prompt_version, model_version)
           VALUES (?, ?, ?, ?, ?)""",
        (country_iso3, article_id, chain_json, prompt_version, model_version)
    )
    conn.commit()
    return cursor.lastrowid


def get_recent_articles(conn, country_iso3: str, days: int = 7) -> list:
    """Get articles from the last N days for a country."""
    cursor = conn.execute(
        """SELECT * FROM articles
           WHERE country_iso3 = ?
           AND pulled_at >= date('now', ? || ' days')
           ORDER BY published_date DESC""",
        (country_iso3, f"-{days}")
    )
    return [dict(row) for row in cursor.fetchall()]


def get_recent_reasoning_chains(conn, country_iso3: str, days: int = 7) -> list:
    """Get reasoning chains from the last N days for a country."""
    cursor = conn.execute(
        """SELECT * FROM reasoning_chains
           WHERE country_iso3 = ?
           AND created_at >= date('now', ? || ' days')
           ORDER BY created_at DESC""",
        (country_iso3, f"-{days}")
    )
    return [dict(row) for row in cursor.fetchall()]


def get_acled_summary(conn, country_iso3: str, days: int = 30) -> dict:
    """Get ACLED event summary for a country over the last N days."""
    cursor = conn.execute(
        """SELECT event_type, COUNT(*) as count, SUM(fatalities) as total_fatalities
           FROM acled_events
           WHERE country_iso3 = ?
           AND event_date >= date('now', ? || ' days')
           GROUP BY event_type
           ORDER BY count DESC""",
        (country_iso3, f"-{days}")
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
