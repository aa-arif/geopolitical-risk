"""Unit tests for evaluation modules."""
import sys
sys.path.insert(0, ".")

import pytest
from evaluation.brier import brier_score, brier_score_aggregate
from evaluation.decompose import decompose_brier


class TestBrierScore:
    def test_perfect_prediction_event(self):
        assert brier_score(1.0, 1) == pytest.approx(0.0)

    def test_perfect_prediction_nonevent(self):
        assert brier_score(0.0, 0) == pytest.approx(0.0)

    def test_worst_prediction(self):
        assert brier_score(0.0, 1) == pytest.approx(1.0)
        assert brier_score(1.0, 0) == pytest.approx(1.0)

    def test_moderate_prediction(self):
        assert brier_score(0.7, 1) == pytest.approx(0.09)

    def test_aggregate_empty(self):
        assert brier_score_aggregate([], []) == 0.0

    def test_aggregate_multiple(self):
        preds = [0.9, 0.1, 0.5]
        actuals = [1, 0, 1]
        expected = (0.01 + 0.01 + 0.25) / 3
        assert brier_score_aggregate(preds, actuals) == pytest.approx(expected)


class TestBrierDecomposition:
    def test_decomposition_sums_correctly(self):
        preds = [0.1, 0.2, 0.3, 0.8, 0.9, 0.7, 0.15, 0.85, 0.5, 0.6]
        actuals = [0, 0, 0, 1, 1, 1, 0, 1, 0, 1]
        result = decompose_brier(preds, actuals)
        expected_total = result["uncertainty"] - result["resolution"] + result["reliability"]
        assert result["total"] == pytest.approx(expected_total, abs=0.01)

    def test_empty_input(self):
        result = decompose_brier([], [])
        assert result["total"] == 0
