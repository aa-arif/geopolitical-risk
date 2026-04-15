"""
Prediction logging with full provenance tracking.
Every prediction is logged with prompt versions, model versions,
data snapshot hash, and timestamps for full reproducibility.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

# Configure module logger with both console and file handlers
LOG_DIR = Path(__file__).parent.parent / "data" / "pipeline_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("georisk")
logger.setLevel(logging.INFO)

# Console handler
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(console_handler)

    # File handler (daily rotation by filename)
    log_file = LOG_DIR / f"pipeline_{datetime.now().astimezone().strftime('%Y-%m-%d')}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(file_handler)


def compute_data_hash(country_iso3: str, acled_count: int,
                      article_count: int, latest_event_date: str = "",
                      latest_article_id: int = 0) -> str:
    """Compute a hash of the data state at prediction time."""
    data_str = (
        f"{country_iso3}:{acled_count}:{article_count}:"
        f"{latest_event_date}:{latest_article_id}:"
        f"{datetime.now().astimezone().date()}"
    )
    return hashlib.sha256(data_str.encode()).hexdigest()[:16]


def log_prediction(db_conn, prediction_data: dict) -> int:
    """Log a complete prediction with all metadata. Returns prediction ID."""
    cursor = db_conn.execute(
        """INSERT OR REPLACE INTO predictions
        (country_iso3, prediction_date, window_end_date, event_type,
         track_a_probability, track_b_probability, fused_probability,
         extremized_probability, calibrated_probability,
         reasoning_summary, prompt_versions_json, model_versions_json,
         data_snapshot_hash, event_threshold)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            prediction_data["country_iso3"],
            prediction_data["prediction_date"],
            prediction_data["window_end_date"],
            prediction_data.get("event_type", "composite"),
            prediction_data["track_a"],
            prediction_data["track_b"],
            prediction_data["fused"],
            prediction_data["extremized"],
            prediction_data["calibrated"],
            json.dumps(prediction_data["reasoning"]),
            json.dumps(prediction_data.get("prompt_versions", {})),
            json.dumps(prediction_data.get("model_versions", {})),
            prediction_data.get("data_hash", ""),
            prediction_data.get("event_threshold"),
        ),
    )
    db_conn.commit()
    prediction_id = cursor.lastrowid

    logger.info(
        "Prediction logged (id=%d): %s/%s P=%.3f (A=%.3f, B=%.3f, fused=%.3f)",
        prediction_id,
        prediction_data["country_iso3"],
        prediction_data.get("event_type", "composite"),
        prediction_data["calibrated"],
        prediction_data["track_a"],
        prediction_data["track_b"],
        prediction_data["fused"],
    )
    return prediction_id
