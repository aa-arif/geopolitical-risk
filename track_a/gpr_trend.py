# Download GPR data from: https://www.matteoiacoviello.com/gpr.htm
# Save the monthly Excel file as: data/gpr_monthly.xlsx
# The file contains country-specific GPR columns (GPRC_XXX format).

"""
Geopolitical Risk (GPR) Index trend analysis.

The GPR Index is maintained by Caldara & Iacoviello (Federal Reserve Board).
It measures geopolitical risk based on newspaper articles.

When real GPR data is available (data/gpr_monthly.xlsx), computes a 3-month
trend score. Otherwise falls back to manually curated placeholder values.
"""

from pathlib import Path

from utils.logger import logger

_GPR_DATA_PATH = Path(__file__).parent.parent / "data" / "gpr_monthly.xlsx"

# ISO3 -> possible GPR column name patterns (will be matched flexibly)
_ISO3_TO_GPR_PATTERNS = {
    "NGA": ["GPRC_NGA", "GPRC_Nigeria", "Nigeria"],
    "BGD": ["GPRC_BGD", "GPRC_Bangladesh", "Bangladesh"],
    "PAK": ["GPRC_PAK", "GPRC_Pakistan", "Pakistan"],
    "PHL": ["GPRC_PHL", "GPRC_Philippines", "Philippines"],
    "TUR": ["GPRC_TUR", "GPRC_Turkey", "Turkey"],
}

# Placeholder values used when Excel is unavailable.
# Scale: -1.0 (declining risk) to +1.0 (rising risk), 0.0 = stable
_FALLBACK_TRENDS = {
    "NGA": 0.3,   # Elevated due to security situation, US tensions
    "BGD": 0.5,   # Post-crisis political uncertainty
    "PAK": 0.4,   # PTI crackdown, TTP escalation
    "PHL": 0.1,   # Moderate, Marcos-Duterte tension rising
    "TUR": 0.4,   # Imamoglu arrest, opposition crackdown
}

# Cache for loaded GPR data: {iso3: [(datetime, value), ...]}
_gpr_cache = None
_gpr_columns_found = None


def _find_column(columns, patterns):
    """Find the matching column name from a list of patterns."""
    for pattern in patterns:
        for col in columns:
            if pattern.lower() == col.lower() or pattern.lower() in col.lower():
                return col
    return None


def _load_gpr_data():
    """Load GPR Excel data into memory using pandas."""
    global _gpr_cache, _gpr_columns_found
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
        import pandas as pd

        df = pd.read_excel(_GPR_DATA_PATH)
        logger.info("GPR Excel loaded: %d rows, %d columns", len(df), len(df.columns))
        logger.info("GPR columns: %s", list(df.columns))

        # Find the date column
        date_col = None
        for candidate in ["date", "Date", "DATE", "Month", "month", "MONTH"]:
            if candidate in df.columns:
                date_col = candidate
                break
        if date_col is None:
            # Try first column
            date_col = df.columns[0]
            logger.info("Using first column as date: %s", date_col)

        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col]).sort_values(date_col)

        # Build per-country series
        data = {}
        _gpr_columns_found = {}
        for iso3, patterns in _ISO3_TO_GPR_PATTERNS.items():
            col = _find_column(df.columns.tolist(), patterns)
            if col:
                series = df[[date_col, col]].dropna()
                data[iso3] = list(zip(series[date_col].tolist(),
                                       series[col].astype(float).tolist()))
                _gpr_columns_found[iso3] = col
                logger.info("GPR: %s -> column '%s' (%d data points)", iso3, col, len(data[iso3]))
            else:
                logger.warning("GPR: No column found for %s (tried %s)", iso3, patterns)

        _gpr_cache = data
        return _gpr_cache

    except Exception as e:
        logger.warning("Failed to load GPR Excel: %s. Using placeholders.", e)
        _gpr_cache = {}
        return _gpr_cache


def _compute_trend_from_data(series, lookback_months=3):
    """
    Compute GPR trend from time series data.

    Compares the most recent month's value to the average from
    lookback_months ago. Returns normalized score:
      0.0 = sharply declining risk narrative
      0.5 = stable
      1.0 = sharply rising risk narrative
    """
    if len(series) < lookback_months + 1:
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
    normalized = max(-1.0, min(1.0, pct_change)) * 0.5 + 0.5

    return normalized


def get_gpr_trend(country_iso3: str) -> float:
    """
    Get the GPR trend score for a country.

    Returns: trend score from -1.0 (declining risk) to +1.0 (rising risk).
    """
    gpr_data = _load_gpr_data()

    if country_iso3 in gpr_data and gpr_data[country_iso3]:
        trend = _compute_trend_from_data(gpr_data[country_iso3])
        if trend is not None:
            # Convert 0-1 normalized score to -1 to +1 legacy scale
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
