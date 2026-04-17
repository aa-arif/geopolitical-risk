"""
Deterministic tests for _parse_gdelt_date.

Reproduces the April 15-17 2026 production corruption (55% of
conflict_events.event_date stored as 10-char truncations like
'20260414T0') and verifies the parser handles every known input shape.
"""
import sys
sys.path.insert(0, ".")

import re

import pytest

from pipeline.ingest import _parse_gdelt_date


class TestValidInputs:
    def test_full_iso_seendate(self):
        assert _parse_gdelt_date("20260414T010203Z") == "2026-04-14"

    def test_classic_8_char(self):
        assert _parse_gdelt_date("20260414") == "2026-04-14"

    def test_already_iso_prefix(self):
        assert _parse_gdelt_date("2026-04-14") == "2026-04-14"

    def test_already_iso_with_time_suffix(self):
        assert _parse_gdelt_date("2026-04-14T01:02:03Z") == "2026-04-14"

    def test_already_truncated_10_char(self):
        # Reproduces the corruption shape currently in production.
        # Parser must recover the date rather than skip, so the cleanup
        # migration can call this same function on historic rows.
        assert _parse_gdelt_date("20260414T0") == "2026-04-14"

    def test_production_top_10_malformed_values(self):
        cases = {
            "20260414T0": "2026-04-14",
            "20260414T1": "2026-04-14",
            "20260415T0": "2026-04-15",
            "20260415T1": "2026-04-15",
            "20260411T1": "2026-04-11",
            "20260415T2": "2026-04-15",
            "20260416T0": "2026-04-16",
            "20260412T1": "2026-04-12",
            "20260416T1": "2026-04-16",
            "20260413T0": "2026-04-13",
        }
        for raw, expected in cases.items():
            assert _parse_gdelt_date(raw) == expected, f"failed on {raw!r}"


class TestInvalidInputs:
    def test_empty_string(self):
        assert _parse_gdelt_date("") is None

    def test_whitespace_only(self):
        assert _parse_gdelt_date("   ") is None

    def test_none(self):
        assert _parse_gdelt_date(None) is None

    def test_int(self):
        assert _parse_gdelt_date(20260414) is None

    def test_bytes(self):
        assert _parse_gdelt_date(b"20260414") is None

    def test_garbage_text(self):
        assert _parse_gdelt_date("not-a-date") is None

    def test_too_short(self):
        assert _parse_gdelt_date("2026") is None

    def test_impossible_month(self):
        assert _parse_gdelt_date("20261301") is None

    def test_impossible_day(self):
        assert _parse_gdelt_date("20260230") is None

    def test_non_digit_prefix(self):
        assert _parse_gdelt_date("ABCDEFGHT0") is None


class TestOutputShape:
    @pytest.mark.parametrize("raw", [
        "20260414T010203Z",
        "20260414",
        "20260414T0",
        "2026-04-14",
        "2026-04-14T01:02:03Z",
    ])
    def test_output_matches_iso_pattern(self, raw):
        result = _parse_gdelt_date(raw)
        assert result is not None
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", result)
