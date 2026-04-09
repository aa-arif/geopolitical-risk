"""
FastAPI server for the geopolitical risk prediction system.

Endpoints:
  GET /countries                    -> all countries with current risk scores
  GET /countries/{iso3}             -> full detail for a country
  GET /countries/{iso3}/history     -> time series of predictions
  GET /evaluation                   -> aggregate Brier scores, calibration
  GET /evaluation/track-comparison  -> Track A vs B accuracy
  GET /health                       -> system status
"""

import json
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config.settings import load_all_country_configs, COUNTRIES
from track_a.predict import predict_track_a
from utils.db import (
    get_connection, initialize_db, get_prediction_history,
    get_acled_summary, get_recent_reasoning_chains, get_resolved_predictions,
    get_agent_outputs,
)
from evaluation.track_comparison import compare_tracks
from evaluation.calibration_curve import compute_calibration_curve
from evaluation.brier import brier_score_aggregate

app = FastAPI(
    title="Geopolitical Risk Prediction API",
    version="1.0.0",
    description="Dual-track geopolitical risk forecasting with reasoning chains",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    initialize_db()


@app.get("/health")
def health():
    conn = get_connection()
    try:
        pred_count = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        article_count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        acled_count = conn.execute("SELECT COUNT(*) FROM acled_events").fetchone()[0]
    finally:
        conn.close()

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "predictions_count": pred_count,
        "articles_count": article_count,
        "acled_events_count": acled_count,
    }


@app.get("/countries")
def list_countries():
    configs = load_all_country_configs()
    conn = get_connection()
    results = []

    for iso3, config in configs.items():
        history = get_prediction_history(conn, iso3, limit=1)
        latest = None
        if history:
            latest = history[0]
            prob = latest["calibrated_probability"]
            reasoning = json.loads(latest["reasoning_summary"]) if latest["reasoning_summary"] else {}
            results.append({
                "iso3": iso3,
                "name": config["name"],
                "current_probability": prob,
                "track_a": latest["track_a_probability"],
                "track_b": latest["track_b_probability"],
                "confidence": reasoning.get("confidence"),
                "prediction_date": latest["prediction_date"],
                "risk_level": _risk_level(prob),
            })
        else:
            # No prediction yet -- compute Track A structural probability
            track_a_result = predict_track_a(config, conn)
            track_a_prob = track_a_result["probability"]
            results.append({
                "iso3": iso3,
                "name": config["name"],
                "current_probability": track_a_prob,
                "track_a": track_a_prob,
                "track_b": None,
                "confidence": None,
                "prediction_date": None,
                "risk_level": _risk_level(track_a_prob),
                "track_a_only": True,
            })

    conn.close()
    return {"countries": sorted(results, key=lambda x: x["current_probability"] or 0, reverse=True)}


@app.get("/countries/{iso3}")
def get_country(iso3: str):
    iso3 = iso3.upper()
    configs = load_all_country_configs()
    if iso3 not in configs:
        raise HTTPException(status_code=404, detail=f"Country {iso3} not found")

    config = configs[iso3]
    conn = get_connection()

    # Latest prediction
    history = get_prediction_history(conn, iso3, limit=1)
    latest = None
    reasoning = {}
    if history:
        latest = history[0]
        reasoning = json.loads(latest["reasoning_summary"]) if latest["reasoning_summary"] else {}

    # ACLED summary
    acled = get_acled_summary(conn, iso3, days=30)

    # Recent reasoning chains
    chains = get_recent_reasoning_chains(conn, iso3, days=7)
    chain_data = []
    for c in chains[:10]:
        try:
            chain_data.append(json.loads(c["chain_json"]))
        except (json.JSONDecodeError, KeyError):
            pass

    # Agent outputs for the latest prediction
    agent_data = []
    if latest:
        agent_rows = get_agent_outputs(conn, latest["id"])
        for row in agent_rows:
            agent_data.append({
                "agent_type": row["agent_type"],
                "probability": row["probability"],
                "reasoning": json.loads(row["reasoning_json"]) if row.get("reasoning_json") else {},
            })

    conn.close()

    return {
        "iso3": iso3,
        "name": config["name"],
        "risk_context": config["risk_context"],
        "key_actors": config["key_actors"],
        "current_prediction": {
            "probability": latest["calibrated_probability"] if latest else None,
            "track_a": latest["track_a_probability"] if latest else None,
            "track_b": latest["track_b_probability"] if latest else None,
            "fused": latest["fused_probability"] if latest else None,
            "prediction_date": latest["prediction_date"] if latest else None,
            "window_end": latest["window_end_date"] if latest else None,
            "risk_level": _risk_level(latest["calibrated_probability"]) if latest else None,
        },
        "reasoning": {
            "track_a_components": reasoning.get("track_a_components", {}),
            "narrative": reasoning.get("supervisor", ""),
            "executive_summary": reasoning.get("executive_summary", ""),
            "key_risk_factors": reasoning.get("key_risk_factors", []),
            "key_stabilizing_factors": reasoning.get("key_stabilizing_factors", []),
            "confidence": reasoning.get("confidence"),
        },
        "acled_30d": acled,
        "reasoning_chains": chain_data,
        "agent_outputs": agent_data,
        "structural_variables": {
            "polity_code": config["polity_code"],
            "polity_category": config["polity_category"],
            "infant_mortality": config["infant_mortality_per_1000"],
            "gdp_per_capita": config["gdp_per_capita_usd"],
            "v_dem_liberal_democracy": config["v_dem_liberal_democracy_index"],
        },
    }


@app.get("/countries/{iso3}/history")
def get_country_history(iso3: str, limit: int = 90):
    iso3 = iso3.upper()
    conn = get_connection()
    history = get_prediction_history(conn, iso3, limit=limit)
    conn.close()

    if not history:
        raise HTTPException(status_code=404, detail=f"No predictions for {iso3}")

    return {
        "iso3": iso3,
        "predictions": [
            {
                "date": p["prediction_date"],
                "probability": p["calibrated_probability"],
                "track_a": p["track_a_probability"],
                "track_b": p["track_b_probability"],
                "fused": p["fused_probability"],
                "resolved": p["resolved"],
                "actual": p["actual_outcome"],
            }
            for p in history
        ],
    }


@app.get("/evaluation")
def get_evaluation():
    conn = get_connection()
    resolved = get_resolved_predictions(conn)

    if not resolved:
        conn.close()
        return {"n_resolved": 0, "message": "No resolved predictions yet."}

    preds = [r["calibrated_probability"] for r in resolved if r["actual_outcome"] is not None]
    actuals = [r["actual_outcome"] for r in resolved if r["actual_outcome"] is not None]

    brier = brier_score_aggregate(preds, actuals) if preds else None
    calibration = compute_calibration_curve(preds, actuals) if preds else None

    conn.close()

    return {
        "n_resolved": len(preds),
        "brier_aggregate": brier,
        "base_rate": sum(actuals) / len(actuals) if actuals else None,
        "calibration": calibration,
    }


@app.get("/evaluation/track-comparison")
def get_track_comparison():
    conn = get_connection()
    comparison = compare_tracks(conn)
    conn.close()
    return comparison


def _risk_level(prob: float) -> str:
    if prob >= 0.5:
        return "CRITICAL"
    elif prob >= 0.3:
        return "HIGH"
    elif prob >= 0.15:
        return "ELEVATED"
    return "LOW"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
