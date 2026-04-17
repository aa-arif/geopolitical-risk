"""
Deterministic tests for _parse_publication_date.

Covers every known input shape across ingest sources:
  - GDELT (seendate): full ISO, classic 8-char, already-truncated 10-char
  - NewsAPI / general ISO 8601
  - RSS (RFC 2822 via feedparser)

Reproduces both production corruption patterns:
  - 2026-04-15..17: 10-char truncations like '20260414T0' (GDELT)
  - Multiple dates: 10-char truncations like 'Mon, 16 Ma' (RSS RFC 2822)
"""
import sys
sys.path.insert(0, ".")

import re

import pytest

from pipeline.ingest import _parse_publication_date


class TestValidInputs:
    def test_full_iso_seendate(self):
        assert _parse_publication_date("20260414T010203Z") == "2026-04-14"

    def test_classic_8_char(self):
        assert _parse_publication_date("20260414") == "2026-04-14"

    def test_already_iso_prefix(self):
        assert _parse_publication_date("2026-04-14") == "2026-04-14"

    def test_already_iso_with_time_suffix(self):
        assert _parse_publication_date("2026-04-14T01:02:03Z") == "2026-04-14"

    def test_already_truncated_10_char(self):
        # Reproduces the GDELT corruption shape currently in production.
        # Parser must recover the date rather than skip, so the cleanup
        # migration can call this same function on historic rows.
        assert _parse_publication_date("20260414T0") == "2026-04-14"

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
            assert _parse_publication_date(raw) == expected, f"failed on {raw!r}"


class TestRFC2822:
    def test_standard_rfc2822(self):
        assert _parse_publication_date("Mon, 16 May 2026 10:23:45 GMT") == "2026-05-16"

    def test_rfc2822_no_seconds(self):
        assert _parse_publication_date("Fri, 10 Apr 2026 14:30 -0400") == "2026-04-10"

    def test_rfc2822_single_digit_day(self):
        assert _parse_publication_date("Wed, 8 Apr 2026 12:00:00 GMT") == "2026-04-08"

    def test_rfc2822_truncated_unsalvageable(self):
        # The 10-char truncation we see in production. Parser cannot
        # recover the year or full month from this, so must return None
        # and defer to article-lookup migration path.
        assert _parse_publication_date("Mon, 16 Ma") is None


class TestInvalidInputs:
    def test_empty_string(self):
        assert _parse_publication_date("") is None

    def test_whitespace_only(self):
        assert _parse_publication_date("   ") is None

    def test_none(self):
        assert _parse_publication_date(None) is None

    def test_int(self):
        assert _parse_publication_date(20260414) is None

    def test_bytes(self):
        assert _parse_publication_date(b"20260414") is None

    def test_garbage_text(self):
        assert _parse_publication_date("not-a-date") is None

    def test_too_short(self):
        assert _parse_publication_date("2026") is None

    def test_impossible_month(self):
        assert _parse_publication_date("20261301") is None

    def test_impossible_day(self):
        assert _parse_publication_date("20260230") is None

    def test_non_digit_prefix(self):
        assert _parse_publication_date("ABCDEFGHT0") is None


class TestOutputShape:
    @pytest.mark.parametrize("raw", [
        "20260414T010203Z",
        "20260414",
        "20260414T0",
        "2026-04-14",
        "2026-04-14T01:02:03Z",
        "Mon, 16 May 2026 10:23:45 GMT",
    ])
    def test_output_matches_iso_pattern(self, raw):
        result = _parse_publication_date(raw)
        assert result is not None
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", result)
