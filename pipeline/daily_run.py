"""
Daily pipeline orchestrator.

Pipeline steps are composable functions that take and return a context dict.
Each step is independently testable and re-runnable.

Steps:
1. Ingest new data (GDELT, RSS, NewsAPI)
2. Run Track A (instant, structural model)
3. Filter and extract articles (Track B, part 1)
4. Classify articles into event types (LLM)
5. Run Track B forecasting ensemble (4 agents + supervisor)
6. Fuse and calibrate (per event type)
7. Log predictions (6 rows: 5 types + 1 composite)
8. Check for contradictions
9. Evaluate any resolved prediction windows
"""

import json
import sqlite3
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
    get_recent_reasoning_chains, get_event_summary,
    get_latest_event_date, get_latest_article_id,
    compute_event_threshold, compute_event_threshold_by_category,
    insert_agent_outputs, get_resolved_predictions,
)
from utils.logger import logger, compute_data_hash, log_prediction
from utils.api_client import MODELS

from pipeline.ingest import ingest_all, ingest_gdelt, ingest_gdelt_events
from pipeline.classify import classify_and_store
from track_a.predict import predict_track_a
from track_b.filter import is_relevant, compute_reliability_tier
from track_b.extraction import extract_and_store, summarize_reasoning_chains
from track_b.forecasting import run_ensemble
from track_b.supervisor import reconcile
from track_b.contradiction import check_contradiction_heuristic
from pipeline.alerts import detect_changes_for_country, store_alerts
from pipeline.brief_generator import generate_daily_brief
from pipeline.email_delivery import deliver_alerts
from fusion.blend import fuse
from fusion.extremize import extremize
from fusion.calibrate import calibrate
from evaluation.resolve import resolve_expired_predictions


# --- Pipeline Step Functions ---
# Each takes a context dict and returns it (mutated).
# Context keys are documented at each step.

def step_ingest(ctx):
    """Step 1: Ingest data from all sources.

    When ingestion_done=True in context (set by run_subset Phase 1),
    skips ingestion entirely to avoid SQLite write contention during
    parallel execution.
    """
    conn, config, iso3 = ctx["conn"], ctx["config"], ctx["iso3"]
    skip_gdelt = ctx.get("skip_gdelt", False)

    if ctx.get("ingestion_done"):
        logger.info("[1/12] Ingestion already done for %s (Phase 1).", iso3)
        ctx["ingest_counts"] = {}
        return ctx

    logger.info("[1/12] Ingesting data for %s...", iso3)
    ingest_counts = ingest_all(config, skip_gdelt=skip_gdelt)
    logger.info("Ingestion: NewsAPI=%d, RSS=%d, GDELT_art=%d, GDELT_ev=%d",
                ingest_counts.get("newsapi", 0), ingest_counts.get("rss", 0),
                ingest_counts.get("gdelt_articles", 0),
                ingest_counts.get("gdelt_events", 0))

    # Seed articles if cold start
    article_count = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE country_iso3 = ? AND LENGTH(full_text) > 200",
        (iso3,)
    ).fetchone()[0]
    if article_count < 3:
        logger.info("Cold start for %s (%d articles). Running supplementary ingestion...",
                     iso3, article_count)
        from pipeline.ingest import ingest_newsapi
        extra_news = ingest_newsapi(config, days=14)
        extra_gdelt = ingest_gdelt(config, days=14) if not skip_gdelt else 0
        logger.info("Supplementary ingestion: NewsAPI=%d, GDELT=%d", extra_news, extra_gdelt)

    ctx["ingest_counts"] = ingest_counts
    return ctx


def step_track_a(ctx):
    """Step 2: Run Track A structural model."""
    conn, config, iso3 = ctx["conn"], ctx["config"], ctx["iso3"]

    logger.info("[2/9] Running Track A (structural model)...")
    track_a = predict_track_a(config, conn)
    logger.info("Track A: P=%.3f (PITF=%.3f, vuln=%.3f, neighbor=%.3f, GPR=%.3f)",
                track_a["probability"],
                track_a["components"]["pitf_base"],
                track_a["components"]["structural_vulnerability"],
                track_a["components"]["neighborhood_contagion"],
                track_a["components"]["gpr_trend"])

    # Compute event thresholds committed at prediction time (no look-ahead).
    # Composite threshold (V1 backward compat) + per-type thresholds.
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    event_threshold = compute_event_threshold(conn, iso3, months=12, before_date=today)

    from config.settings import EVENT_TYPE_RESOLUTION, CALIBRATED_EVENT_TYPES
    event_type_thresholds = {}
    for et in CALIBRATED_EVENT_TYPES:
        cfg = EVENT_TYPE_RESOLUTION[et]
        if cfg["threshold_method"] == "percentile_90":
            event_type_thresholds[et] = compute_event_threshold_by_category(
                conn, iso3, et,
                require_fatalities=cfg.get("require_fatalities", False),
                months=12, before_date=today,
            )
        elif cfg["threshold_method"] == "occurrence":
            event_type_thresholds[et] = 0  # any occurrence counts
        else:
            event_type_thresholds[et] = None

    logger.info("Event thresholds for %s: composite=%.0f, %s",
                iso3, event_threshold,
                ", ".join(f"{k}={v:.0f}" for k, v in event_type_thresholds.items() if v is not None))

    ctx["track_a"] = track_a
    ctx["event_threshold"] = event_threshold
    ctx["event_type_thresholds"] = event_type_thresholds
    return ctx


def step_filter_extract(ctx):
    """Step 3: Filter articles for relevance and extract reasoning chains."""
    conn, config, iso3 = ctx["conn"], ctx["config"], ctx["iso3"]

    logger.info("[3/9] Filtering and extracting articles...")
    articles = get_recent_articles(conn, iso3, days=7)
    extracted_count = 0

    for article in articles:
        if article.get("is_relevant") is not None:
            continue

        text = article.get("full_text", "") or ""
        title = article.get("title", "") or ""
        relevant = is_relevant(title, text, config)

        conn.execute(
            "UPDATE articles SET is_relevant = ?, reliability_tier = ? WHERE id = ?",
            (relevant, compute_reliability_tier(article.get("url", "")), article["id"]),
        )

        if relevant and len(text) > 100:
            try:
                extract_and_store(article["id"], title + "\n\n" + text, config, conn)
                extracted_count += 1
            except Exception as e:
                logger.warning("Extraction failed for article %d: %s", article["id"], e)

    conn.commit()
    logger.info("Extracted reasoning from %d articles.", extracted_count)

    ctx["articles"] = articles
    ctx["extracted_count"] = extracted_count
    return ctx


def step_classify(ctx):
    """Step 4: Classify articles into event types using LLM."""
    conn, config, iso3 = ctx["conn"], ctx["config"], ctx["iso3"]

    logger.info("[4/9] Classifying articles into event types...")
    try:
        classified_count = classify_and_store(conn, config)
        logger.info("Classified articles -> %d conflict events.", classified_count)
        ctx["classified_count"] = classified_count
    except Exception as e:
        logger.warning("Article classification failed for %s: %s", iso3, e)
        ctx["classified_count"] = 0

    return ctx


def step_track_b(ctx):
    """Step 5: Run Track B forecasting ensemble + supervisor reconciliation."""
    conn, config, iso3 = ctx["conn"], ctx["config"], ctx["iso3"]
    track_a = ctx["track_a"]

    logger.info("[5/9] Running Track B forecasting ensemble...")

    # Build reasoning summary
    chains = get_recent_reasoning_chains(conn, iso3, days=7)
    if chains:
        reasoning_summary = summarize_reasoning_chains(chains)
    else:
        reasoning_summary = (
            f"No recent articles available. Country context: "
            f"{config['risk_context']}"
        )
        logger.info("No reasoning chains for %s, using country context as fallback.", iso3)

    # Learning feedback from resolved predictions
    prompt_feedback = ""
    try:
        from evaluation.learning import generate_prompt_feedback
        prompt_feedback = generate_prompt_feedback(conn)
    except Exception:
        pass

    if prompt_feedback:
        reasoning_summary = prompt_feedback + "\n" + reasoning_summary

    event_data = get_event_summary(conn, iso3, days=30)

    # Run ensemble
    ensemble_results = run_ensemble(config, track_a, reasoning_summary, event_data)

    # Supervisor reconciliation
    logger.info("[5b/9] Running supervisor reconciliation...")
    supervisor_result = reconcile(config, track_a, ensemble_results,
                                   reasoning_summary=reasoning_summary,
                                   acled_data=event_data)

    ctx["reasoning_summary"] = reasoning_summary
    ctx["event_data"] = event_data
    ctx["ensemble_results"] = ensemble_results
    ctx["supervisor_result"] = supervisor_result
    ctx["track_b_prob"] = supervisor_result["final_probability"]
    return ctx


def step_fuse(ctx):
    """Step 6: Fuse Track A and Track B, extremize, calibrate.

    Produces:
    - Composite score (fused Track A + Track B composite)
    - Per-event-type scores (composite Track A as shared prior + supervisor per-type)
    """
    track_a_prob = ctx["track_a"]["probability"]
    track_b_prob = ctx["track_b_prob"]
    supervisor_result = ctx["supervisor_result"]

    logger.info("[6/9] Fusing and calibrating...")

    # Composite fusion (same as V1)
    fused_prob = fuse(track_a_prob, track_b_prob, FUSION_WEIGHT_TRACK_A)
    extremized_prob = extremize(fused_prob, EXTREMIZING_PARAMETER)
    calibrated_prob = calibrate(extremized_prob)

    logger.info("Composite: A=%.3f, B=%.3f, Fused=%.3f, Calibrated=%.3f",
                track_a_prob, track_b_prob, fused_prob, calibrated_prob)

    # Per-event-type fusion: composite Track A as shared prior (Arch Issue 3A)
    event_type_scores = supervisor_result.get("event_type_scores", {})
    fused_event_types = {}
    for et in ["ACE", "MCU", "REC", "PSS", "CID"]:
        et_track_b = event_type_scores.get(et, 0.10)
        et_fused = fuse(track_a_prob, et_track_b, FUSION_WEIGHT_TRACK_A)
        et_extremized = extremize(et_fused, EXTREMIZING_PARAMETER)
        et_calibrated = calibrate(et_extremized)
        fused_event_types[et] = {
            "track_b": et_track_b,
            "fused": et_fused,
            "extremized": et_extremized,
            "calibrated": et_calibrated,
        }

    logger.info("Event types: %s",
                ", ".join(f"{et}={v['calibrated']:.3f}" for et, v in fused_event_types.items()))

    ctx["fused_prob"] = fused_prob
    ctx["extremized_prob"] = extremized_prob
    ctx["calibrated_prob"] = calibrated_prob
    ctx["fused_event_types"] = fused_event_types
    return ctx


def step_log(ctx):
    """Step 7: Log predictions to database.

    Stores 6 rows per country:
    - 1 composite prediction (backward compatible with V1)
    - 5 event-type predictions (ACE, MCU, REC, PSS, CID)
    """
    conn, iso3 = ctx["conn"], ctx["iso3"]
    track_a = ctx["track_a"]
    supervisor_result = ctx["supervisor_result"]
    event_data = ctx["event_data"]
    articles = ctx.get("articles", [])
    fused_event_types = ctx.get("fused_event_types", {})

    logger.info("[7/9] Logging predictions (1 composite + 5 event types)...")
    now = datetime.now().astimezone()
    pred_date = now.strftime("%Y-%m-%d")
    window_end = (now + timedelta(days=PREDICTION_WINDOW_DAYS)).strftime("%Y-%m-%d")

    data_hash = compute_data_hash(
        iso3, event_data["total_events"], len(articles),
        get_latest_event_date(conn, iso3),
        get_latest_article_id(conn, iso3),
    )

    reasoning = {
        "track_a_components": track_a["components"],
        "supervisor": supervisor_result.get("narrative_summary", ""),
        "executive_summary": supervisor_result.get("executive_summary", ""),
        "key_risk_factors": supervisor_result.get("key_risk_factors", []),
        "key_stabilizing_factors": supervisor_result.get("key_stabilizing_factors", []),
        "confidence": supervisor_result.get("confidence", "medium"),
        "event_type_scores": supervisor_result.get("event_type_scores", {}),
        "event_type_drivers": supervisor_result.get("event_type_drivers", {}),
    }

    # --- Composite prediction (V1 backward compat) ---
    composite_data = {
        "country_iso3": iso3,
        "prediction_date": pred_date,
        "window_end_date": window_end,
        "event_type": "composite",
        "track_a": track_a["probability"],
        "track_b": ctx["track_b_prob"],
        "fused": ctx["fused_prob"],
        "extremized": ctx["extremized_prob"],
        "calibrated": ctx["calibrated_prob"],
        "reasoning": reasoning,
        "prompt_versions": PROMPT_VERSIONS,
        "model_versions": MODELS,
        "data_hash": data_hash,
        "event_threshold": ctx["event_threshold"],
    }
    prediction_id = log_prediction(conn, composite_data)

    # Store individual agent outputs (linked to composite prediction)
    insert_agent_outputs(conn, prediction_id, iso3, ctx["ensemble_results"])

    # --- Per-event-type predictions ---
    et_drivers = supervisor_result.get("event_type_drivers", {})
    et_thresholds = ctx.get("event_type_thresholds", {})
    for et, scores in fused_event_types.items():
        et_data = {
            "country_iso3": iso3,
            "prediction_date": pred_date,
            "window_end_date": window_end,
            "event_type": et,
            "track_a": track_a["probability"],  # composite Track A as shared prior
            "track_b": scores["track_b"],
            "fused": scores["fused"],
            "extremized": scores["extremized"],
            "calibrated": scores["calibrated"],
            "reasoning": {
                "driver": et_drivers.get(et, ""),
                "confidence": reasoning["confidence"],
            },
            "data_hash": data_hash,
            "event_threshold": et_thresholds.get(et),
        }
        log_prediction(conn, et_data)

    ctx["prediction_id"] = prediction_id
    logger.info("Logged 6 predictions for %s (composite + 5 event types).", iso3)
    return ctx


def step_detect_alerts(ctx):
    """Step 8: Detect significant changes across event types."""
    conn, iso3 = ctx["conn"], ctx["iso3"]

    logger.info("[8/11] Detecting significant changes...")
    try:
        triggers = detect_changes_for_country(conn, iso3)
        if triggers:
            store_alerts(conn, triggers)
            logger.info("Detected %d alerts for %s.", len(triggers), iso3)
            for t in triggers:
                logger.info("  %s/%s: %s (%+.1f%%)",
                            iso3, t["event_type"], t["trigger_type"],
                            t["delta"] * 100)
        ctx["alert_triggers"] = triggers
    except Exception as e:
        logger.warning("Alert detection failed for %s: %s", iso3, e)
        ctx["alert_triggers"] = []

    return ctx


def step_brief(ctx):
    """Step 9: Generate daily brief for the country."""
    conn, iso3 = ctx["conn"], ctx["iso3"]
    config = ctx["config"]
    supervisor_result = ctx["supervisor_result"]
    fused_event_types = ctx.get("fused_event_types", {})

    logger.info("[9/11] Generating daily brief...")

    # Build calibrated event-type scores
    et_scores = {et: scores["calibrated"]
                  for et, scores in fused_event_types.items()}

    # Format alerts as text for the brief
    from pipeline.alerts import _format_trigger_text
    alert_texts = [_format_trigger_text(t) for t in ctx.get("alert_triggers", [])]

    # Reasoning from supervisor
    reasoning = {
        "supervisor": supervisor_result.get("narrative_summary", ""),
        "executive_summary": supervisor_result.get("executive_summary", ""),
        "key_risk_factors": supervisor_result.get("key_risk_factors", []),
        "key_stabilizing_factors": supervisor_result.get("key_stabilizing_factors", []),
        "confidence": supervisor_result.get("confidence", "medium"),
        "event_type_drivers": supervisor_result.get("event_type_drivers", {}),
    }

    try:
        brief = generate_daily_brief(
            conn, iso3, config["name"],
            et_scores, reasoning, alert_texts,
        )
        ctx["brief"] = brief
        logger.info("Brief: %s", brief.get("headline", "")[:80])
    except Exception as e:
        logger.warning("Brief generation failed for %s: %s", iso3, e)
        ctx["brief"] = None

    return ctx


def step_contradiction(ctx):
    """Step 10: Check for data contradictions."""
    conn, iso3 = ctx["conn"], ctx["iso3"]
    calibrated_prob = ctx["calibrated_prob"]

    logger.info("[10/11] Running contradiction check...")
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
        except sqlite3.OperationalError as e:
            logger.warning("Failed to store contradiction alert: %s", e)

    ctx["contradiction"] = contradiction
    return ctx


def step_send_alerts(ctx):
    """Step 11: Send alert emails to matched subscribers."""
    conn = ctx["conn"]
    triggers = ctx.get("alert_triggers", [])

    if not triggers:
        return ctx

    logger.info("[11/12] Sending alert emails...")
    try:
        sent = deliver_alerts(conn, triggers)
        ctx["emails_sent"] = sent
        if sent:
            logger.info("Sent %d alert emails.", sent)
    except Exception as e:
        logger.warning("Email delivery failed: %s", e)
        ctx["emails_sent"] = 0

    return ctx


def step_resolve(ctx):
    """Step 12: Evaluate any resolved prediction windows."""
    conn, iso3 = ctx["conn"], ctx["iso3"]

    logger.info("[12/12] Evaluating resolved predictions...")
    resolved = resolve_expired_predictions(conn, iso3)
    ctx["resolved"] = resolved
    return ctx


# --- Orchestrators ---

def run_country(country_name: str, skip_gdelt: bool = False,
                ingestion_done: bool = False) -> dict:
    """
    Run the full prediction pipeline for a single country.

    Args:
        skip_gdelt: Skip GDELT fetching (already pre-seeded)
        ingestion_done: Skip all ingestion (Phase 1 of run_subset already did it)

    Executes pipeline steps in sequence, passing a shared context dict.
    Returns prediction result dict.
    """
    logger.info("=" * 60)
    logger.info("Starting pipeline for %s", country_name.upper())
    logger.info("=" * 60)

    config = load_country_config(country_name)
    conn = get_connection()

    ctx = {
        "conn": conn,
        "config": config,
        "iso3": config["iso3"],
        "skip_gdelt": skip_gdelt,
        "ingestion_done": ingestion_done,
    }

    try:
        ctx = step_ingest(ctx)
        ctx = step_track_a(ctx)
        ctx = step_filter_extract(ctx)
        ctx = step_classify(ctx)
        ctx = step_track_b(ctx)
        ctx = step_fuse(ctx)
        ctx = step_log(ctx)
        ctx = step_detect_alerts(ctx)
        ctx = step_brief(ctx)
        ctx = step_contradiction(ctx)
        ctx = step_send_alerts(ctx)
        ctx = step_resolve(ctx)

        logger.info("Pipeline complete for %s. Final P=%.3f",
                     ctx["iso3"], ctx["calibrated_prob"])

        return {
            "country": ctx["iso3"],
            "calibrated_probability": ctx["calibrated_prob"],
            "track_a": ctx["track_a"]["probability"],
            "track_b": ctx["track_b_prob"],
            "confidence": ctx["supervisor_result"].get("confidence", "medium"),
        }
    finally:
        conn.close()


def run_all():
    """
    Run the pipeline for all target countries.
    Phase 1: Sequential GDELT pre-seeding (avoids rate limiting).
    Phase 2: Parallel pipeline for countries (3 workers).
    Phase 3-5: Post-pipeline maintenance.
    """
    logger.info("Starting daily pipeline run at %s (%d countries)",
                datetime.now().astimezone().isoformat(), len(COUNTRIES))

    initialize_db()

    # --- Phase 1: Sequential GDELT pre-seeding ---
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
        logger.info("Phase 1: Skipping %d already-seeded, fetching %d remaining...",
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

    # --- Phase 2: Sequential pipeline (SQLite write contention) ---
    # SQLite can't handle parallel writers across threads. Run countries
    # sequentially; within each country, agents 1-3 parallelize LLM calls.
    # Migrate to PostgreSQL post-YC to unlock cross-country parallelism.
    logger.info("Phase 2: Sequential pipeline for %d countries (GDELT done)...",
                len(COUNTRIES))
    results = []

    for country in COUNTRIES:
        try:
            result = run_country(country, skip_gdelt=True, ingestion_done=True)
            results.append(result)
        except Exception as e:
            logger.error("Pipeline failed for %s: %s", country, e, exc_info=True)
            results.append({"country": country, "error": str(e)})

    logger.info("Daily pipeline complete. %d/%d countries processed.",
                sum(1 for r in results if "error" not in r), len(COUNTRIES))

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
        logger.warning("Weight update failed: %s", e, exc_info=True)

    # --- Phase 5: Calibration model update ---
    try:
        from fusion.calibrate import fit_calibration_model
        conn = get_connection()
        resolved = get_resolved_predictions(conn)
        if resolved:
            preds = [r["extremized_probability"] for r in resolved if r["actual_outcome"] is not None]
            actuals = [r["actual_outcome"] for r in resolved if r["actual_outcome"] is not None]
            if len(preds) >= 30:
                fit_calibration_model(preds, actuals)
                logger.info("Calibration model updated with %d resolved predictions", len(preds))
        conn.close()
    except Exception as e:
        logger.warning("Calibration update failed: %s", e, exc_info=True)

    # --- Phase 6: Vertical briefs ---
    try:
        from pipeline.vertical_brief import generate_vertical_brief
        from config.settings import VERTICALS
        conn = get_connection()
        for v_name in VERTICALS:
            brief = generate_vertical_brief(conn, v_name)
            if brief:
                logger.info("Vertical brief '%s': %s", v_name,
                            brief.get("corridor_headline", "")[:80])
        conn.close()
    except Exception as e:
        logger.warning("Vertical brief generation failed: %s", e, exc_info=True)

    # --- Phase 7: Daily email brief ---
    try:
        from pipeline.email_brief import send_daily_brief
        sent = send_daily_brief()
        if sent:
            logger.info("Daily email brief sent to %d recipients.", sent)
    except Exception as e:
        logger.warning("Daily email brief failed: %s", e, exc_info=True)

    return results


def run_subset(country_names: list):
    """
    Run the pipeline for a subset of countries.

    Phase 1: Sequential ingestion (SQLite can't handle parallel writes)
    Phase 2: Parallel LLM-heavy pipeline (skip_gdelt=True, ingestion done)
    """
    logger.info("Starting pipeline for %d countries: %s",
                len(country_names), ", ".join(country_names))

    initialize_db()

    # --- Phase 1: Sequential ingestion for all countries ---
    # SQLite serializes writes; running ingestion in parallel causes "database is locked".
    # Ingestion is I/O-bound (HTTP fetches), not LLM-bound, so sequential is fine.
    logger.info("Phase 1: Sequential ingestion for %d countries...", len(country_names))
    for country_name in country_names:
        try:
            cfg = load_country_config(country_name)
            iso3 = cfg["iso3"]
            logger.info("Ingesting %s (%s)...", cfg["name"], iso3)
            ingest_counts = ingest_all(cfg, skip_gdelt=False)
            logger.info("  %s: NewsAPI=%d, RSS=%d, GDELT_art=%d, GDELT_ev=%d",
                        iso3, ingest_counts.get("newsapi", 0),
                        ingest_counts.get("rss", 0),
                        ingest_counts.get("gdelt_articles", 0),
                        ingest_counts.get("gdelt_events", 0))
        except Exception as e:
            logger.warning("Ingestion failed for %s: %s", country_name, e)

    # --- Phase 2: Sequential pipeline (SQLite can't handle parallel writes) ---
    # SQLite serializes all writes. Even with WAL mode, concurrent writers from
    # different threads cause "database is locked" during extraction, classification,
    # and prediction logging. Run sequentially until PostgreSQL migration (post-YC).
    # Within each country, agents 1-3 still run in parallel (LLM calls, not DB writes).
    logger.info("Phase 2: Sequential pipeline for %d countries (ingestion done)...",
                len(country_names))
    results = []

    for c in country_names:
        try:
            result = run_country(c, skip_gdelt=True, ingestion_done=True)
            results.append(result)
        except Exception as e:
            logger.error("Pipeline failed for %s: %s", c, e, exc_info=True)
            results.append({"country": c, "error": str(e)})

    logger.info("Pipeline complete. %d/%d countries processed.",
                sum(1 for r in results if "error" not in r), len(country_names))

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Precursion daily pipeline")
    parser.add_argument("country", nargs="?", help="Single country name (legacy)")
    parser.add_argument("--countries", nargs="+", help="List of country names to process")
    parser.add_argument("--all", action="store_true", help="Run all countries")
    args = parser.parse_args()

    try:
        if args.countries:
            run_subset(args.countries)
        elif args.country:
            run_country(args.country)
        elif args.all:
            run_all()
        else:
            run_all()
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user.")
        sys.exit(1)
    except Exception as e:
        logger.error("Pipeline crashed: %s", e, exc_info=True)
        sys.exit(1)
