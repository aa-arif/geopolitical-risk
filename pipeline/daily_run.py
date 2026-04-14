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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from config.settings import (
    load_all_country_configs, load_country_config,
    FUSION_WEIGHT_TRACK_A, EXTREMIZING_PARAMETER,
    PREDICTION_WINDOW_DAYS, PROMPT_VERSIONS, COUNTRIES,
    DATA_DIR,
)
from utils.db import (
    get_connection, initialize_db, get_recent_articles,
    get_recent_reasoning_chains, get_acled_summary,
    get_latest_event_date, get_latest_article_id,
    compute_event_threshold, insert_agent_outputs,
    get_resolved_predictions,
)
from utils.logger import logger, compute_data_hash, log_prediction
from utils.api_client import MODELS

from pipeline.ingest import ingest_all, ingest_gdelt, ingest_gdelt_events
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


def run_country(country_name: str, skip_gdelt: bool = False) -> dict:
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
    try:
        # --- Step 1: Ingest ---
        logger.info("[1/8] Ingesting data for %s...", iso3)
        ingest_counts = ingest_all(country_config, skip_gdelt=skip_gdelt)
        logger.info("Ingestion: ACLED=%d, NewsAPI=%d, RSS=%d, GDELT_art=%d, GDELT_ev=%d",
                    ingest_counts["acled"], ingest_counts.get("newsapi", 0),
                    ingest_counts.get("rss", 0),
                    ingest_counts.get("gdelt_articles", 0),
                    ingest_counts.get("gdelt_events", 0))

        # --- Seed articles if cold start ---
        article_count = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE country_iso3 = ? AND LENGTH(full_text) > 200",
            (iso3,)
        ).fetchone()[0]
        if article_count < 3:
            logger.info("Cold start for %s (%d articles). Running supplementary ingestion...", iso3, article_count)
            from pipeline.ingest import ingest_newsapi, ingest_gdelt
            seed_config = dict(country_config)
            extra_news = ingest_newsapi(seed_config, days=14)
            extra_gdelt = ingest_gdelt(seed_config, days=14)
            logger.info("Supplementary ingestion for %s: NewsAPI=%d, GDELT=%d", iso3, extra_news, extra_gdelt)

        # --- Compute event threshold ---
        # Committed at prediction time using only pre-prediction data to avoid look-ahead.
        # Uses only violent events (battles, explosions, violence against civilians) with
        # fatalities, decoupled from the broader ACLED data used as model input.
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        event_threshold = compute_event_threshold(conn, iso3, months=12, before_date=today)
        logger.info("Event threshold (90th pct violent events) for %s: %.0f events/month", iso3, event_threshold)

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
            if article.get("is_relevant") is not None:
                continue

            text = article.get("full_text", "") or ""
            title = article.get("title", "") or ""

            relevant = is_relevant(title, text, country_config)

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

        # --- Learning feedback from resolved predictions ---
        prompt_feedback = ""
        try:
            from evaluation.learning import generate_prompt_feedback
            prompt_feedback = generate_prompt_feedback(conn)
        except Exception:
            pass

        # --- Step 4: Track B Ensemble ---
        logger.info("[4/8] Running Track B forecasting ensemble...")
        chains = get_recent_reasoning_chains(conn, iso3, days=7)
        if chains:
            reasoning_summary = summarize_reasoning_chains(chains)
        else:
            reasoning_summary = (
                f"No recent articles available. Country context: "
                f"{country_config['risk_context']}"
            )
            logger.info("No reasoning chains for %s, using country context as fallback.", iso3)
        acled_data = get_acled_summary(conn, iso3, days=30)

        if prompt_feedback:
            reasoning_summary = prompt_feedback + "\n" + reasoning_summary

        ensemble_results = run_ensemble(country_config, track_a, reasoning_summary, acled_data)

        # --- Step 5: Supervisor Reconciliation ---
        logger.info("[5/8] Running supervisor reconciliation...")
        supervisor_result = reconcile(country_config, track_a, ensemble_results,
                                       reasoning_summary=reasoning_summary,
                                       acled_data=acled_data)
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
        now = datetime.now().astimezone()
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
            try:
                conn.execute(
                    """INSERT INTO change_alerts
                       (country_iso3, alert_date, previous_probability,
                        current_probability, delta, alert_text)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (iso3, datetime.now().astimezone().strftime("%Y-%m-%d"),
                     calibrated_prob, calibrated_prob, 0.0,
                     f"CONTRADICTION: {contradiction['explanation']}"),
                )
                conn.commit()
            except Exception as e:
                logger.warning("Failed to store contradiction alert: %s", e)

        # --- Evaluate resolved windows ---
        resolve_expired_predictions(conn, iso3)

        logger.info("Pipeline complete for %s. Final P=%.3f", iso3, calibrated_prob)

        return {
            "country": iso3,
            "calibrated_probability": calibrated_prob,
            "track_a": track_a["probability"],
            "track_b": track_b_prob,
            "confidence": supervisor_result.get("confidence", "medium"),
        }
    finally:
        conn.close()


def run_all():
    """
    Run the pipeline for all target countries.
    Phase 1: Sequential GDELT pre-seeding (avoids rate limiting).
    Phase 2: Sequential pipeline for all countries (GDELT skipped).
    """
    logger.info("Starting daily pipeline run at %s (%d countries)",
                datetime.now().astimezone().isoformat(), len(COUNTRIES))

    initialize_db()

    # --- Phase 1: Sequential GDELT pre-seeding ---
    # Checkpoint: skip countries that already have today's GDELT data (resume after crash)
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    conn_check = get_connection()
    already_seeded = set()
    for country_name in COUNTRIES:
        cfg = load_country_config(country_name)
        iso3 = cfg["iso3"]
        row = conn_check.execute(
            "SELECT COUNT(*) as n FROM articles WHERE country_iso3 = ? AND pulled_at >= ?",
            (iso3, today),
        ).fetchone()
        if row["n"] > 0:
            already_seeded.add(country_name)
    conn_check.close()

    remaining = [c for c in COUNTRIES if c not in already_seeded]
    if already_seeded:
        logger.info("Phase 1: Skipping %d already-seeded countries, fetching %d remaining...",
                    len(already_seeded), len(remaining))
    else:
        logger.info("Phase 1: Sequential GDELT ingestion for %d countries...", len(COUNTRIES))

    for country_name in remaining:
        try:
            cfg = load_country_config(country_name)
            g_art = ingest_gdelt(cfg, days=7)
            g_ev = ingest_gdelt_events(cfg, days=7)
            logger.info("GDELT pre-seed %s: articles=%d, events=%d",
                        cfg["iso3"], g_art, g_ev)
        except Exception as e:
            logger.warning("GDELT pre-seed failed for %s: %s", country_name, e)

    # --- Phase 2: Sequential pipeline (GDELT skipped, already pre-seeded) ---
    # Runs sequentially to respect Anthropic API rate limits.
    # Each country makes ~5 LLM calls (3 parallel agents + supervisor + extraction).
    logger.info("Phase 2: Sequential pipeline for %d countries (GDELT skipped)...",
                len(COUNTRIES))
    results = []

    for country in COUNTRIES:
        try:
            result = run_country(country, skip_gdelt=True)
            results.append(result)
        except Exception as e:
            logger.error("Pipeline failed for %s: %s", country, e, exc_info=True)
            results.append({"country": country, "error": str(e)})

    logger.info("Daily pipeline complete. %d/%d countries processed.",
                sum(1 for r in results if "error" not in r), len(COUNTRIES))

    # --- Phase 3: Change detection ---
    try:
        from pipeline.change_detection import detect_all_changes, store_alerts
        conn = get_connection()
        changes = detect_all_changes(conn, threshold_pct=2.0)
        store_alerts(conn, changes)
        sig = [c for c in changes if c["is_significant"]]
        if sig:
            logger.info("Significant changes detected: %d countries",  len(sig))
            for c in sig:
                logger.info("  %s: %+.1fpp (%s)", c["country_iso3"], c["delta_pct"], c["direction"])
        conn.close()
    except Exception as e:
        logger.warning("Change detection failed: %s", e)

    # --- Phase 4: Adaptive weight update ---
    try:
        from evaluation.weight_updater import compute_updated_weight
        conn = get_connection()
        update_result = compute_updated_weight(conn, FUSION_WEIGHT_TRACK_A)
        if update_result["direction"] != "unchanged":
            new_weight = update_result["new_weight"]
            import json as json_mod
            weight_file = DATA_DIR / "fusion_weight_override.json"
            weight_file.write_text(json_mod.dumps({
                "fusion_weight_track_a": new_weight,
                "updated_at": datetime.now().astimezone().isoformat(),
                "reasoning": update_result["reasoning"],
            }))
            logger.info("Fusion weight updated: %.2f -> %.2f (%s)",
                        FUSION_WEIGHT_TRACK_A, new_weight, update_result["direction"])
        conn.close()
    except Exception as e:
        logger.warning("Weight update failed: %s", e)

    # --- Phase 5: Calibration model update ---
    try:
        from fusion.calibrate import fit_calibration_model
        conn = get_connection()
        resolved = get_resolved_predictions(conn)
        if resolved:
            preds = [r["calibrated_probability"] for r in resolved if r["actual_outcome"] is not None]
            actuals = [r["actual_outcome"] for r in resolved if r["actual_outcome"] is not None]
            if len(preds) >= 30:
                fit_calibration_model(preds, actuals)
                logger.info("Calibration model updated with %d resolved predictions", len(preds))
        conn.close()
    except Exception as e:
        logger.warning("Calibration update failed: %s", e)

    return results


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            run_country(sys.argv[1])
        else:
            run_all()
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user.")
        sys.exit(1)
    except Exception as e:
        logger.error("Pipeline crashed: %s", e, exc_info=True)
        sys.exit(1)
