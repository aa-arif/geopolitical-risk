"""
Track A + Track B probability blending.
Simple weighted average with configurable weight.
"""


def fuse(p_a: float, p_b: float, weight: float = 0.6) -> float:
    """
    Blend Track A and Track B probabilities.

    Args:
        p_a: Track A (structural) probability
        p_b: Track B (LLM ensemble) probability
        weight: Weight for Track A (0.0-1.0). Default 0.6 favors Track A.

    Returns:
        Fused probability
    """
    fused = weight * p_a + (1 - weight) * p_b
    return max(0.01, min(0.99, fused))
