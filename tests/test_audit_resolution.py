"""
Smoke test for scripts/audit_resolution_readiness.py.

Just verifies the orchestrator runs end-to-end without exception. Unit
tests for the underlying machinery live in test_ingest_deviation.py and
test_resolution.py.
"""
import sys
sys.path.insert(0, ".")

import sqlite3
from datetime import datetime

from scripts.audit_resolution_readiness import run_audit


def _make_db_with_fixtures():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE predictions (
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
            ingest_confidence TEXT DEFAULT NULL,
            ingest_deviation_sigma REAL DEFAULT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE conflict_events (
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
        CREATE TABLE acled_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT, event_date TEXT, event_type TEXT,
            sub_event_type TEXT, fatalities INTEGER DEFAULT 0,
            latitude REAL, longitude REAL, source TEXT, notes TEXT,
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE gdelt_conflict_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT, event_date TEXT, event_type TEXT,
            num_articles INTEGER DEFAULT 1, avg_tone REAL,
            latitude REAL, longitude REAL, source_url TEXT,
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)

    fixtures = [
        ("NGA", "2026-04-01", "2026-05-01", "composite", 0.45, 8.0),
        ("NGA", "2026-04-01", "2026-05-01", "ACE", 0.30, 5.0),
        ("BGD", "2026-04-05", "2026-05-05", "MCU", 0.20, 10.0),
        ("PAK", "2026-04-10", "2026-05-10", "REC", 0.10, None),
        ("TUR", "2026-04-12", "2026-05-12", "PSS", 0.25, None),
    ]
    for iso3, pd, wed, et, prob, thr in fixtures:
        conn.execute(
            """INSERT INTO predictions
               (country_iso3, prediction_date, window_end_date, event_type,
                calibrated_probability, event_threshold)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (iso3, pd, wed, et, prob, thr),
        )

    for i in range(3):
        conn.execute(
            """INSERT INTO conflict_events
               (country_iso3, event_date, event_category, source,
                fatalities, latitude, pulled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("NGA", "2026-04-05", "ACE", "gdelt", 1, float(i),
             "2026-04-05 12:00:00"),
        )

    conn.commit()
    return conn


def test_run_audit_completes_without_exception():
    conn = _make_db_with_fixtures()
    today = datetime(2026, 4, 17)
    run_audit(conn, today, verbose=True)
