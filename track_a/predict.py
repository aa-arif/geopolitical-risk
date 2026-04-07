"""
Track A prediction: combines all structural components into P(A).
No LLM. No API calls. Pure computation. Runs in milliseconds.
"""

import numpy as np

from track_a.pitf_model import compute_pitf_probability
from track_a.fearon_laitin import compute_structural_vulnerability
from track_a.neighborhood import compute_neighborhood_risk
from track_a.gpr_trend import get_gpr_trend, get_gpr_trend_description


def _to_logit(p):
    p = np.clip(p, 0.001, 0.999)
    return np.log(p / (1 - p))


def _from_logit(logit):
    return 1.0 / (1.0 + np.exp(-logit))


def predict_track_a(country_config: dict, db_conn) -> dict:
    """
    Generate Track A structural probability for a country.

    Combines PITF base probability with modifiers in logit space,
    so modifiers can push probability both up AND down.

    Returns dict with probability and all component scores.
    """
    # Core PITF probability
    pitf_prob = compute_pitf_probability(
        polity_code=country_config["polity_code"],
        infant_mortality=country_config["infant_mortality_per_1000"],
        years_stability=country_config["years_since_last_instability"],
    )

    # Structural vulnerability modifier (0-1 scale)
    vulnerability = compute_structural_vulnerability(
        gdp_per_capita=country_config["gdp_per_capita_usd"],
        population_millions=country_config["population_millions"],
        terrain_ruggedness=country_config["terrain_ruggedness"],
        v_dem_liberal_democracy=country_config["v_dem_liberal_democracy_index"],
    )

    # Neighborhood contagion (0-1 scale)
    neighborhood = compute_neighborhood_risk(
        country_iso3=country_config["iso3"],
        neighbors=country_config["neighboring_countries"],
        db_conn=db_conn,
    )

    # GPR trend (0-1 scale where 0.5 = neutral)
    gpr_raw = get_gpr_trend(country_config["iso3"])
    gpr_description = get_gpr_trend_description(country_config["iso3"])

    # Combine in logit space: modifiers are additive adjustments
    base_logit = _to_logit(pitf_prob)

    # Vulnerability: 0.5 = neutral, >0.5 increases risk, <0.5 decreases risk
    vuln_adjustment = (vulnerability - 0.5) * 1.5

    # Neighborhood: 0.0 = no conflict, higher = more conflict nearby
    neighbor_adjustment = neighborhood * 1.0

    # GPR: remap from [-1,1] to [0,1] for the adjustment formula
    # gpr_raw is on [-1, 1] scale; 0.0 = stable
    gpr_normalized = (gpr_raw + 1.0) / 2.0  # map to 0-1
    gpr_adjustment = (gpr_normalized - 0.5) * 0.8

    combined_logit = (
        base_logit + vuln_adjustment + neighbor_adjustment + gpr_adjustment
    )
    combined = float(np.clip(_from_logit(combined_logit), 0.02, 0.95))

    return {
        "probability": combined,
        "components": {
            "pitf_base": pitf_prob,
            "structural_vulnerability": vulnerability,
            "neighborhood_contagion": neighborhood,
            "gpr_trend": gpr_raw,
            "gpr_trend_description": gpr_description,
        },
    }
