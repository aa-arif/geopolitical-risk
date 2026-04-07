"""
Adaptive fusion weight updater.

Adjusts the Track A vs Track B weight based on historical performance:
- If Track A consistently outperforms fused: increase w toward Track A
- If fused outperforms Track A: keep w
- If Track B alone outperforms Track A: decrease w (give Track B more weight)

Updates are conservative (max 0.05 per evaluation cycle) to prevent oscillation.
"""

from evaluation.track_comparison import compare_tracks
from utils.logger import logger

MAX_WEIGHT_CHANGE = 0.05
MIN_WEIGHT = 0.3
MAX_WEIGHT = 0.8
MIN_RESOLVED_FOR_UPDATE = 10


def compute_updated_weight(db_conn, current_weight: float,
                            country_iso3: str = None) -> dict:
    """
    Compute an updated fusion weight based on track performance.

    Args:
        db_conn: Database connection
        current_weight: Current Track A weight (0.0-1.0)
        country_iso3: Optional filter by country

    Returns:
        Dict with new_weight, direction, and reasoning
    """
    comparison = compare_tracks(db_conn, country_iso3)

    if comparison["n_resolved"] < MIN_RESOLVED_FOR_UPDATE:
        return {
            "new_weight": current_weight,
            "direction": "unchanged",
            "reasoning": (
                f"Insufficient resolved predictions ({comparison['n_resolved']}) "
                f"for weight update. Need {MIN_RESOLVED_FOR_UPDATE}."
            ),
        }

    a_brier = comparison["track_a_brier"]
    b_brier = comparison["track_b_brier"]
    f_brier = comparison["fused_brier"]

    new_weight = current_weight
    direction = "unchanged"
    reasoning = ""

    # If Track A alone beats fused, increase weight toward A
    if a_brier < f_brier:
        new_weight = min(MAX_WEIGHT, current_weight + MAX_WEIGHT_CHANGE)
        direction = "increase_track_a"
        reasoning = (
            f"Track A (Brier={a_brier:.4f}) outperforms fused ({f_brier:.4f}). "
            f"Increasing Track A weight."
        )

    # If Track B alone beats Track A, decrease weight (give B more influence)
    elif b_brier < a_brier:
        new_weight = max(MIN_WEIGHT, current_weight - MAX_WEIGHT_CHANGE)
        direction = "increase_track_b"
        reasoning = (
            f"Track B (Brier={b_brier:.4f}) outperforms Track A ({a_brier:.4f}). "
            f"Increasing Track B weight."
        )

    # Fused is best: keep current weight
    else:
        reasoning = (
            f"Fused (Brier={f_brier:.4f}) is best. "
            f"Keeping current weight at {current_weight:.2f}."
        )

    logger.info(
        "Weight update: %.2f -> %.2f (%s). A=%.4f, B=%.4f, F=%.4f",
        current_weight, new_weight, direction, a_brier, b_brier, f_brier,
    )

    return {
        "new_weight": new_weight,
        "direction": direction,
        "reasoning": reasoning,
        "comparison": comparison,
    }
