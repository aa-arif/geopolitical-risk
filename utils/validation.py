"""
Shared probability validation for LLM forecast outputs.

Handles percentage-to-decimal conversion, clamping, Track A deviation
guard, and missing field defaults. Used by both forecasting agents
and the supervisor reconciliation.
"""


def validate_probability(result: dict, track_a_prob: float = None,
                          max_deviation: float = 0.50,
                          key: str = "final_probability",
                          default: float = 0.15) -> float:
    """
    Validate and normalize a probability value from LLM output.

    Handles:
    1. Missing value -> default fallback
    2. Percentage-to-decimal conversion (35.0 -> 0.35) for free-text fallback
    3. Clamping to [0.01, 0.99]
    4. Track A deviation guard (optional)

    Args:
        result: LLM result dict (modified in place for backward compat)
        track_a_prob: If set, clamp within max_deviation of Track A
        max_deviation: Maximum allowed deviation from Track A (default 0.50)
        key: Key to read/write probability from result dict
        default: Fallback value if key is missing

    Returns:
        Validated probability value (also written back to result[key])
    """
    if key not in result:
        result[key] = default

    p = result[key]

    # With tool_use, the model returns a float directly.
    # But if fallback to free-text parsing occurred, the model may have
    # returned percentage (e.g. 23.0) instead of decimal (0.23).
    # Only convert if clearly a percentage (> 1.0) AND not structured output.
    if not result.get("_meta", {}).get("structured", False) and p > 1.0:
        p = p / 100.0

    p = max(0.01, min(0.99, p))

    # Clamp to within max_deviation of Track A if available
    if track_a_prob is not None:
        p = max(track_a_prob - max_deviation,
                min(track_a_prob + max_deviation, p))
        p = max(0.01, min(0.99, p))

    result[key] = p
    return p


def validate_event_type_scores(result: dict, track_a_prob: float = None,
                                 max_deviation: float = 0.50) -> dict:
    """
    Validate per-event-type probability scores from LLM output.

    Expects result to contain an 'event_type_scores' dict like:
    {"ACE": 0.35, "MCU": 0.12, "REC": 0.05, "PSS": 0.20, "CID": 0.10}

    Each score is validated independently. Missing scores default to 0.10.

    Returns:
        Validated event_type_scores dict
    """
    scores = result.get("event_type_scores", {})
    expected_types = ["ACE", "MCU", "REC", "PSS", "CID"]

    validated = {}
    for et in expected_types:
        p = scores.get(et, 0.10)

        # Percentage-to-decimal for free-text fallback
        if not result.get("_meta", {}).get("structured", False) and p > 1.0:
            p = p / 100.0

        p = max(0.01, min(0.99, p))

        if track_a_prob is not None:
            p = max(track_a_prob - max_deviation,
                    min(track_a_prob + max_deviation, p))
            p = max(0.01, min(0.99, p))

        validated[et] = p

    result["event_type_scores"] = validated
    return validated
