"""
Track A prediction: combines all structural components into P(A).
No LLM. No API calls. Pure computation. Runs in milliseconds.
"""

from track_a.pitf_model import compute_pitf_probability
from track_a.fearon_laitin import compute_structural_vulnerability
from track_a.neighborhood import compute_neighborhood_risk
from track_a.gpr_trend import get_gpr_trend, get_gpr_trend_description


def predict_track_a(country_config: dict, db_conn) -> dict:
    """
    Generate Track A structural probability for a country.

    Returns dict with probability and all component scores.
    """
    # Core PITF probability
    pitf_prob = compute_pitf_probability(
        polity_code=country_config["polity_code"],
        infant_mortality=country_config["infant_mortality_per_1000"],
        years_stability=country_config["years_since_last_instability"],
    )

    # Structural vulnerability modifier
    vulnerability = compute_structural_vulnerability(
        gdp_per_capita=country_config["gdp_per_capita_usd"],
        population_millions=country_config["population_millions"],
        terrain_ruggedness=country_config["terrain_ruggedness"],
        v_dem_liberal_democracy=country_config["v_dem_liberal_democracy_index"],
    )

    # Neighborhood contagion
    neighborhood = compute_neighborhood_risk(
        country_iso3=country_config["iso3"],
        neighbors=country_config["neighboring_countries"],
        db_conn=db_conn,
    )

    # GPR trend
    gpr = get_gpr_trend(country_config["iso3"])
    gpr_description = get_gpr_trend_description(country_config["iso3"])

    # Combine: PITF is the backbone, others are modifiers
    combined = pitf_prob * (
        1.0 + 0.3 * vulnerability + 0.2 * neighborhood + 0.1 * gpr
    )
    combined = min(0.95, max(0.02, combined))

    return {
        "probability": combined,
        "components": {
            "pitf_base": pitf_prob,
            "structural_vulnerability": vulnerability,
            "neighborhood_contagion": neighborhood,
            "gpr_trend": gpr,
            "gpr_trend_description": gpr_description,
        },
    }
