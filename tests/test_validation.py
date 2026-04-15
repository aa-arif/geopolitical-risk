"""Unit tests for probability validation logic."""
import sys
sys.path.insert(0, ".")

import pytest
from utils.validation import validate_probability, validate_event_type_scores


class TestValidateProbability:
    def test_decimal_passes_through(self):
        result = {"final_probability": 0.35, "_meta": {"structured": True}}
        p = validate_probability(result)
        assert p == pytest.approx(0.35)

    def test_percentage_converted_when_not_structured(self):
        result = {"final_probability": 35.0, "_meta": {"structured": False}}
        p = validate_probability(result)
        assert p == pytest.approx(0.35)

    def test_percentage_not_converted_when_structured(self):
        """Structured output returns floats; 1.5 is a valid probability, not 150%."""
        result = {"final_probability": 1.5, "_meta": {"structured": True}}
        p = validate_probability(result)
        # 1.5 > 0.99, so clamped to 0.99
        assert p == pytest.approx(0.99)

    def test_clamped_low(self):
        result = {"final_probability": -0.5, "_meta": {"structured": True}}
        p = validate_probability(result)
        assert p == pytest.approx(0.01)

    def test_clamped_high(self):
        result = {"final_probability": 1.5, "_meta": {"structured": True}}
        p = validate_probability(result)
        assert p == pytest.approx(0.99)

    def test_missing_probability_uses_default(self):
        result = {"_meta": {"structured": True}}
        p = validate_probability(result)
        assert p == pytest.approx(0.15)
        assert result["final_probability"] == pytest.approx(0.15)

    def test_custom_default(self):
        result = {"_meta": {"structured": True}}
        p = validate_probability(result, default=0.50)
        assert p == pytest.approx(0.50)

    def test_track_a_deviation_guard_clamps_up(self):
        result = {"final_probability": 0.90, "_meta": {"structured": True}}
        p = validate_probability(result, track_a_prob=0.20)
        assert p == pytest.approx(0.70)  # 0.20 + 0.50

    def test_track_a_deviation_guard_clamps_down(self):
        result = {"final_probability": 0.01, "_meta": {"structured": True}}
        p = validate_probability(result, track_a_prob=0.80)
        assert p == pytest.approx(0.30)  # 0.80 - 0.50

    def test_track_a_guard_respects_absolute_clamp(self):
        """Track A guard should not push below 0.01 or above 0.99."""
        result = {"final_probability": 0.01, "_meta": {"structured": True}}
        p = validate_probability(result, track_a_prob=0.02)
        assert p >= 0.01

    def test_no_meta_key_treats_as_unstructured(self):
        result = {"final_probability": 45.0}
        p = validate_probability(result)
        assert p == pytest.approx(0.45)

    def test_writes_back_to_result(self):
        result = {"final_probability": 150.0}
        validate_probability(result)
        assert result["final_probability"] == pytest.approx(0.99)

    def test_custom_key(self):
        result = {"my_prob": 0.42, "_meta": {"structured": True}}
        p = validate_probability(result, key="my_prob")
        assert p == pytest.approx(0.42)


class TestValidateEventTypeScores:
    def test_all_five_types_returned(self):
        result = {
            "event_type_scores": {"ACE": 0.5, "MCU": 0.3},
            "_meta": {"structured": True},
        }
        scores = validate_event_type_scores(result)
        assert set(scores.keys()) == {"ACE", "MCU", "REC", "PSS", "CID"}

    def test_missing_types_get_default(self):
        result = {"event_type_scores": {}, "_meta": {"structured": True}}
        scores = validate_event_type_scores(result)
        assert scores["ACE"] == pytest.approx(0.10)
        assert scores["PSS"] == pytest.approx(0.10)

    def test_no_scores_key_creates_defaults(self):
        result = {"_meta": {"structured": True}}
        scores = validate_event_type_scores(result)
        assert len(scores) == 5
        assert all(v == pytest.approx(0.10) for v in scores.values())

    def test_per_type_clamping(self):
        result = {
            "event_type_scores": {"ACE": 1.5, "MCU": -0.5},
            "_meta": {"structured": True},
        }
        scores = validate_event_type_scores(result)
        assert scores["ACE"] == pytest.approx(0.99)
        assert scores["MCU"] == pytest.approx(0.01)

    def test_per_type_track_a_deviation(self):
        result = {
            "event_type_scores": {"ACE": 0.95},
            "_meta": {"structured": True},
        }
        scores = validate_event_type_scores(result, track_a_prob=0.20)
        assert scores["ACE"] == pytest.approx(0.70)

    def test_percentage_conversion_for_unstructured(self):
        result = {
            "event_type_scores": {"ACE": 35.0, "MCU": 12.0},
        }
        scores = validate_event_type_scores(result)
        assert scores["ACE"] == pytest.approx(0.35)
        assert scores["MCU"] == pytest.approx(0.12)

    def test_writes_back_to_result(self):
        result = {"event_type_scores": {"ACE": 0.5}, "_meta": {"structured": True}}
        validate_event_type_scores(result)
        assert "event_type_scores" in result
        assert len(result["event_type_scores"]) == 5
