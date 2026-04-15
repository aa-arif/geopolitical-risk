"""Tests for article classification pipeline."""
import sys
sys.path.insert(0, ".")

import sqlite3
import pytest
from unittest.mock import patch

from pipeline.classify import (
    classify_articles, classify_and_store,
    _build_classification_prompt, _CLASSIFY_SCHEMA,
)
from pipeline.ingest import _classify_gdelt_article


class TestGdeltKeywordClassifier:
    """Tests for the lightweight keyword-based GDELT classifier."""

    def test_military_classified_as_ace(self):
        assert _classify_gdelt_article("Military clashes in northern region") == "ACE"

    def test_bombing_classified_as_ace(self):
        assert _classify_gdelt_article("Car bombing kills 12 in Baghdad market") == "ACE"

    def test_protest_classified_as_mcu(self):
        assert _classify_gdelt_article("Thousands protest government corruption") == "MCU"

    def test_riot_classified_as_mcu(self):
        assert _classify_gdelt_article("Riot police deploy tear gas at demonstration") == "MCU"

    def test_coup_classified_as_rec(self):
        assert _classify_gdelt_article("Coup attempt thwarted by security forces") == "REC"

    def test_resignation_classified_as_rec(self):
        assert _classify_gdelt_article("Prime Minister resigns amid political crisis") == "REC"

    def test_generic_conflict_defaults_to_ace(self):
        assert _classify_gdelt_article("Violence erupts in eastern provinces") == "ACE"

    def test_unrelated_defaults_to_ace(self):
        # GDELT query already filtered for conflict terms, so even vague
        # titles default to ACE
        assert _classify_gdelt_article("Situation deteriorates rapidly") == "ACE"


class TestClassificationPrompt:
    def test_prompt_includes_all_event_types(self):
        articles = [{"title": "Test article", "full_text": "Test text", "id": 1}]
        prompt = _build_classification_prompt(articles, "Nigeria")
        assert "ACE" in prompt
        assert "MCU" in prompt
        assert "REC" in prompt
        assert "PSS" in prompt
        assert "CID" in prompt
        assert "NONE" in prompt
        assert "Nigeria" in prompt

    def test_prompt_truncates_long_text(self):
        articles = [{"title": "Test", "full_text": "x" * 2000, "id": 1}]
        prompt = _build_classification_prompt(articles, "Test")
        # Text should be truncated to ~500 chars
        assert len(prompt) < 3000

    def test_prompt_handles_batch(self):
        articles = [
            {"title": f"Article {i}", "full_text": f"Text {i}", "id": i}
            for i in range(5)
        ]
        prompt = _build_classification_prompt(articles, "Test")
        assert "[Article 0]" in prompt
        assert "[Article 4]" in prompt
        assert "Classify all 5 articles" in prompt


class TestClassifySchema:
    def test_schema_has_required_fields(self):
        item_props = _CLASSIFY_SCHEMA["properties"]["classifications"]["items"]["properties"]
        assert "article_index" in item_props
        assert "event_category" in item_props
        assert "severity" in item_props
        assert "confidence" in item_props

    def test_event_category_enum(self):
        enum = _CLASSIFY_SCHEMA["properties"]["classifications"]["items"]["properties"]["event_category"]["enum"]
        assert set(enum) == {"ACE", "MCU", "REC", "PSS", "CID", "NONE"}


class TestClassifyAndStore:
    @pytest.fixture
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY, country_iso3 TEXT, title TEXT,
                source TEXT, url TEXT, published_date TEXT, full_text TEXT,
                is_relevant BOOLEAN, reliability_tier INTEGER,
                pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE conflict_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                country_iso3 TEXT NOT NULL, event_date TEXT NOT NULL,
                event_category TEXT NOT NULL, event_type TEXT,
                sub_event_type TEXT, source TEXT NOT NULL,
                fatalities INTEGER DEFAULT 0, severity TEXT,
                num_articles INTEGER DEFAULT 1, avg_tone REAL,
                latitude REAL, longitude REAL, source_url TEXT,
                source_article_id INTEGER, actors TEXT,
                confidence REAL, notes TEXT,
                pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE UNIQUE INDEX idx_conflict_events_unique
                ON conflict_events(country_iso3, event_date, event_category,
                                   source, latitude, longitude);
        """)
        return conn

    def test_no_articles_returns_zero(self, db):
        config = {"iso3": "NGA", "name": "Nigeria"}
        count = classify_and_store(db, config)
        assert count == 0

    @patch("pipeline.classify.generate_with_tool")
    def test_classifies_and_stores_events(self, mock_gen, db):
        """Mock the LLM call and verify events are stored."""
        # Seed a relevant article
        db.execute(
            "INSERT INTO articles (id, country_iso3, title, full_text, is_relevant, published_date, pulled_at) "
            "VALUES (1, 'NGA', 'Military operation in Borno', 'Full text...', 1, '2026-04-14', '2026-04-14')"
        )
        db.commit()

        # Mock LLM response
        mock_gen.return_value = {
            "classifications": [{
                "article_index": 0,
                "event_category": "ACE",
                "sub_category": "armed_clash",
                "severity": "high",
                "fatalities_mentioned": True,
                "estimated_fatalities": 15,
                "key_actors": "Nigerian Army, Boko Haram",
                "confidence": 0.9,
            }],
            "_meta": {"structured": True},
        }

        config = {"iso3": "NGA", "name": "Nigeria"}
        count = classify_and_store(db, config)
        assert count == 1

        # Verify stored event
        events = db.execute("SELECT * FROM conflict_events WHERE country_iso3='NGA'").fetchall()
        assert len(events) == 1
        assert events[0]["event_category"] == "ACE"
        assert events[0]["source"] == "internal"
        assert events[0]["fatalities"] == 15
        assert events[0]["source_article_id"] == 1

    @patch("pipeline.classify.generate_with_tool")
    def test_none_category_not_stored(self, mock_gen, db):
        """Articles classified as NONE should not create conflict events."""
        db.execute(
            "INSERT INTO articles (id, country_iso3, title, full_text, is_relevant, published_date, pulled_at) "
            "VALUES (1, 'NGA', 'Sports news', 'Football match results...', 1, '2026-04-14', '2026-04-14')"
        )
        db.commit()

        mock_gen.return_value = {
            "classifications": [{
                "article_index": 0,
                "event_category": "NONE",
                "severity": "low",
                "fatalities_mentioned": False,
                "confidence": 0.95,
            }],
            "_meta": {"structured": True},
        }

        config = {"iso3": "NGA", "name": "Nigeria"}
        count = classify_and_store(db, config)
        assert count == 0

    @patch("pipeline.classify.generate_with_tool")
    def test_already_classified_articles_skipped(self, mock_gen, db):
        """Articles that already have conflict_events should not be re-classified."""
        db.execute(
            "INSERT INTO articles (id, country_iso3, title, full_text, is_relevant, published_date, pulled_at) "
            "VALUES (1, 'NGA', 'Test', 'Text', 1, '2026-04-14', '2026-04-14')"
        )
        db.execute(
            "INSERT INTO conflict_events (country_iso3, event_date, event_category, source, source_article_id) "
            "VALUES ('NGA', '2026-04-14', 'ACE', 'internal', 1)"
        )
        db.commit()

        config = {"iso3": "NGA", "name": "Nigeria"}
        count = classify_and_store(db, config)
        assert count == 0
        mock_gen.assert_not_called()
