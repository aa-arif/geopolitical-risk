"""Unit tests for fusion layer."""
import sys
sys.path.insert(0, ".")

import pytest
from fusion.blend import fuse
from fusion.extremize import extremize


class TestFuse:
    def test_equal_weights(self):
        assert fuse(0.3, 0.7, weight=0.5) == pytest.approx(0.5, abs=0.01)

    def test_track_a_dominant(self):
        result = fuse(0.2, 0.8, weight=0.9)
        assert result == pytest.approx(0.26, abs=0.01)

    def test_output_clamped(self):
        assert fuse(0.0, 0.0, weight=0.5) >= 0.01
        assert fuse(1.0, 1.0, weight=0.5) <= 0.99

    def test_default_weight_favors_track_a(self):
        result = fuse(0.1, 0.9)  # default weight=0.6
        assert result < 0.5


class TestExtremize:
    def test_no_change_at_d1(self):
        assert extremize(0.3, d=1.0) == pytest.approx(0.3, abs=0.001)

    def test_pushes_away_from_half(self):
        p = 0.7
        extremized = extremize(p, d=2.0)
        assert extremized > p

    def test_low_prob_gets_lower(self):
        p = 0.2
        extremized = extremize(p, d=2.0)
        assert extremized < p

    def test_half_unchanged(self):
        assert extremize(0.5, d=2.0) == pytest.approx(0.5, abs=0.001)

    def test_output_clamped(self):
        assert extremize(0.99, d=5.0) <= 0.99
        assert extremize(0.01, d=5.0) >= 0.01
