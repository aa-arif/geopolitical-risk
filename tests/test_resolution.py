"""
Tests for source-agnostic resolution logic.

Written BEFORE the ACLED migration as the spec for the migrated resolve.py.
Tests use the unified conflict_events table and EVENT_TYPE_RESOLUTION config.
"""
import sys
sys.path.insert(0, ".")

import sqlite3
import pytest
from datetime import datetime, timedelta

from utils.db import (
    get_event_count, compute_event_threshold_by_category,
    insert_conflict_event,
)
from config.settings import EVENT_TYPE_RESOLUTION, CALIBRATED_EVENT_TYPES


# --- Fixtures ---

@pytest.fixture
def db():
    """In-memory SQLite database with conflict_events table."""
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
            pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX idx_conflict_events_unique
            ON conflict_events(country_iso3, event_date, event_category, source,
                               latitude, longitude);
        CREATE TABLE predictions (
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
            event_threshold REAL DEFAULT NULL,
            event_type TEXT DEFAULT 'composite',
            resolved BOOLEAN DEFAULT FALSE,
            actual_outcome INTEGER DEFAULT NULL,
            brier_score REAL DEFAULT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    return conn


def _seed_monthly_events(conn, iso3, event_category, source, months_of_data,
                          events_per_month, fatalities_per_event=0):
    """Seed conflict_events with a regular pattern of monthly events."""
    base = datetime(2025, 1, 1)
    for month_offset in range(months_of_data):
        month_start = base + timedelta(days=30 * month_offset)
        for day in range(events_per_month):
            event_date = (month_start + timedelta(days=day)).strftime("%Y-%m-%d")
            insert_conflict_event(
                conn, iso3, event_date, event_category, source,
                fatalities=fatalities_per_event,
                latitude=float(day),  # unique per event for dedup
            )
    conn.commit()


def _seed_prediction(conn, iso3, pred_date, window_end, calibrated_prob,
                      event_threshold=None, event_type="composite"):
    """Seed a prediction row for resolution testing."""
    conn.execute(
        """INSERT INTO predictions
           (country_iso3, prediction_date, window_end_date,
            calibrated_probability, event_threshold, event_type)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (iso3, pred_date, window_end, calibrated_prob, event_threshold, event_type),
    )
    conn.commit()


# --- Config tests ---

class TestEventTypeResolutionConfig:
    def test_all_five_event_types_defined(self):
        assert set(EVENT_TYPE_RESOLUTION.keys()) == {"ACE", "MCU", "REC", "PSS", "CID"}

    def test_calibrated_types_have_threshold_methods(self):
        for et in CALIBRATED_EVENT_TYPES:
            assert EVENT_TYPE_RESOLUTION[et]["threshold_method"] is not None, \
                f"{et} is calibrated but has no threshold_method"

    def test_qualitative_types_have_no_threshold(self):
        for et in ["PSS", "CID"]:
            assert EVENT_TYPE_RESOLUTION[et]["threshold_method"] is None, \
                f"{et} should be qualitative (no threshold)"

    def test_all_types_have_sources(self):
        for et, cfg in EVENT_TYPE_RESOLUTION.items():
            assert "sources" in cfg and len(cfg["sources"]) > 0, \
                f"{et} has no data sources"

    def test_ace_requires_fatalities(self):
        assert EVENT_TYPE_RESOLUTION["ACE"]["require_fatalities"] is True

    def test_mcu_does_not_require_fatalities(self):
        assert EVENT_TYPE_RESOLUTION["MCU"]["require_fatalities"] is False


# --- Event count tests ---

class TestGetEventCount:
    def test_counts_events_in_range(self, db):
        insert_conflict_event(db, "NGA", "2026-03-15", "ACE", "gdelt", fatalities=2)
        insert_conflict_event(db, "NGA", "2026-03-20", "ACE", "gdelt", fatalities=1)
        insert_conflict_event(db, "NGA", "2026-04-05", "ACE", "gdelt", fatalities=3)
        db.commit()

        count = get_event_count(db, "NGA", "2026-03-10", "2026-03-31", event_category="ACE")
        assert count == 2

    def test_fatality_filter(self, db):
        insert_conflict_event(db, "NGA", "2026-03-15", "ACE", "gdelt", fatalities=2)
        insert_conflict_event(db, "NGA", "2026-03-16", "ACE", "gdelt", fatalities=0)
        db.commit()

        with_fatalities = get_event_count(
            db, "NGA", "2026-03-10", "2026-03-31",
            event_category="ACE", require_fatalities=True,
        )
        assert with_fatalities == 1

        without_filter = get_event_count(
            db, "NGA", "2026-03-10", "2026-03-31", event_category="ACE",
        )
        assert without_filter == 2

    def test_category_filter(self, db):
        insert_conflict_event(db, "NGA", "2026-03-15", "ACE", "gdelt", fatalities=1)
        insert_conflict_event(db, "NGA", "2026-03-15", "MCU", "gdelt",
                               latitude=1.0)  # different lat for dedup
        db.commit()

        ace = get_event_count(db, "NGA", "2026-03-10", "2026-03-20", event_category="ACE")
        mcu = get_event_count(db, "NGA", "2026-03-10", "2026-03-20", event_category="MCU")
        all_events = get_event_count(db, "NGA", "2026-03-10", "2026-03-20")
        assert ace == 1
        assert mcu == 1
        assert all_events == 2

    def test_no_events_returns_zero(self, db):
        count = get_event_count(db, "NGA", "2026-03-10", "2026-03-31", event_category="ACE")
        assert count == 0

    def test_boundary_dates_are_inclusive(self, db):
        insert_conflict_event(db, "NGA", "2026-03-10", "ACE", "gdelt", fatalities=1)
        insert_conflict_event(db, "NGA", "2026-03-31", "ACE", "gdelt", fatalities=1,
                               latitude=1.0)
        db.commit()

        count = get_event_count(db, "NGA", "2026-03-10", "2026-03-31", event_category="ACE")
        assert count == 2


# --- Threshold tests ---

class TestComputeEventThresholdByCategory:
    def test_computes_90th_percentile(self, db):
        # 12 months of data: 10 events/month for months 1-11, 50 for month 12
        _seed_monthly_events(db, "NGA", "ACE", "gdelt", 11, 10, fatalities_per_event=1)
        _seed_monthly_events(db, "NGA", "ACE", "internal", 1, 50, fatalities_per_event=1)
        # Note: internal events overlap on month 1, but with different source
        # so they're separate entries. Let's be more precise:

        # Clear and reseed with controlled data
        db.execute("DELETE FROM conflict_events")
        db.commit()
        base = datetime(2025, 1, 1)
        for month in range(12):
            events_this_month = 50 if month == 11 else 10
            month_start = base + timedelta(days=30 * month)
            for i in range(events_this_month):
                d = (month_start + timedelta(days=i % 28)).strftime("%Y-%m-%d")
                insert_conflict_event(
                    db, "NGA", d, "ACE", "gdelt",
                    fatalities=1,
                    latitude=float(month * 100 + i),  # unique
                )
        db.commit()

        threshold = compute_event_threshold_by_category(
            db, "NGA", "ACE", require_fatalities=True,
            months=12, before_date="2026-01-01",
        )
        # 11 months at 10, 1 month at 50. 90th pct should be well above 10.
        assert threshold > 10

    def test_no_events_returns_zero(self, db):
        threshold = compute_event_threshold_by_category(
            db, "NGA", "ACE", months=12, before_date="2026-01-01",
        )
        assert threshold == 0.0

    def test_respects_before_date(self, db):
        # Events in Jan and Feb 2026
        for i in range(5):
            insert_conflict_event(
                db, "NGA", f"2026-01-{10+i:02d}", "ACE", "gdelt",
                fatalities=1, latitude=float(i),
            )
        for i in range(5):
            insert_conflict_event(
                db, "NGA", f"2026-02-{10+i:02d}", "ACE", "gdelt",
                fatalities=1, latitude=float(100 + i),
            )
        db.commit()

        # before_date = Feb 1 should only see Jan events
        threshold_jan = compute_event_threshold_by_category(
            db, "NGA", "ACE", require_fatalities=True,
            months=12, before_date="2026-02-01",
        )
        # before_date = Mar 1 should see both months
        threshold_both = compute_event_threshold_by_category(
            db, "NGA", "ACE", require_fatalities=True,
            months=12, before_date="2026-03-01",
        )
        # With only 1 month of data, threshold == that month's count
        assert threshold_jan == 5.0
        # With 2 months at 5 each, 90th pct of [5, 5] == 5
        assert threshold_both == 5.0

    def test_fatality_filter(self, db):
        for i in range(10):
            insert_conflict_event(
                db, "NGA", f"2025-06-{10+i:02d}", "ACE", "gdelt",
                fatalities=1 if i < 5 else 0,
                latitude=float(i),
            )
        db.commit()

        with_fatalities = compute_event_threshold_by_category(
            db, "NGA", "ACE", require_fatalities=True,
            months=12, before_date="2025-07-01",
        )
        without_fatalities = compute_event_threshold_by_category(
            db, "NGA", "ACE", require_fatalities=False,
            months=12, before_date="2025-07-01",
        )
        assert with_fatalities < without_fatalities


# --- Resolution logic spec tests ---
# These define the TARGET behavior for the migrated resolve.py.

class TestResolutionSpec:
    """
    Spec tests for how resolution SHOULD work after migration.
    Each test describes: given events + threshold + config -> expected outcome.
    """

    def test_ace_exceeds_threshold_resolves_positive(self, db):
        """ACE: 20 violent events with fatalities, threshold 15 -> outcome=1."""
        for i in range(20):
            insert_conflict_event(
                db, "IRQ", f"2025-06-{(i % 28) + 1:02d}", "ACE", "gdelt",
                fatalities=2, latitude=float(i),
            )
        db.commit()

        count = get_event_count(
            db, "IRQ", "2025-06-01", "2025-06-30",
            event_category="ACE",
            require_fatalities=EVENT_TYPE_RESOLUTION["ACE"]["require_fatalities"],
        )
        threshold = 15
        assert count > threshold
        assert count == 20

    def test_ace_below_threshold_resolves_negative(self, db):
        """ACE: 5 violent events, threshold 15 -> outcome=0."""
        for i in range(5):
            insert_conflict_event(
                db, "IRQ", f"2025-06-{10+i:02d}", "ACE", "gdelt",
                fatalities=1, latitude=float(i),
            )
        db.commit()

        count = get_event_count(
            db, "IRQ", "2025-06-01", "2025-06-30",
            event_category="ACE",
            require_fatalities=True,
        )
        threshold = 15
        assert count < threshold

    def test_mcu_does_not_require_fatalities(self, db):
        """MCU: protests without fatalities should still count."""
        for i in range(30):
            insert_conflict_event(
                db, "EGY", f"2025-06-{(i % 28) + 1:02d}", "MCU", "internal",
                event_type="protest", fatalities=0, latitude=float(i),
            )
        db.commit()

        count = get_event_count(
            db, "EGY", "2025-06-01", "2025-06-30",
            event_category="MCU",
            require_fatalities=EVENT_TYPE_RESOLUTION["MCU"]["require_fatalities"],
        )
        assert count == 30

    def test_rec_occurrence_based(self, db):
        """REC: any occurrence counts (threshold_method='occurrence')."""
        assert EVENT_TYPE_RESOLUTION["REC"]["threshold_method"] == "occurrence"

        # One coup event is enough
        insert_conflict_event(
            db, "SDN", "2025-06-15", "REC", "internal",
            event_type="coup", severity="high",
        )
        db.commit()

        count = get_event_count(
            db, "SDN", "2025-06-01", "2025-06-30", event_category="REC",
        )
        assert count >= 1  # occurrence-based: any count > 0 is positive

    def test_rec_no_occurrence_resolves_negative(self, db):
        """REC: zero events -> outcome=0."""
        count = get_event_count(
            db, "SDN", "2025-06-01", "2025-06-30", event_category="REC",
        )
        assert count == 0

    def test_pss_has_no_automated_resolution(self):
        """PSS should not be automatically resolved."""
        assert EVENT_TYPE_RESOLUTION["PSS"]["threshold_method"] is None
        assert "PSS" not in CALIBRATED_EVENT_TYPES

    def test_cid_has_no_automated_resolution(self):
        """CID should not be automatically resolved."""
        assert EVENT_TYPE_RESOLUTION["CID"]["threshold_method"] is None
        assert "CID" not in CALIBRATED_EVENT_TYPES

    def test_cross_source_events_both_counted(self, db):
        """Events from both GDELT and internal sources count toward resolution."""
        insert_conflict_event(
            db, "NGA", "2025-06-15", "ACE", "gdelt",
            fatalities=3,
        )
        insert_conflict_event(
            db, "NGA", "2025-06-15", "ACE", "internal",
            fatalities=2, latitude=1.0,  # different source -> different unique key
        )
        db.commit()

        count = get_event_count(
            db, "NGA", "2025-06-01", "2025-06-30",
            event_category="ACE", require_fatalities=True,
        )
        assert count == 2

    def test_events_outside_window_not_counted(self, db):
        """Only events within the prediction window count."""
        insert_conflict_event(
            db, "NGA", "2025-05-31", "ACE", "gdelt",
            fatalities=1,
        )
        insert_conflict_event(
            db, "NGA", "2025-06-15", "ACE", "gdelt",
            fatalities=1, latitude=1.0,
        )
        insert_conflict_event(
            db, "NGA", "2025-07-01", "ACE", "gdelt",
            fatalities=1, latitude=2.0,
        )
        db.commit()

        count = get_event_count(
            db, "NGA", "2025-06-01", "2025-06-30",
            event_category="ACE", require_fatalities=True,
        )
        assert count == 1  # only the June 15 event


# --- End-to-end resolution tests ---
# These call resolve_expired_predictions() with seeded data and verify
# the full flow: prediction + events -> resolved outcome + Brier score.

class TestResolveEndToEnd:
    """Integration tests that run the actual resolve_expired_predictions function."""

    def _add_legacy_tables(self, db):
        """Add legacy tables needed by resolve.py's composite fallback."""
        db.executescript("""
            CREATE TABLE IF NOT EXISTS acled_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                country_iso3 TEXT, event_date TEXT, event_type TEXT,
                sub_event_type TEXT, fatalities INTEGER DEFAULT 0,
                latitude REAL, longitude REAL, source TEXT, notes TEXT,
                pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS gdelt_conflict_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                country_iso3 TEXT, event_date TEXT, event_type TEXT,
                num_articles INTEGER DEFAULT 1, avg_tone REAL,
                latitude REAL, longitude REAL, source_url TEXT,
                pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

    def test_ace_prediction_resolves_positive(self, db):
        """ACE prediction with events exceeding threshold resolves to outcome=1."""
        from evaluation.resolve import resolve_expired_predictions
        self._add_legacy_tables(db)

        # Seed an expired ACE prediction with stored threshold of 10
        _seed_prediction(db, "IRQ", "2025-05-01", "2025-06-01", 0.65,
                          event_threshold=10, event_type="ACE")

        # Seed 15 ACE events in the window (exceeds threshold of 10)
        for i in range(15):
            insert_conflict_event(
                db, "IRQ", f"2025-05-{(i % 28) + 1:02d}", "ACE", "gdelt",
                fatalities=1, latitude=float(i),
            )
        db.commit()

        resolved = resolve_expired_predictions(db, "IRQ")
        assert len(resolved) == 1
        assert resolved[0]["event_type"] == "ACE"
        assert resolved[0]["actual_outcome"] == 1
        assert resolved[0]["brier_score"] < 0.2  # predicted 0.65, outcome 1

    def test_ace_prediction_resolves_negative(self, db):
        """ACE prediction with events below threshold resolves to outcome=0."""
        from evaluation.resolve import resolve_expired_predictions
        self._add_legacy_tables(db)

        _seed_prediction(db, "NGA", "2025-05-01", "2025-06-01", 0.60,
                          event_threshold=20, event_type="ACE")

        # Seed 5 ACE events (below threshold of 20)
        for i in range(5):
            insert_conflict_event(
                db, "NGA", f"2025-05-{10+i:02d}", "ACE", "gdelt",
                fatalities=1, latitude=float(i),
            )
        db.commit()

        resolved = resolve_expired_predictions(db, "NGA")
        assert len(resolved) == 1
        assert resolved[0]["actual_outcome"] == 0
        assert resolved[0]["brier_score"] > 0.3  # predicted 0.60, outcome 0

    def test_mcu_resolves_without_fatalities(self, db):
        """MCU prediction counts events without fatality requirement."""
        from evaluation.resolve import resolve_expired_predictions
        self._add_legacy_tables(db)

        _seed_prediction(db, "EGY", "2025-05-01", "2025-06-01", 0.40,
                          event_threshold=5, event_type="MCU")

        # Seed 10 MCU events with 0 fatalities
        for i in range(10):
            insert_conflict_event(
                db, "EGY", f"2025-05-{(i % 28) + 1:02d}", "MCU", "internal",
                event_type="protest", fatalities=0, latitude=float(i),
            )
        db.commit()

        resolved = resolve_expired_predictions(db, "EGY")
        assert len(resolved) == 1
        assert resolved[0]["event_type"] == "MCU"
        assert resolved[0]["actual_outcome"] == 1  # 10 > 5

    def test_rec_resolves_on_occurrence(self, db):
        """REC prediction resolves positive on any occurrence."""
        from evaluation.resolve import resolve_expired_predictions
        self._add_legacy_tables(db)

        _seed_prediction(db, "SDN", "2025-05-01", "2025-06-01", 0.15,
                          event_threshold=0, event_type="REC")

        # One coup event
        insert_conflict_event(
            db, "SDN", "2025-05-15", "REC", "internal",
            event_type="coup", severity="high",
        )
        db.commit()

        resolved = resolve_expired_predictions(db, "SDN")
        assert len(resolved) == 1
        assert resolved[0]["actual_outcome"] == 1
        assert resolved[0]["brier_score"] > 0.5  # predicted 0.15, outcome 1

    def test_pss_prediction_skipped(self, db):
        """PSS predictions should not be automatically resolved."""
        from evaluation.resolve import resolve_expired_predictions
        self._add_legacy_tables(db)

        _seed_prediction(db, "IRN", "2025-05-01", "2025-06-01", 0.50,
                          event_type="PSS")
        db.commit()

        resolved = resolve_expired_predictions(db, "IRN")
        assert len(resolved) == 0

        # Verify prediction is still unresolved
        row = db.execute(
            "SELECT resolved FROM predictions WHERE country_iso3='IRN' AND event_type='PSS'"
        ).fetchone()
        assert row["resolved"] == 0

    def test_cid_prediction_skipped(self, db):
        """CID predictions should not be automatically resolved."""
        from evaluation.resolve import resolve_expired_predictions
        self._add_legacy_tables(db)

        _seed_prediction(db, "IRN", "2025-05-01", "2025-06-01", 0.30,
                          event_type="CID")
        db.commit()

        resolved = resolve_expired_predictions(db, "IRN")
        assert len(resolved) == 0

    def test_multiple_types_resolve_independently(self, db):
        """ACE and MCU predictions for the same country resolve independently."""
        from evaluation.resolve import resolve_expired_predictions
        self._add_legacy_tables(db)

        # ACE: predicted high, will resolve positive
        _seed_prediction(db, "NGA", "2025-05-01", "2025-06-01", 0.70,
                          event_threshold=5, event_type="ACE")
        # MCU: predicted low, will resolve negative
        _seed_prediction(db, "NGA", "2025-05-01", "2025-06-01", 0.10,
                          event_threshold=10, event_type="MCU")

        # Seed 10 ACE events (above threshold 5)
        for i in range(10):
            insert_conflict_event(
                db, "NGA", f"2025-05-{(i % 28) + 1:02d}", "ACE", "gdelt",
                fatalities=1, latitude=float(i),
            )
        # Seed 3 MCU events (below threshold 10)
        for i in range(3):
            insert_conflict_event(
                db, "NGA", f"2025-05-{10+i:02d}", "MCU", "internal",
                fatalities=0, latitude=float(100 + i),
            )
        db.commit()

        resolved = resolve_expired_predictions(db, "NGA")
        assert len(resolved) == 2

        ace_r = next(r for r in resolved if r["event_type"] == "ACE")
        mcu_r = next(r for r in resolved if r["event_type"] == "MCU")

        assert ace_r["actual_outcome"] == 1
        assert mcu_r["actual_outcome"] == 0
        # ACE: predicted 0.70, outcome 1 -> good prediction, low Brier
        assert ace_r["brier_score"] < 0.15
        # MCU: predicted 0.10, outcome 0 -> good prediction, low Brier
        assert mcu_r["brier_score"] < 0.05

    def test_unexpired_prediction_not_resolved(self, db):
        """Predictions whose windows haven't closed should not be resolved."""
        from evaluation.resolve import resolve_expired_predictions
        self._add_legacy_tables(db)

        # Window ends in 2026 — still in the future
        _seed_prediction(db, "NGA", "2026-04-01", "2026-05-01", 0.50,
                          event_threshold=10, event_type="ACE")
        db.commit()

        resolved = resolve_expired_predictions(db, "NGA")
        assert len(resolved) == 0
