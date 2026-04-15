"""
Tests for change detection (alerts) and brief generation pipeline steps.
"""
import sys
sys.path.insert(0, ".")

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from pipeline.alerts import (
    detect_changes_for_country, store_alerts,
    _format_trigger_text, DELTA_THRESHOLD, LEVEL_BOUNDARIES,
)
from pipeline.daily_run import step_detect_alerts, step_brief
from utils.logger import log_prediction


@pytest.fixture
def db():
    """In-memory SQLite with predictions + alerts + briefs tables."""
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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX idx_predictions_unique
            ON predictions(country_iso3, prediction_date,
                           COALESCE(event_type, 'composite'));
        CREATE TABLE alerts (
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
        CREATE TABLE briefs (
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
        CREATE TABLE change_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            alert_date TEXT NOT NULL,
            previous_probability REAL,
            current_probability REAL,
            delta REAL,
            alert_text TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE conflict_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_category TEXT NOT NULL,
            event_type TEXT, sub_event_type TEXT,
            source TEXT NOT NULL,
            fatalities INTEGER DEFAULT 0,
            severity TEXT, num_articles INTEGER DEFAULT 1,
            avg_tone REAL, latitude REAL, longitude REAL,
            source_url TEXT, source_article_id INTEGER,
            actors TEXT, confidence REAL, notes TEXT,
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX idx_conflict_events_unique
            ON conflict_events(country_iso3, event_date, event_category,
                               source, latitude, longitude);
    """)
    return conn


def _seed_prediction(conn, iso3, date, event_type, prob):
    """Seed a prediction row."""
    log_prediction(conn, {
        "country_iso3": iso3,
        "prediction_date": date,
        "window_end_date": "2026-06-01",
        "event_type": event_type,
        "track_a": prob,
        "track_b": prob,
        "fused": prob,
        "extremized": prob,
        "calibrated": prob,
        "reasoning": {},
    })


# --- Change Detection Tests ---

class TestDetectChanges:
    def test_no_previous_returns_empty(self, db):
        _seed_prediction(db, "NGA", "2026-04-14", "composite", 0.30)
        triggers = detect_changes_for_country(db, "NGA")
        assert triggers == []

    def test_delta_spike_triggers_alert(self, db):
        _seed_prediction(db, "NGA", "2026-04-13", "ACE", 0.20)
        _seed_prediction(db, "NGA", "2026-04-14", "ACE", 0.35)  # +15pp

        triggers = detect_changes_for_country(db, "NGA")
        spike_triggers = [t for t in triggers if t["trigger_type"] == "delta_spike"]
        assert len(spike_triggers) >= 1
        assert spike_triggers[0]["event_type"] == "ACE"
        assert spike_triggers[0]["delta"] > DELTA_THRESHOLD

    def test_small_delta_no_trigger(self, db):
        _seed_prediction(db, "NGA", "2026-04-13", "ACE", 0.20)
        _seed_prediction(db, "NGA", "2026-04-14", "ACE", 0.25)  # +5pp < 10pp

        triggers = detect_changes_for_country(db, "NGA")
        spike_triggers = [t for t in triggers if t["trigger_type"] == "delta_spike"]
        assert len(spike_triggers) == 0

    def test_threshold_cross_triggers_alert(self, db):
        _seed_prediction(db, "NGA", "2026-04-13", "composite", 0.18)
        _seed_prediction(db, "NGA", "2026-04-14", "composite", 0.25)  # crosses 0.20

        triggers = detect_changes_for_country(db, "NGA")
        cross_triggers = [t for t in triggers if t["trigger_type"] == "threshold_cross"]
        assert len(cross_triggers) >= 1
        assert cross_triggers[0]["boundary"] == 0.20
        assert cross_triggers[0]["direction"] == "up"

    def test_threshold_cross_downward(self, db):
        _seed_prediction(db, "NGA", "2026-04-13", "MCU", 0.45)
        _seed_prediction(db, "NGA", "2026-04-14", "MCU", 0.35)  # crosses 0.40 down

        triggers = detect_changes_for_country(db, "NGA")
        cross_triggers = [t for t in triggers
                          if t["trigger_type"] == "threshold_cross"
                          and t["event_type"] == "MCU"]
        assert len(cross_triggers) >= 1
        assert cross_triggers[0]["direction"] == "down"

    def test_dominant_shift_triggers_alert(self, db):
        # Yesterday: ACE dominant
        _seed_prediction(db, "NGA", "2026-04-13", "ACE", 0.40)
        _seed_prediction(db, "NGA", "2026-04-13", "MCU", 0.20)
        _seed_prediction(db, "NGA", "2026-04-13", "REC", 0.10)
        # Today: MCU dominant
        _seed_prediction(db, "NGA", "2026-04-14", "ACE", 0.15)
        _seed_prediction(db, "NGA", "2026-04-14", "MCU", 0.50)
        _seed_prediction(db, "NGA", "2026-04-14", "REC", 0.10)

        triggers = detect_changes_for_country(db, "NGA")
        shift_triggers = [t for t in triggers if t["trigger_type"] == "dominant_shift"]
        assert len(shift_triggers) == 1
        assert shift_triggers[0]["new_dominant"] == "MCU"
        assert shift_triggers[0]["previous_dominant"] == "ACE"

    def test_same_day_predictions_no_trigger(self, db):
        """Two predictions on the same day should not trigger change detection."""
        _seed_prediction(db, "NGA", "2026-04-14", "ACE", 0.20)
        # Can't seed same day+type due to unique index, but if we only have one day:
        triggers = detect_changes_for_country(db, "NGA")
        assert triggers == []


class TestStoreAlerts:
    def test_stores_trigger_in_alerts_table(self, db):
        triggers = [{
            "country_iso3": "NGA",
            "event_type": "ACE",
            "trigger_type": "delta_spike",
            "previous_probability": 0.20,
            "current_probability": 0.35,
            "delta": 0.15,
            "direction": "up",
        }]
        count = store_alerts(db, triggers)
        assert count == 1

        rows = db.execute("SELECT * FROM alerts").fetchall()
        assert len(rows) == 1
        assert rows[0]["event_type"] == "ACE"
        assert rows[0]["trigger_type"] == "delta_spike"


class TestFormatTriggerText:
    def test_delta_spike_format(self):
        text = _format_trigger_text({
            "country_iso3": "IRN",
            "event_type": "ACE",
            "trigger_type": "delta_spike",
            "previous_probability": 0.50,
            "current_probability": 0.65,
            "delta": 0.15,
            "direction": "up",
        })
        assert "IRN" in text
        assert "rose" in text

    def test_threshold_cross_format(self):
        text = _format_trigger_text({
            "country_iso3": "IRQ",
            "event_type": "composite",
            "trigger_type": "threshold_cross",
            "previous_probability": 0.35,
            "current_probability": 0.45,
            "delta": 0.10,
            "direction": "up",
            "boundary": 0.40,
        })
        assert "HIGH" in text

    def test_dominant_shift_format(self):
        text = _format_trigger_text({
            "country_iso3": "SDN",
            "event_type": "MCU",
            "trigger_type": "dominant_shift",
            "previous_probability": 0.20,
            "current_probability": 0.45,
            "delta": 0.25,
            "direction": "shift",
            "previous_dominant": "ACE",
            "new_dominant": "MCU",
        })
        assert "shifted" in text


# --- Pipeline Step Tests ---

class TestStepDetectAlerts:
    def test_step_adds_triggers_to_context(self, db):
        _seed_prediction(db, "NGA", "2026-04-13", "ACE", 0.20)
        _seed_prediction(db, "NGA", "2026-04-14", "ACE", 0.40)

        ctx = {
            "conn": db,
            "iso3": "NGA",
            "config": {"name": "Nigeria"},
            "calibrated_prob": 0.40,
            "supervisor_result": {"event_type_scores": {}},
            "fused_event_types": {},
        }
        ctx = step_detect_alerts(ctx)
        assert "alert_triggers" in ctx
        assert len(ctx["alert_triggers"]) > 0
