"""Tests for email delivery and subscriber matching."""
import sys
sys.path.insert(0, ".")

import sqlite3
import pytest
from unittest.mock import patch

from utils.db import (
    insert_subscriber, get_active_subscribers,
    get_matched_subscribers, delete_subscriber,
)
from pipeline.email_delivery import (
    _markdown_to_html, _build_alert_email_html, deliver_alerts,
)


@pytest.fixture
def db():
    """In-memory SQLite with subscribers + alerts + briefs tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            name TEXT, organization TEXT,
            countries TEXT, event_types TEXT, sectors TEXT,
            tier TEXT DEFAULT 'free',
            active BOOLEAN DEFAULT TRUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
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
            headline TEXT, body_markdown TEXT,
            event_type_scores TEXT, key_drivers TEXT,
            sector_implications TEXT, source_prediction_ids TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    return conn


class TestSubscriberMatching:
    def test_all_countries_all_types_matches_everything(self, db):
        insert_subscriber(db, "all@test.com", countries=["all"], event_types=["all"])
        matched = get_matched_subscribers(db, "IRN", "ACE")
        assert len(matched) == 1

    def test_specific_country_matches(self, db):
        insert_subscriber(db, "iran@test.com", countries=["IRN"], event_types=["all"])
        assert len(get_matched_subscribers(db, "IRN", "ACE")) == 1
        assert len(get_matched_subscribers(db, "NGA", "ACE")) == 0

    def test_specific_event_type_matches(self, db):
        insert_subscriber(db, "ace@test.com", countries=["all"], event_types=["ACE"])
        assert len(get_matched_subscribers(db, "IRN", "ACE")) == 1
        assert len(get_matched_subscribers(db, "IRN", "MCU")) == 0

    def test_combined_filter(self, db):
        insert_subscriber(db, "gulf@test.com",
                           countries=["IRN", "IRQ", "SAU"],
                           event_types=["ACE", "CID"])
        assert len(get_matched_subscribers(db, "IRN", "ACE")) == 1
        assert len(get_matched_subscribers(db, "IRN", "MCU")) == 0
        assert len(get_matched_subscribers(db, "NGA", "ACE")) == 0

    def test_deactivated_subscriber_not_matched(self, db):
        sid = insert_subscriber(db, "gone@test.com", countries=["all"], event_types=["all"])
        delete_subscriber(db, sid)
        assert len(get_matched_subscribers(db, "IRN", "ACE")) == 0

    def test_multiple_subscribers_matched(self, db):
        insert_subscriber(db, "a@test.com", countries=["all"], event_types=["all"])
        insert_subscriber(db, "b@test.com", countries=["IRN"], event_types=["ACE"])
        assert len(get_matched_subscribers(db, "IRN", "ACE")) == 2

    def test_duplicate_email_raises(self, db):
        insert_subscriber(db, "dup@test.com")
        with pytest.raises(Exception):
            insert_subscriber(db, "dup@test.com")


class TestMarkdownToHtml:
    def test_converts_headers(self):
        html = _markdown_to_html("# Title\n## Subtitle")
        assert "<h1" in html
        assert "<h2" in html
        assert "Title" in html

    def test_converts_bold(self):
        html = _markdown_to_html("This is **bold** text")
        assert "<strong>bold</strong>" in html

    def test_converts_list_items(self):
        html = _markdown_to_html("- Item 1\n- Item 2")
        assert "<li" in html
        assert "Item 1" in html


class TestBuildAlertEmail:
    def test_produces_valid_html(self):
        alert = {
            "country_iso3": "IRN",
            "event_type": "ACE",
            "trigger_type": "delta_spike",
            "previous_probability": 0.45,
            "current_probability": 0.60,
            "delta": 0.15,
        }
        html = _build_alert_email_html(alert, "## Brief\nTest brief body", "Iran")
        assert "Iran" in html
        assert "Armed Conflict" in html
        assert "+15.0pp" in html
        assert "Precursion" in html
        assert "<!DOCTYPE html>" in html

    def test_handles_missing_brief(self):
        alert = {
            "country_iso3": "NGA",
            "event_type": "MCU",
            "trigger_type": "threshold_cross",
            "previous_probability": 0.18,
            "current_probability": 0.25,
            "delta": 0.07,
        }
        html = _build_alert_email_html(alert, "", "Nigeria")
        assert "Nigeria" in html


class TestDeliverAlerts:
    def test_no_api_key_returns_zero(self, db):
        """Without RESEND_API_KEY, deliver_alerts skips gracefully."""
        alerts = [{
            "country_iso3": "IRN",
            "event_type": "ACE",
            "trigger_type": "delta_spike",
            "previous_probability": 0.40,
            "current_probability": 0.55,
            "delta": 0.15,
        }]
        insert_subscriber(db, "test@test.com", countries=["all"], event_types=["all"])
        sent = deliver_alerts(db, alerts)
        assert sent == 0  # no API key

    def test_no_alerts_returns_zero(self, db):
        sent = deliver_alerts(db, [])
        assert sent == 0

    def test_no_matching_subscribers_returns_zero(self, db):
        """Alerts with no matching subscribers should not attempt delivery."""
        insert_subscriber(db, "japan@test.com", countries=["JPN"], event_types=["all"])
        alerts = [{
            "country_iso3": "IRN",
            "event_type": "ACE",
            "trigger_type": "delta_spike",
            "previous_probability": 0.40,
            "current_probability": 0.55,
            "delta": 0.15,
        }]
        sent = deliver_alerts(db, alerts)
        assert sent == 0

    @patch("pipeline.email_delivery.RESEND_API_KEY", "test-key")
    @patch("pipeline.email_delivery.send_alert_email")
    def test_sends_to_matched_subscribers(self, mock_send, db):
        """With API key + matching subscriber, should call send_alert_email."""
        mock_send.return_value = True

        insert_subscriber(db, "gulf@test.com", countries=["IRN"], event_types=["ACE"])
        alerts = [{
            "country_iso3": "IRN",
            "event_type": "ACE",
            "trigger_type": "delta_spike",
            "previous_probability": 0.40,
            "current_probability": 0.55,
            "delta": 0.15,
        }]

        sent = deliver_alerts(db, alerts)
        assert sent == 1
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][0] == "gulf@test.com"
        assert "Iran" in call_args[0][1] or "IRN" in call_args[0][1]
