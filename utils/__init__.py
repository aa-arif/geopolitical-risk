def risk_level(prob: float) -> str:
    """Classify probability into risk level category."""
    if prob >= 0.5:
        return "CRITICAL"
    elif prob >= 0.3:
        return "HIGH"
    elif prob >= 0.15:
        return "ELEVATED"
    return "LOW"
