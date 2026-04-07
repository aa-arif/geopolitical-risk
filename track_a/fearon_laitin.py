"""
Fearon-Laitin structural vulnerability baseline.

Based on: Fearon & Laitin (2003), "Ethnicity, Insurgency, and Civil War,"
American Political Science Review, 97(1): 75-90.

Core structural variables: GDP per capita, population, terrain,
state capacity. These determine which countries are structurally at risk.
"""

import numpy as np


def compute_structural_vulnerability(
    gdp_per_capita: float,
    population_millions: float,
    terrain_ruggedness: str,
    v_dem_liberal_democracy: float,
) -> float:
    """
    Compute structural vulnerability score (0-1 scale).

    Higher = more structurally vulnerable to instability.
    This is a modifier on the PITF base probability, not a
    standalone prediction.
    """
    # Lower GDP per capita = higher vulnerability
    gdp_factor = max(0, 1.0 - (gdp_per_capita / 15000))

    # Larger population = more opportunity for insurgency
    pop_factor = min(1.0, np.log(max(population_millions, 1)) / np.log(200))

    # Rough terrain favors insurgency
    terrain_map = {"low": 0.2, "moderate": 0.5, "high": 0.8, "very_high": 1.0}
    terrain_factor = terrain_map.get(terrain_ruggedness, 0.5)

    # Lower liberal democracy = weaker state capacity
    state_capacity = 1.0 - v_dem_liberal_democracy

    # Weighted combination
    score = (
        0.35 * gdp_factor
        + 0.20 * pop_factor
        + 0.15 * terrain_factor
        + 0.30 * state_capacity
    )

    return float(np.clip(score, 0.0, 1.0))
