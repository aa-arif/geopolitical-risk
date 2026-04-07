"""
Probability extremizing.

Pushes probabilities away from 0.5 based on the finding that
aggregated forecasts tend to be underconfident (Satopaa et al., 2014).

The extremizing parameter d controls how much:
- d = 1.0: no change
- d > 1.0: push away from 0.5 (more extreme)
- d < 1.0: push toward 0.5 (more moderate)
"""


def extremize(p: float, d: float = 1.0) -> float:
    """
    Apply extremizing transformation to a probability.

    Formula: p_ext = p^d / (p^d + (1-p)^d)

    Args:
        p: Input probability (0.01-0.99)
        d: Extremizing parameter (1.0 = no change)

    Returns:
        Extremized probability
    """
    if d == 1.0:
        return p

    p = max(0.01, min(0.99, p))
    p_d = p ** d
    q_d = (1 - p) ** d
    result = p_d / (p_d + q_d)
    return max(0.01, min(0.99, result))
