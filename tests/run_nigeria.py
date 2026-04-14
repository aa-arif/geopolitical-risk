"""
Full prediction cycle for Nigeria.
Uses the already-extracted article + Track A anchor to run the complete pipeline.
"""

import sys
import json
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from config.settings import (
    load_country_config, FUSION_WEIGHT_TRACK_A, EXTREMIZING_PARAMETER,
    PREDICTION_WINDOW_DAYS, PROMPT_VERSIONS,
)
from utils.db import initialize_db, get_connection
from utils.api_client import MODELS
from utils.logger import logger, compute_data_hash, log_prediction

from track_a.predict import predict_track_a
from utils.db import insert_article, get_latest_event_date, get_latest_article_id
from track_b.extraction import extract_and_store, summarize_reasoning_chains
from track_b.forecasting import run_ensemble
from track_b.supervisor import reconcile
from fusion.blend import fuse
from fusion.extremize import extremize
from fusion.calibrate import calibrate


def main():
    initialize_db()
    conn = get_connection()
    country_config = load_country_config("nigeria")
    iso3 = country_config["iso3"]

    # ── Step 1: Track A ──────────────────────────────────────────────
    print("=" * 70)
    print("STEP 1: Track A (Structural Model)")
    print("=" * 70)
    track_a = predict_track_a(country_config, conn)
    print(f"  PITF base:        {track_a['components']['pitf_base']:.4f}")
    print(f"  Vulnerability:    {track_a['components']['structural_vulnerability']:.4f}")
    print(f"  Neighborhood:     {track_a['components']['neighborhood_contagion']:.4f}")
    print(f"  GPR trend:        {track_a['components']['gpr_trend']} ({track_a['components']['gpr_trend_description']})")
    print(f"  >> Track A P(instability) = {track_a['probability']:.4f} ({track_a['probability']*100:.1f}%)")
    print()

    # ── Step 2: Load existing extraction ─────────────────────────────
    print("=" * 70)
    print("STEP 2: Load Existing Reasoning Chain")
    print("=" * 70)
    with open("data/articles/test_nigeria_aljazeera.txt", "r") as f:
        article_text = f.read()

    # Insert article into DB and extract with storage
    print("  Inserting article and running extraction...")
    article_id = insert_article(
        conn, iso3,
        "Holding hands: How Nigeria turned Trump threats to military partnership",
        "Al Jazeera",
        "https://www.aljazeera.com/news/2026/2/19/from-us-threats-to-holding-hands",
        "2026-02-19",
        article_text,
    )
    extraction = extract_and_store(article_id, article_text, country_config, conn)
    n_factors = len(extraction.get("causal_factors", []))
    n_counter = len(extraction.get("counterarguments", []))
    print(f"  Stored article id={article_id}, extracted {n_factors} factors, {n_counter} counterarguments")

    # Build reasoning summary for forecasting agents
    chains = [extraction]
    reasoning_summary = summarize_reasoning_chains(chains)
    print(f"  Reasoning summary: {len(reasoning_summary)} chars")
    print()

    # ── Step 3: ACLED summary (empty for now, no live data) ──────────
    acled_data = {
        "total_events": 0,
        "total_fatalities": 0,
        "by_type": [],
        "period_days": 30,
    }

    # ── Step 4: Track B Forecasting Ensemble ─────────────────────────
    print("=" * 70)
    print("STEP 3: Track B Forecasting Ensemble (4 agents)")
    print("=" * 70)
    ensemble_results = run_ensemble(country_config, track_a, reasoning_summary, acled_data)
    for i, result in enumerate(ensemble_results):
        agent_type = result.get("agent_type", f"agent_{i+1}")
        prob = result["final_probability"]
        print(f"  Agent {i+1} ({agent_type}): P = {prob:.4f} ({prob*100:.1f}%)")
    print()

    # ── Step 5: Supervisor Reconciliation ────────────────────────────
    print("=" * 70)
    print("STEP 4: Supervisor Reconciliation (Opus)")
    print("=" * 70)
    supervisor_result = reconcile(country_config, track_a, ensemble_results)
    track_b_prob = supervisor_result["final_probability"]
    print(f"  >> Track B P(instability) = {track_b_prob:.4f} ({track_b_prob*100:.1f}%)")
    print(f"  Confidence: {supervisor_result.get('confidence', 'N/A')}")
    if supervisor_result.get("narrative_summary"):
        print(f"  Narrative: {supervisor_result['narrative_summary'][:200]}...")
    print()

    # ── Step 6: Fusion ───────────────────────────────────────────────
    print("=" * 70)
    print("STEP 5: Fusion (Blend + Extremize + Calibrate)")
    print("=" * 70)
    fused_prob = fuse(track_a["probability"], track_b_prob, FUSION_WEIGHT_TRACK_A)
    extremized_prob = extremize(fused_prob, EXTREMIZING_PARAMETER)
    calibrated_prob = calibrate(extremized_prob)
    print(f"  Track A:      {track_a['probability']:.4f} (weight {FUSION_WEIGHT_TRACK_A})")
    print(f"  Track B:      {track_b_prob:.4f} (weight {1-FUSION_WEIGHT_TRACK_A})")
    print(f"  Fused:        {fused_prob:.4f}")
    print(f"  Extremized:   {extremized_prob:.4f} (d={EXTREMIZING_PARAMETER})")
    print(f"  >> Calibrated: {calibrated_prob:.4f} ({calibrated_prob*100:.1f}%)")
    print()

    # ── Step 7: Save to database ─────────────────────────────────────
    print("=" * 70)
    print("STEP 6: Save Prediction to Database")
    print("=" * 70)
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
            "key_risk_factors": supervisor_result.get("key_risk_factors", []),
            "key_stabilizing_factors": supervisor_result.get("key_stabilizing_factors", []),
            "confidence": supervisor_result.get("confidence", "medium"),
        },
        "prompt_versions": PROMPT_VERSIONS,
        "model_versions": MODELS,
        "data_hash": compute_data_hash(
            iso3, 0, 1,
            get_latest_event_date(conn, iso3),
            get_latest_article_id(conn, iso3),
        ),
    }
    log_prediction(conn, prediction_data)
    conn.close()
    print("  Prediction saved to data/geopolitical.db")
    print()

    # ── Summary ──────────────────────────────────────────────────────
    print("=" * 70)
    print("FINAL RESULT: Nigeria 30-Day Instability Forecast")
    print("=" * 70)
    print(f"  Track A (structural):  {track_a['probability']*100:.1f}%")
    print(f"  Track B (LLM ensemble): {track_b_prob*100:.1f}%")
    print(f"  Fused probability:     {calibrated_prob*100:.1f}%")
    print(f"  Confidence:            {supervisor_result.get('confidence', 'N/A')}")
    print(f"  Window:                {now.strftime('%Y-%m-%d')} to {(now + timedelta(days=30)).strftime('%Y-%m-%d')}")
    if supervisor_result.get("key_risk_factors"):
        print(f"  Risk factors:          {', '.join(supervisor_result['key_risk_factors'][:4])}")
    if supervisor_result.get("key_stabilizing_factors"):
        print(f"  Stabilizers:           {', '.join(supervisor_result['key_stabilizing_factors'][:4])}")
    print("=" * 70)


if __name__ == "__main__":
    main()
