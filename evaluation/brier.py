"""
Brier score computation for probabilistic forecast evaluation.
Brier score = (predicted - actual)^2
Lower is better. Perfect = 0.0, worst = 1.0.
"""


def brier_score(predicted: float, actual: int) -> float:
    """
    Compute Brier score for a single prediction.

    Args:
        predicted: Predicted probability (0.0-1.0)
        actual: Actual outcome (0 or 1)

    Returns:
        Brier score (0.0-1.0)
    """
    return (predicted - actual) ** 2


def brier_score_aggregate(predictions: list, actuals: list) -> float:
    """
    Compute mean Brier score over multiple predictions.

    Args:
        predictions: List of predicted probabilities
        actuals: List of actual outcomes (0 or 1)

    Returns:
        Mean Brier score
    """
    if not predictions:
        return 0.0
    return sum(brier_score(p, a) for p, a in zip(predictions, actuals)) / len(predictions)
