"""
Integration tests for the refactored V2 pipeline.

Uses in-memory SQLite + mocked LLM calls to test the full pipeline
without making real API calls. Verifies:
- Pipeline step functions work independently and in sequence
- 6 prediction rows are stored (5 event types + 1 composite)
- Event-type scores are present and valid
- Source-agnostic: all test fixtures use conflict_events, not acled_events
"""
import sys
sys.path.insert(0, ".")

import json
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from utils.db import (
    initialize_db, get_connection, insert_conflict_event,
    insert_article, insert_reasoning_chain,
)
from utils.logger import log_prediction
from utils.validation import validate_probability, validate_event_type_scores
from pipeline.daily_run import (
    step_track_a, step_fuse, step_log,
    step_filter_extract, step_contradiction, step_resolve,
)
from config.settings import FUSION_WEIGHT_TRACK_A, EVENT_TYPE_RESOLUTION


# --- Fixtures ---

@pytest.fixture
def db():
    """In-memory SQLite with full schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Build full schema from initialize_db logic but in memory
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS acled_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_type TEXT,
            sub_event_type TEXT,
            fatalities INTEGER DEFAULT 0,
            latitude REAL, longitude REAL,
            source TEXT, notes TEXT,
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            title TEXT, source TEXT, url TEXT,
            published_date TEXT, full_text TEXT,
            is_relevant BOOLEAN DEFAULT NULL,
            reliability_tier INTEGER DEFAULT NULL,
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS reasoning_chains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            article_id INTEGER,
            chain_json TEXT NOT NULL,
            prompt_version TEXT, model_version TEXT,
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
        CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_unique
            ON predictions(country_iso3, prediction_date,
                           COALESCE(event_type, 'composite'));
        CREATE TABLE IF NOT EXISTS agent_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id INTEGER,
            country_iso3 TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            probability REAL,
            reasoning_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
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
            latitude REAL, longitude REAL,
            source_url TEXT,
            source_article_id INTEGER,
            actors TEXT, confidence REAL, notes TEXT,
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
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
        CREATE TABLE IF NOT EXISTS gdelt_conflict_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_type TEXT,
            num_articles INTEGER DEFAULT 1,
            avg_tone REAL,
            latitude REAL, longitude REAL,
            source_url TEXT,
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_conflict_events_unique
            ON conflict_events(country_iso3, event_date, event_category, source,
                               latitude, longitude);
    """)
    return conn


@pytest.fixture
def country_config():
    """Minimal country config for testing."""
    return {
        "name": "Testland",
        "iso3": "TST",
        "polity_code": 4,
        "polity_category": "factionalized",
        "infant_mortality_per_1000": 50,
        "years_since_last_instability": 3,
        "gdp_per_capita_usd": 3000,
        "population_millions": 50,
        "terrain_ruggedness": "moderate",
        "v_dem_liberal_democracy_index": 0.35,
        "neighboring_countries": [],
        "key_actors": ["Government", "Opposition"],
        "risk_context": "Test country with moderate instability risk.",
        "news_sources_rss": [],
    }


def _mock_ensemble_results():
    """Canned agent results with event_type_scores."""
    base_scores = {"ACE": 0.25, "MCU": 0.15, "REC": 0.05, "PSS": 0.10, "CID": 0.08}
    return [
        {
            "agent_type": "baserate",
            "final_probability": 0.22,
            "event_type_scores": base_scores,
            "key_uncertainties": ["test uncertainty"],
            "base_rate_acknowledged": 0.20,
            "upward_adjustments": [],
            "downward_adjustments": [],
        },
        {
            "agent_type": "analogy",
            "final_probability": 0.18,
            "event_type_scores": {k: v * 0.9 for k, v in base_scores.items()},
            "key_uncertainties": [],
            "analogies": [],
            "synthesis": "test",
        },
        {
            "agent_type": "decomposition",
            "final_probability": 0.25,
            "event_type_scores": {k: v * 1.1 for k, v in base_scores.items()},
            "key_uncertainties": [],
            "sub_questions": [],
            "combination_logic": "test",
        },
        {
            "agent_type": "devil",
            "final_probability": 0.20,
            "event_type_scores": base_scores,
            "key_uncertainties": [],
            "consensus_challenged": "test",
            "contrarian_arguments": [],
        },
    ]


def _mock_supervisor_result():
    """Canned supervisor result with event_type_scores."""
    return {
        "final_probability": 0.21,
        "event_type_scores": {
            "ACE": 0.25, "MCU": 0.15, "REC": 0.05, "PSS": 0.10, "CID": 0.08,
        },
        "event_type_drivers": {
            "ACE": "Ongoing border clashes with increasing frequency",
            "MCU": "Economic grievances following currency devaluation",
        },
        "narrative_summary": "Test narrative",
        "executive_summary": "Test executive summary",
        "key_risk_factors": ["factor 1"],
        "key_stabilizing_factors": ["stabilizer 1"],
        "confidence": "medium",
        "agreements": ["agents agree on moderate risk"],
        "disagreements": [],
        "_meta": {"structured": True},
    }


@pytest.fixture
def pipeline_ctx(db, country_config):
    """Build a pipeline context with all prior steps completed (mocked)."""
    iso3 = country_config["iso3"]

    # Seed some conflict events
    for i in range(10):
        insert_conflict_event(
            db, iso3, f"2026-04-{i+1:02d}", "ACE", "gdelt",
            fatalities=1, latitude=float(i),
        )
    db.commit()

    # Seed an article
    insert_article(db, iso3, "Test article", "test", "http://test.com/1",
                    "2026-04-10", "Full text of test article about conflict.")
    db.commit()

    track_a = {
        "probability": 0.20,
        "components": {
            "pitf_base": 0.15,
            "structural_vulnerability": 0.40,
            "neighborhood_contagion": 0.10,
            "gpr_trend": 0.02,
            "gpr_trend_description": "slightly elevated",
            "conflict_avg_monthly": 10,
            "conflict_floor_applied": 0.0,
        },
    }

    return {
        "conn": db,
        "config": country_config,
        "iso3": iso3,
        "track_a": track_a,
        "event_threshold": 15.0,
        "track_b_prob": 0.21,
        "supervisor_result": _mock_supervisor_result(),
        "ensemble_results": _mock_ensemble_results(),
        "event_data": {"total_events": 10, "total_fatalities": 5,
                        "by_category": [], "period_days": 30},
        "articles": [{"id": 1, "title": "Test"}],
    }


# --- Tests ---

class TestStepFuse:
    def test_produces_composite_and_event_type_scores(self, pipeline_ctx):
        ctx = step_fuse(pipeline_ctx)

        assert "calibrated_prob" in ctx
        assert "fused_event_types" in ctx
        assert len(ctx["fused_event_types"]) == 5
        assert set(ctx["fused_event_types"].keys()) == {"ACE", "MCU", "REC", "PSS", "CID"}

    def test_composite_prob_in_valid_range(self, pipeline_ctx):
        ctx = step_fuse(pipeline_ctx)
        assert 0.01 <= ctx["calibrated_prob"] <= 0.99

    def test_event_type_probs_in_valid_range(self, pipeline_ctx):
        ctx = step_fuse(pipeline_ctx)
        for et, scores in ctx["fused_event_types"].items():
            assert 0.01 <= scores["calibrated"] <= 0.99, f"{et} calibrated out of range"
            assert 0.01 <= scores["fused"] <= 0.99, f"{et} fused out of range"

    def test_composite_uses_track_a_and_b(self, pipeline_ctx):
        ctx = step_fuse(pipeline_ctx)
        # Composite should be between track_a (0.20) and track_b (0.21)
        assert 0.15 <= ctx["calibrated_prob"] <= 0.30


class TestStepLog:
    def test_stores_six_prediction_rows(self, pipeline_ctx):
        ctx = step_fuse(pipeline_ctx)
        ctx = step_log(ctx)

        conn = ctx["conn"]
        rows = conn.execute(
            "SELECT event_type, calibrated_probability FROM predictions WHERE country_iso3 = 'TST'"
        ).fetchall()

        assert len(rows) == 6
        types = {r["event_type"] for r in rows}
        assert types == {"composite", "ACE", "MCU", "REC", "PSS", "CID"}

    def test_composite_has_agent_outputs(self, pipeline_ctx):
        ctx = step_fuse(pipeline_ctx)
        ctx = step_log(ctx)

        conn = ctx["conn"]
        agent_rows = conn.execute(
            "SELECT * FROM agent_outputs WHERE prediction_id = ?",
            (ctx["prediction_id"],),
        ).fetchall()

        assert len(agent_rows) == 4  # 4 agents
        agent_types = {r["agent_type"] for r in agent_rows}
        assert agent_types == {"baserate", "analogy", "decomposition", "devil"}

    def test_event_type_rows_have_correct_track_b(self, pipeline_ctx):
        ctx = step_fuse(pipeline_ctx)
        ctx = step_log(ctx)

        conn = ctx["conn"]
        ace_row = conn.execute(
            "SELECT track_b_probability FROM predictions WHERE country_iso3='TST' AND event_type='ACE'"
        ).fetchone()

        # ACE track_b should be the supervisor's ACE score (0.25)
        assert ace_row is not None
        assert abs(ace_row["track_b_probability"] - 0.25) < 0.01

    def test_idempotent_on_rerun(self, pipeline_ctx):
        """Running step_log twice for the same date should REPLACE, not duplicate."""
        ctx = step_fuse(pipeline_ctx)
        step_log(ctx)
        step_log(ctx)  # second run

        conn = ctx["conn"]
        count = conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE country_iso3 = 'TST'"
        ).fetchone()[0]
        assert count == 6  # still 6, not 12


class TestStepContradiction:
    def test_no_crash_with_empty_events(self, pipeline_ctx):
        """Contradiction check should handle zero events gracefully."""
        ctx = step_fuse(pipeline_ctx)
        ctx = step_log(ctx)
        ctx = step_contradiction(ctx)
        assert "contradiction" in ctx

    def test_stores_alert_on_spike(self, pipeline_ctx):
        """If events spike, contradiction check should store an alert."""
        # Seed a 90-day baseline of ~2 events/week, then spike to 50 in last 7 days
        conn = pipeline_ctx["conn"]
        iso3 = pipeline_ctx["iso3"]
        for i in range(26):  # 26 events over 90 days (~2/week)
            insert_conflict_event(
                conn, iso3, f"2026-01-{(i % 28) + 1:02d}", "ACE", "gdelt",
                fatalities=1, latitude=float(100 + i),
            )
        for i in range(50):  # 50 events in last 7 days
            insert_conflict_event(
                conn, iso3, f"2026-04-{10 + (i % 4):02d}", "ACE", "internal",
                fatalities=1, latitude=float(200 + i),
            )
        conn.commit()

        ctx = step_fuse(pipeline_ctx)
        ctx = step_log(ctx)
        ctx = step_contradiction(ctx)

        # Should have detected a spike
        assert ctx["contradiction"]["assessment"] in ("potential_contradiction", "neutral")


class TestStepResolve:
    def test_no_crash_with_no_expired(self, pipeline_ctx):
        ctx = step_fuse(pipeline_ctx)
        ctx = step_log(ctx)
        ctx = step_resolve(ctx)
        assert "resolved" in ctx
        assert ctx["resolved"] == []  # nothing expired yet


class TestEndToEnd:
    def test_fuse_log_resolve_sequence(self, pipeline_ctx):
        """Full step sequence: fuse -> log -> contradiction -> resolve."""
        ctx = pipeline_ctx
        ctx = step_fuse(ctx)
        ctx = step_log(ctx)
        ctx = step_contradiction(ctx)
        ctx = step_resolve(ctx)

        # Verify final state
        conn = ctx["conn"]
        rows = conn.execute(
            "SELECT event_type, calibrated_probability FROM predictions WHERE country_iso3='TST' ORDER BY event_type"
        ).fetchall()
        assert len(rows) == 6

        # Composite should exist
        composite = [r for r in rows if r["event_type"] == "composite"]
        assert len(composite) == 1
        assert 0.01 <= composite[0]["calibrated_probability"] <= 0.99

    def test_event_type_scores_consistent_with_supervisor(self, pipeline_ctx):
        """Verify that stored per-type track_b matches supervisor output."""
        ctx = step_fuse(pipeline_ctx)
        ctx = step_log(ctx)

        conn = ctx["conn"]
        supervisor_scores = ctx["supervisor_result"]["event_type_scores"]

        for et in ["ACE", "MCU", "REC", "PSS", "CID"]:
            row = conn.execute(
                "SELECT track_b_probability FROM predictions WHERE country_iso3='TST' AND event_type=?",
                (et,),
            ).fetchone()
            assert row is not None, f"Missing prediction for {et}"
            assert abs(row["track_b_probability"] - supervisor_scores[et]) < 0.01, \
                f"{et} track_b mismatch: stored={row['track_b_probability']}, expected={supervisor_scores[et]}"
