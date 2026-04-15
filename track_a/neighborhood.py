"""
Neighborhood contagion risk based on PITF finding that armed conflict
in neighboring countries is a significant predictor of instability.

Queries the unified conflict_events table (source-agnostic).
"""

import numpy as np


def compute_neighborhood_risk(country_iso3: str, neighbors: list,
                                db_conn, lookback_days: int = 90) -> float:
    """
    Compute risk from neighboring country conflict activity.

    Queries the unified conflict_events table for ACE (armed conflict)
    events in bordering countries over the lookback period.

    Returns: contagion risk score (0.0 to 1.0)
    """
    if not neighbors:
        return 0.0

    placeholders = ",".join(["?" for _ in neighbors])
    date_offset = f"-{int(lookback_days)} days"
    query = f"""
        SELECT country_iso3, COUNT(*) as event_count
        FROM conflict_events
        WHERE country_iso3 IN ({placeholders})
        AND event_date >= date('now', ?)
        AND event_category = 'ACE'
        GROUP BY country_iso3
    """
    params = list(neighbors) + [date_offset]
    cursor = db_conn.execute(query, params)
    results = cursor.fetchall()

    if not results:
        return 0.0

    # More active neighbors = higher contagion risk
    active_neighbors = len(results)
    total_events = sum(r["event_count"] for r in results)

    # Normalize: if more than half of neighbors have active conflict
    # with significant event counts, risk is high
    neighbor_ratio = active_neighbors / len(neighbors)
    event_intensity = min(1.0, total_events / (len(neighbors) * 50))

    return float(np.clip(0.6 * neighbor_ratio + 0.4 * event_intensity, 0.0, 1.0))
