"""
Daily pipeline orchestrator.

Runs all system components in order:
1. Ingest new data (ACLED, RSS, GDELT)
2. Run Track A (instant, structural model)
3. Filter and extract articles (Track B, part 1)
4. Run Track B forecasting ensemble (4 agents + supervisor)
5. Fuse and calibrate
6. Log prediction
7. Check for contradictions
8. Evaluate any resolved prediction windows
"""

import json
import sys
from datetime import datetime, timedelta, timezone

from config.settings import (
    load_all_country_configs, load_country_config,
    FUSION_WEIGHT_TRACK_A, EXTREMIZING_PARAMETER,
    PREDICTION_WINDOW_DAYS, PROMPT_VERSIONS, COUNTRIES,
)
from utils.db import (
    get_connection, initialize_db, get_recent_articles,
    get_recent_reasoning_chains, get_acled_summary,
    get_latest_event_date, get_latest_article_id,
    compute_event_threshold, insert_agent_outputs,
)
from utils.logger import logger, compute_data_hash, log_prediction
from utils.api_client import MODELS

from pipeline.ingest import ingest_all
from track_a.predict import predict_track_a
from track_b.filter import is_relevant, compute_reliability_tier
from track_b.extraction import extract_and_store, summarize_reasoning_chains
from track_b.forecasting import run_ensemble
from track_b.supervisor import reconcile
from track_b.contradiction import check_contradiction_heuristic
from fusion.blend import fuse
from fusion.extremize import extremize
from fusion.calibrate import calibrate
from evaluation.brier import brier_score
from evaluation.resolve import resolve_expired_predictions


def run_country(country_name: str) -> dict:
    """
    Run the full prediction pipeline for a single country.

    Returns prediction result dict.
    """
    logger.info("=" * 60)
    logger.info("Starting pipeline for %s", country_name.upper())
    logger.info("=" * 60)

    country_config = load_country_config(country_name)
    iso3 = country_config["iso3"]
    conn = get_connection()

    # --- Step 1: Ingest ---
    logger.info("[1/8] Ingesting data for %s...", iso3)
    ingest_counts = ingest_all(country_config)
    logger.info("Ingestion: ACLED=%d, NewsAPI=%d, RSS=%d, GDELT=%d",
                ingest_counts["acled"], ingest_counts.get("newsapi", 0),
                ingest_counts.get("rss", 0), ingest_counts["gdelt"])

    # --- Compute event threshold ---
    event_threshold = compute_event_threshold(conn, iso3, months=12)
    logger.info("Event threshold (90th pct) for %s: %.0f events/month", iso3, event_threshold)

    # --- Step 2: Track A ---
    logger.info("[2/8] Running Track A (structural model)...")
    track_a = predict_track_a(country_config, conn)
    logger.info("Track A: P=%.3f (PITF=%.3f, vuln=%.3f, neighbor=%.3f, GPR=%.3f)",
                track_a["probability"],
                track_a["components"]["pitf_base"],
                track_a["components"]["structural_vulnerability"],
                track_a["components"]["neighborhood_contagion"],
                track_a["components"]["gpr_trend"])

    # --- Step 3: Filter & Extract ---
    logger.info("[3/8] Filtering and extracting articles...")
    articles = get_recent_articles(conn, iso3, days=7)
    extracted_count = 0

    for article in articles:
        # Skip already-processed articles
        if article.get("is_relevant") is not None:
            continue

        text = article.get("full_text", "") or ""
        title = article.get("title", "") or ""

        relevant = is_relevant(title, text, country_config)

        # Update relevance flag
        conn.execute(
            "UPDATE articles SET is_relevant = ?, reliability_tier = ? WHERE id = ?",
            (relevant, compute_reliability_tier(article.get("url", "")), article["id"]),
        )

        if relevant and len(text) > 100:
            try:
                extract_and_store(article["id"], title + "\n\n" + text,
                                  country_config, conn)
                extracted_count += 1
            except Exception as e:
                logger.warning("Extraction failed for article %d: %s",
                               article["id"], e)

    conn.commit()
    logger.info("Extracted reasoning from %d articles.", extracted_count)

    # --- Step 4: Track B Ensemble ---
    logger.info("[4/8] Running Track B forecasting ensemble...")
    chains = get_recent_reasoning_chains(conn, iso3, days=7)
    reasoning_summary = summarize_reasoning_chains(chains)
    acled_data = get_acled_summary(conn, iso3, days=30)

    ensemble_results = run_ensemble(country_config, track_a, reasoning_summary, acled_data)

    # --- Step 5: Supervisor Reconciliation ---
    logger.info("[5/8] Running supervisor reconciliation...")
    supervisor_result = reconcile(country_config, track_a, ensemble_results)
    track_b_prob = supervisor_result["final_probability"]

    # --- Step 6: Fuse & Calibrate ---
    logger.info("[6/8] Fusing and calibrating...")
    fused_prob = fuse(track_a["probability"], track_b_prob, FUSION_WEIGHT_TRACK_A)
    extremized_prob = extremize(fused_prob, EXTREMIZING_PARAMETER)
    calibrated_prob = calibrate(extremized_prob)

    logger.info("Results: A=%.3f, B=%.3f, Fused=%.3f, Extremized=%.3f, Calibrated=%.3f",
                track_a["probability"], track_b_prob, fused_prob,
                extremized_prob, calibrated_prob)

    # --- Step 7: Log Prediction ---
    logger.info("[7/8] Logging prediction...")
    now = datetime.now(timezone.utc)
    prediction_data = {
        "country_iso3": iso3,
        "prediction_date": now.strftime("%Y-%m-%d"),
        "window_end_date": (now + timedelta(days=PREDICTION_WINDOW_DAYS)).strftime("%Y-%m-%d"),
        "track_a": track_a["probability"],
        "track_b": track_b_prob,
        "fused": fused_prob,
        "extremized": extremized_prob,
        "calibrated": calibrated_prob,
        "reasoning": {
            "track_a_components": track_a["components"],
            "supervisor": supervisor_result.get("narrative_summary", ""),
            "executive_summary": supervisor_result.get("executive_summary", ""),
            "key_risk_factors": supervisor_result.get("key_risk_factors", []),
            "key_stabilizing_factors": supervisor_result.get("key_stabilizing_factors", []),
            "confidence": supervisor_result.get("confidence", "medium"),
        },
        "prompt_versions": PROMPT_VERSIONS,
        "model_versions": MODELS,
        "data_hash": compute_data_hash(
            iso3, acled_data["total_events"], len(articles),
            get_latest_event_date(conn, iso3),
            get_latest_article_id(conn, iso3),
        ),
        "event_threshold": event_threshold,
    }
    prediction_id = log_prediction(conn, prediction_data)

    # Store individual agent outputs
    insert_agent_outputs(conn, prediction_id, iso3, ensemble_results)

    # --- Step 8: Contradiction Check ---
    logger.info("[8/8] Running contradiction check...")
    contradiction = check_contradiction_heuristic(iso3, calibrated_prob, conn)
    if contradiction.get("trigger_reprediction"):
        logger.warning("Contradiction detected for %s: %s",
                        iso3, contradiction["explanation"])

    # --- Evaluate resolved windows ---
    resolve_expired_predictions(conn, iso3)

    conn.close()
    logger.info("Pipeline complete for %s. Final P=%.3f", iso3, calibrated_prob)

    return {
        "country": iso3,
        "calibrated_probability": calibrated_prob,
        "track_a": track_a["probability"],
        "track_b": track_b_prob,
        "confidence": supervisor_result.get("confidence", "medium"),
    }


def run_all():
    """Run the pipeline for all target countries."""
    logger.info("Starting daily pipeline run at %s",
                datetime.now(timezone.utc).isoformat())

    initialize_db()
    results = []

    for country in COUNTRIES:
        try:
            result = run_country(country)
            results.append(result)
        except Exception as e:
            logger.error("Pipeline failed for %s: %s", country, e, exc_info=True)
            results.append({"country": country, "error": str(e)})

    logger.info("Daily pipeline complete. %d/%d countries processed.",
                sum(1 for r in results if "error" not in r), len(COUNTRIES))

    return results


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_country(sys.argv[1])
    else:
        run_all()
