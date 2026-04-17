"""
Tests for compute_ingest_deviation (P0.2).

Verifies the ingest-volume confidence flag correctly classifies resolution
windows as high / low / unknown confidence based on baseline comparison.
"""
import sys
sys.path.insert(0, ".")

import sqlite3
import pytest
from datetime import datetime, timedelta

from evaluation.resolve import compute_ingest_deviation


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
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
            pulled_at TEXT
        );
    """)
    return conn


def _seed_ingest(conn, iso3, event_category, source, start_dt, n_days,
                 events_per_day):
    """Insert events with pulled_at spread over n_days starting at start_dt."""
    for day_offset in range(n_days):
        pulled_at = (start_dt + timedelta(days=day_offset)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        event_date = (start_dt + timedelta(days=day_offset)).strftime("%Y-%m-%d")
        for i in range(events_per_day):
            conn.execute(
                """INSERT INTO conflict_events
                   (country_iso3, event_date, event_category, source,
                    latitude, pulled_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (iso3, event_date, event_category, source,
                 float(day_offset * 1000 + i), pulled_at),
            )
    conn.commit()


def test_stable_baseline_and_window_high_confidence():
    """Baseline ~5/day and window ~5/day -> sigma near 0, 'high'."""
    conn = _make_db()
    pred_date = "2026-04-01"
    window_end = "2026-05-01"
    pred_dt = datetime.strptime(pred_date, "%Y-%m-%d")
    window_dt = datetime.strptime(window_end, "%Y-%m-%d")

    # Baseline: 90 days with slight variation (4 or 5 events)
    baseline_start = pred_dt - timedelta(days=90)
    for d in range(90):
        events_today = 5 if d % 2 == 0 else 4
        _seed_ingest(conn, "NGA", "ACE", "gdelt",
                     baseline_start + timedelta(days=d), 1, events_today)

    # Window: 31 days with similar variation
    window_span = (window_dt - pred_dt).days + 1
    for d in range(window_span):
        events_today = 5 if d % 2 == 0 else 4
        _seed_ingest(conn, "NGA", "ACE", "gdelt",
                     pred_dt + timedelta(days=d), 1, events_today)

    sigma, flag = compute_ingest_deviation(
        conn, "NGA", pred_date, window_end, "ACE"
    )
    assert flag == "high"
    assert sigma is not None
    assert abs(sigma) < 2.0


def test_ingest_outage_low_confidence():
    """Baseline ~10/day, window ~1/day -> large negative sigma, 'low'."""
    conn = _make_db()
    pred_date = "2026-04-01"
    window_end = "2026-05-01"
    pred_dt = datetime.strptime(pred_date, "%Y-%m-%d")
    window_dt = datetime.strptime(window_end, "%Y-%m-%d")

    baseline_start = pred_dt - timedelta(days=90)
    for d in range(90):
        events_today = 10 if d % 2 == 0 else 11  # small variation -> nonzero std
        _seed_ingest(conn, "NGA", "ACE", "gdelt",
                     baseline_start + timedelta(days=d), 1, events_today)

    # Window: ingest outage, only ~1 event/day
    window_span = (window_dt - pred_dt).days + 1
    for d in range(window_span):
        _seed_ingest(conn, "NGA", "ACE", "gdelt",
                     pred_dt + timedelta(days=d), 1, 1)

    sigma, flag = compute_ingest_deviation(
        conn, "NGA", pred_date, window_end, "ACE"
    )
    assert flag == "low"
    assert sigma is not None
    assert sigma < -2.0


def test_sparse_baseline_unknown_confidence():
    """Baseline with only 30 non-zero days -> 'unknown'."""
    conn = _make_db()
    pred_date = "2026-04-01"
    window_end = "2026-05-01"
    pred_dt = datetime.strptime(pred_date, "%Y-%m-%d")

    # Only 30 days with ingest in the 90-day baseline
    baseline_start = pred_dt - timedelta(days=90)
    for d in range(30):
        _seed_ingest(conn, "NGA", "ACE", "gdelt",
                     baseline_start + timedelta(days=d), 1, 5)

    sigma, flag = compute_ingest_deviation(
        conn, "NGA", pred_date, window_end, "ACE"
    )
    assert flag == "unknown"
    assert sigma is None


def test_zero_variance_baseline_unknown_confidence():
    """Baseline with all days at exactly 5 events -> 'unknown' (std=0)."""
    conn = _make_db()
    pred_date = "2026-04-01"
    window_end = "2026-05-01"
    pred_dt = datetime.strptime(pred_date, "%Y-%m-%d")

    baseline_start = pred_dt - timedelta(days=90)
    for d in range(90):
        _seed_ingest(conn, "NGA", "ACE", "gdelt",
                     baseline_start + timedelta(days=d), 1, 5)

    sigma, flag = compute_ingest_deviation(
        conn, "NGA", pred_date, window_end, "ACE"
    )
    assert flag == "unknown"
    assert sigma is None
