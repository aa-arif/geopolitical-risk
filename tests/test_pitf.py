"""Unit tests for Track A PITF model."""
import sys
sys.path.insert(0, ".")

import pytest
from track_a.pitf_model import compute_pitf_probability
from track_a.fearon_laitin import compute_structural_vulnerability


class TestPITF:
    def test_factionalized_democracy_highest_risk(self):
        """Factionalized partial democracies (polity 0 to +5) should have highest risk."""
        p_factionalized = compute_pitf_probability(polity_code=4, infant_mortality=70, years_stability=2)
        p_democracy = compute_pitf_probability(polity_code=8, infant_mortality=70, years_stability=2)
        p_autocracy = compute_pitf_probability(polity_code=-8, infant_mortality=70, years_stability=2)
        assert p_factionalized > p_democracy
        assert p_factionalized > p_autocracy

    def test_higher_infant_mortality_increases_risk(self):
        p_low_im = compute_pitf_probability(polity_code=4, infant_mortality=20, years_stability=5)
        p_high_im = compute_pitf_probability(polity_code=4, infant_mortality=100, years_stability=5)
        assert p_high_im > p_low_im

    def test_stability_reduces_risk(self):
        p_recent = compute_pitf_probability(polity_code=4, infant_mortality=70, years_stability=1)
        p_stable = compute_pitf_probability(polity_code=4, infant_mortality=70, years_stability=15)
        assert p_stable < p_recent

    def test_output_range(self):
        p = compute_pitf_probability(polity_code=0, infant_mortality=150, years_stability=0)
        assert 0.01 <= p <= 0.99

    def test_democracy_low_im_stable_is_low_risk(self):
        p = compute_pitf_probability(polity_code=9, infant_mortality=5, years_stability=20)
        assert p < 0.10


class TestStructuralVulnerability:
    def test_poor_large_rough_autocracy_is_high_risk(self):
        score = compute_structural_vulnerability(
            gdp_per_capita=500, population_millions=200,
            terrain_ruggedness="very_high", v_dem_liberal_democracy=0.1
        )
        assert score > 0.7

    def test_rich_small_flat_democracy_is_low_risk(self):
        score = compute_structural_vulnerability(
            gdp_per_capita=50000, population_millions=5,
            terrain_ruggedness="low", v_dem_liberal_democracy=0.9
        )
        assert score < 0.3

    def test_output_range(self):
        score = compute_structural_vulnerability(
            gdp_per_capita=2000, population_millions=100,
            terrain_ruggedness="moderate", v_dem_liberal_democracy=0.3
        )
        assert 0.0 <= score <= 1.0
