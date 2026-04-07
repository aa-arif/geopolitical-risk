"""
Prediction logging with full provenance tracking.
Every prediction is logged with prompt versions, model versions,
data snapshot hash, and timestamps for full reproducibility.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone

# Configure module logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("georisk")


def compute_data_hash(country_iso3: str, acled_count: int,
                      article_count: int, latest_event_date: str = "",
                      latest_article_id: int = 0) -> str:
    """Compute a hash of the data state at prediction time."""
    data_str = (
        f"{country_iso3}:{acled_count}:{article_count}:"
        f"{latest_event_date}:{latest_article_id}:"
        f"{datetime.now(timezone.utc).date()}"
    )
    return hashlib.sha256(data_str.encode()).hexdigest()[:16]


def log_prediction(db_conn, prediction_data: dict):
    """Log a complete prediction with all metadata."""
    db_conn.execute(
        """INSERT INTO predictions
        (country_iso3, prediction_date, window_end_date,
         track_a_probability, track_b_probability, fused_probability,
         extremized_probability, calibrated_probability,
         reasoning_summary, prompt_versions_json, model_versions_json,
         data_snapshot_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            prediction_data["country_iso3"],
            prediction_data["prediction_date"],
            prediction_data["window_end_date"],
            prediction_data["track_a"],
            prediction_data["track_b"],
            prediction_data["fused"],
            prediction_data["extremized"],
            prediction_data["calibrated"],
            json.dumps(prediction_data["reasoning"]),
            json.dumps(prediction_data["prompt_versions"]),
            json.dumps(prediction_data["model_versions"]),
            prediction_data["data_hash"],
        ),
    )
    db_conn.commit()
    logger.info(
        "Prediction logged: %s P=%.3f (A=%.3f, B=%.3f, fused=%.3f)",
        prediction_data["country_iso3"],
        prediction_data["calibrated"],
        prediction_data["track_a"],
        prediction_data["track_b"],
        prediction_data["fused"],
    )
