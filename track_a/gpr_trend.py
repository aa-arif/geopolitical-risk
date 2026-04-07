# Download GPR data from: https://www.matteoiacoviello.com/gpr.htm
# Save the daily country-level CSV as: data/gpr_daily.csv
# Columns: date, GPRC_BRA, GPRC_CHN, ... GPRC_NGA, GPRC_PAK, GPRC_PHL, GPRC_TUR, ...

"""
Geopolitical Risk (GPR) Index trend analysis.

The GPR Index is maintained by Caldara & Iacoviello (Federal Reserve Board).
It measures geopolitical risk based on newspaper articles.

When real GPR data is available (data/gpr_daily.csv), computes a 3-month
trend score. Otherwise falls back to manually curated placeholder values.
"""

import os
from pathlib import Path

from utils.logger import logger

_GPR_DATA_PATH = Path(__file__).parent.parent / "data" / "gpr_daily.csv"

# ISO3 -> GPR column name mapping
_ISO3_TO_GPR_COLUMN = {
    "NGA": "GPRC_NGA",
    "BGD": "GPRC_BGD",
    "PAK": "GPRC_PAK",
    "PHL": "GPRC_PHL",
    "TUR": "GPRC_TUR",
}

# Placeholder values used when CSV is unavailable.
# Scale: -1.0 (declining risk) to +1.0 (rising risk), 0.0 = stable
_FALLBACK_TRENDS = {
    "NGA": 0.3,   # Elevated due to security situation, US tensions
    "BGD": 0.5,   # Post-crisis political uncertainty
    "PAK": 0.4,   # PTI crackdown, TTP escalation
    "PHL": 0.1,   # Moderate, Marcos-Duterte tension rising
    "TUR": 0.4,   # Imamoglu arrest, opposition crackdown
}

# Cache for loaded GPR data
_gpr_cache = None


def _load_gpr_data():
    """Load GPR CSV data into memory. Returns dict of {column: [(date, value)]}."""
    global _gpr_cache
    if _gpr_cache is not None:
        return _gpr_cache

    if not _GPR_DATA_PATH.exists():
        logger.warning(
            "GPR data file not found at %s. Using placeholder values. "
            "Download from https://www.matteoiacoviello.com/gpr.htm",
            _GPR_DATA_PATH,
        )
        _gpr_cache = {}
        return _gpr_cache

    try:
        import csv
        from datetime import datetime

        data = {}
        with open(_GPR_DATA_PATH, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_str = row.get("date", "")
                try:
                    dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
                except ValueError:
                    try:
                        dt = datetime.strptime(date_str.strip(), "%m/%d/%Y")
                    except ValueError:
                        continue

                for col, val in row.items():
                    if col.startswith("GPRC_") and val:
                        try:
                            fval = float(val)
                        except ValueError:
                            continue
                        if col not in data:
                            data[col] = []
                        data[col].append((dt, fval))

        # Sort each series by date
        for col in data:
            data[col].sort(key=lambda x: x[0])

        logger.info("Loaded GPR data: %d columns, latest entries available.", len(data))
        _gpr_cache = data
        return _gpr_cache

    except Exception as e:
        logger.warning("Failed to load GPR data: %s. Using placeholders.", e)
        _gpr_cache = {}
        return _gpr_cache


def _compute_trend_from_data(series, lookback_months=3):
    """
    Compute GPR trend from time series data.

    Compares the most recent month's average to the average from
    lookback_months ago. Returns normalized score:
      0.0 = sharply declining risk narrative
      0.5 = stable
      1.0 = sharply rising risk narrative
    """
    if len(series) < 60:  # Need at least ~2 months of daily data
        return None

    from datetime import timedelta

    latest_date = series[-1][0]
    one_month_ago = latest_date - timedelta(days=30)
    lookback_start = latest_date - timedelta(days=30 * lookback_months)
    lookback_end = latest_date - timedelta(days=30 * (lookback_months - 1))

    recent_vals = [v for d, v in series if d >= one_month_ago]
    past_vals = [v for d, v in series if lookback_start <= d <= lookback_end]

    if not recent_vals or not past_vals:
        return None

    recent_avg = sum(recent_vals) / len(recent_vals)
    past_avg = sum(past_vals) / len(past_vals)

    if past_avg == 0:
        return 0.5

    # Percent change, clamped and normalized to 0-1
    pct_change = (recent_avg - past_avg) / past_avg
    # Clamp to [-1, 1] and map to [0, 1]
    normalized = max(-1.0, min(1.0, pct_change)) * 0.5 + 0.5

    return normalized


def get_gpr_trend(country_iso3: str) -> float:
    """
    Get the GPR trend score for a country.

    Returns: trend score from -1.0 (declining risk) to +1.0 (rising risk)
    on the legacy scale. Internally uses 0-1 if real data is available.
    """
    gpr_data = _load_gpr_data()

    col = _ISO3_TO_GPR_COLUMN.get(country_iso3)
    if col and col in gpr_data:
        trend = _compute_trend_from_data(gpr_data[col])
        if trend is not None:
            # Convert 0-1 normalized score back to -1 to +1 legacy scale
            return (trend - 0.5) * 2.0

    return _FALLBACK_TRENDS.get(country_iso3, 0.0)


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
