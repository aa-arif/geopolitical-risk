"""
Tests for the fatality cap protection added after the 2026-04-23 dashboard
audit (EGY 152,700 / SDN 40,082 cumulative-total mis-codes).

Exercises the DB-level hard backstop in utils.db.insert_conflict_event:
the primary cap in pipeline/classify.py is validated indirectly -- the
DB layer is the last line of defense and the one tested here.
"""

import sys
sys.path.insert(0, ".")

import sqlite3
import pytest

from config.settings import MAX_SINGLE_EVENT_FATALITIES
from utils.db import insert_conflict_event


CONFLICT_EVENTS_SCHEMA = """
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
    pulled_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(country_iso3, event_date, event_category, source, latitude, longitude)
);
"""


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(CONFLICT_EVENTS_SCHEMA)
    yield conn
    conn.close()


def _fetch(conn, event_date):
    row = conn.execute(
        "SELECT fatalities, notes FROM conflict_events WHERE event_date = ?",
        (event_date,),
    ).fetchone()
    return dict(row) if row else None


def test_below_cap_passes_through_unchanged(db):
    """Fatalities well below the cap are preserved; no DB_HARDCAP marker."""
    insert_conflict_event(
        db, "NGA", "2026-04-01", "ACE", "internal",
        fatalities=50, notes="Borno clash",
    )
    db.commit()
    row = _fetch(db, "2026-04-01")
    assert row["fatalities"] == 50
    assert "DB_HARDCAP" not in (row["notes"] or "")


def test_at_cap_boundary_passes_through_unchanged(db):
    """Fatalities exactly equal to the cap must NOT be zeroed."""
    insert_conflict_event(
        db, "YEM", "2026-04-02", "ACE", "internal",
        fatalities=MAX_SINGLE_EVENT_FATALITIES, notes="Large battle",
    )
    db.commit()
    row = _fetch(db, "2026-04-02")
    assert row["fatalities"] == MAX_SINGLE_EVENT_FATALITIES
    assert "DB_HARDCAP" not in (row["notes"] or "")


def test_above_cap_is_zeroed_with_hardcap_marker(db):
    """Fatalities above the cap (e.g. 150000) are zeroed; notes keep raw value."""
    insert_conflict_event(
        db, "EGY", "2026-04-03", "ACE", "internal",
        fatalities=150000, notes="Gaza cumulative totals",
    )
    db.commit()
    row = _fetch(db, "2026-04-03")
    assert row["fatalities"] == 0
    assert "DB_HARDCAP orig=150000" in row["notes"]
    assert "Gaza cumulative totals" in row["notes"]


def test_sudan_40k_regression(db):
    """
    Regression test for the real Sudan scenario from 2026-04-23: the LLM
    extractor read a cumulative civil-war total (40,082) and coded it as a
    single-event fatality. That row was manually zeroed in production.
    """
    insert_conflict_event(
        db, "SDN", "2026-04-22", "ACE", "internal",
        fatalities=40082,
        notes="Sudan civil war cumulative death toll since April 2023",
    )
    db.commit()
    row = _fetch(db, "2026-04-22")
    assert row["fatalities"] == 0
    assert "DB_HARDCAP orig=40082" in row["notes"]


def test_zero_and_none_fatalities_are_preserved(db):
    """Legitimate zero/None fatalities must not trigger the backstop."""
    insert_conflict_event(
        db, "KEN", "2026-04-04", "MCU", "internal",
        fatalities=0, notes="Peaceful protest",
    )
    insert_conflict_event(
        db, "COL", "2026-04-05", "PSS", "internal",
        fatalities=None, notes="Sanctions announcement",
    )
    db.commit()

    row_zero = _fetch(db, "2026-04-04")
    assert row_zero["fatalities"] == 0
    assert "DB_HARDCAP" not in (row_zero["notes"] or "")

    row_none = _fetch(db, "2026-04-05")
    # SQLite stores None as 0 given the DEFAULT 0 column? No -- explicit NULL
    # binds to NULL. The backstop only triggers on truthy values, so None is
    # passed through untouched.
    assert row_none["fatalities"] is None or row_none["fatalities"] == 0
    assert "DB_HARDCAP" not in (row_none["notes"] or "")
