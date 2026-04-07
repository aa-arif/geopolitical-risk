# TODO: Replace with real Caldara-Iacoviello GPR Index data.
# Currently returns hardcoded placeholder values.
# Real data available at: https://www.matteoiacoviello.com/gpr.htm
# Target: Summer 2026 build phase (Week 8-9)

"""
Geopolitical Risk (GPR) Index trend analysis.

The GPR Index is maintained by Caldara & Iacoviello (Federal Reserve Board).
It measures geopolitical risk based on newspaper articles.

This module fetches or computes a GPR trend score for a country.
For MVP: returns a static estimate based on country config.
Future: integrate with actual GPR data feed.
"""

# Country-level GPR trend estimates (manually curated for MVP)
# Scale: -1.0 (declining risk) to +1.0 (rising risk), 0.0 = stable
# Updated periodically based on GPR index readings
_GPR_TRENDS = {
    "NGA": 0.3,   # Elevated due to security situation, subsidy protests
    "BGD": 0.5,   # Post-crisis political uncertainty
    "PAK": 0.4,   # PTI crackdown, TTP escalation
    "PHL": 0.1,   # Moderate, Marcos-Duterte tension rising
    "TUR": 0.4,   # Imamoglu arrest, opposition crackdown
}


def get_gpr_trend(country_iso3: str) -> float:
    """
    Get the GPR trend score for a country.

    Returns: trend score from -1.0 (declining risk) to +1.0 (rising risk)
    """
    return _GPR_TRENDS.get(country_iso3, 0.0)


def get_gpr_trend_description(country_iso3: str) -> str:
    """Get a human-readable description of the GPR trend."""
    trend = get_gpr_trend(country_iso3)
    if trend > 0.3:
        return "rising significantly"
    elif trend > 0.1:
        return "rising moderately"
    elif trend > -0.1:
        return "stable"
    elif trend > -0.3:
        return "declining moderately"
    else:
        return "declining significantly"
