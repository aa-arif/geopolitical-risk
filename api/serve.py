"""
FastAPI server for the geopolitical risk prediction system.

Endpoints:
  GET /countries                    -> all countries with current risk scores
  GET /countries/{iso3}             -> full detail for a country
  GET /countries/{iso3}/history     -> time series of predictions
  GET /countries/{iso3}/events      -> ACLED daily event timeline
  GET /predictions/snapshot         -> all predictions for a given date
  GET /evaluation                   -> aggregate Brier scores, calibration
  GET /evaluation/track-comparison  -> Track A vs B accuracy
  GET /evaluation/learning          -> factor predictiveness + agent accuracy
  GET /alerts                       -> change alerts
  GET /health                       -> system status
"""

import json
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config.settings import load_all_country_configs, load_all_available_country_configs, COUNTRIES
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
    configs = load_all_available_country_configs()
    conn = get_connection()
    results = []

    for iso3, config in configs.items():
        history = get_prediction_history(conn, iso3, limit=1)
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
                "window_end": latest["window_end_date"],
                "risk_level": _risk_level(prob),
                "resolved": bool(latest["resolved"]),
                "actual_outcome": latest["actual_outcome"],
                "brier_score": latest["brier_score"],
            })
        else:
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
                "window_end": None,
                "risk_level": _risk_level(track_a_prob),
                "track_a_only": True,
                "resolved": False,
                "actual_outcome": None,
                "brier_score": None,
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

    history = get_prediction_history(conn, iso3, limit=1)
    latest = None
    reasoning = {}
    if history:
        latest = history[0]
        reasoning = json.loads(latest["reasoning_summary"]) if latest["reasoning_summary"] else {}

    acled = get_acled_summary(conn, iso3, days=30)

    chains = get_recent_reasoning_chains(conn, iso3, days=7)
    chain_data = []
    for c in chains[:10]:
        try:
            chain_data.append(json.loads(c["chain_json"]))
        except (json.JSONDecodeError, KeyError):
            pass

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


@app.get("/countries/{iso3}/events")
def get_country_events(iso3: str, days: int = 60):
    iso3 = iso3.upper()
    conn = get_connection()

    # Use most recent available data, not calendar days from today
    latest_row = conn.execute(
        "SELECT MAX(event_date) as d FROM acled_events WHERE country_iso3 = ?",
        (iso3,)
    ).fetchone()
    ref_date = latest_row["d"] if latest_row and latest_row["d"] else None

    if not ref_date:
        conn.close()
        return {"iso3": iso3, "days": days, "timeline": []}

    rows = conn.execute(
        """SELECT event_date,
                  COUNT(*) as event_count,
                  SUM(fatalities) as fatalities,
                  event_type
           FROM acled_events
           WHERE country_iso3 = ?
           AND event_date >= date(?, ? || ' days')
           GROUP BY event_date, event_type
           ORDER BY event_date""",
        (iso3, ref_date, f"-{int(days)}"),
    ).fetchall()
    conn.close()

    daily = defaultdict(lambda: {"events": 0, "fatalities": 0, "types": {}})
    for r in rows:
        d = r["event_date"]
        daily[d]["events"] += r["event_count"]
        daily[d]["fatalities"] += r["fatalities"] or 0
        daily[d]["types"][r["event_type"]] = r["event_count"]

    return {
        "iso3": iso3,
        "days": days,
        "timeline": [{"date": d, **info} for d, info in sorted(daily.items())],
    }


@app.get("/predictions/snapshot")
def get_prediction_snapshot(date: str = None):
    conn = get_connection()
    configs = load_all_available_country_configs()

    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows = conn.execute(
        """SELECT p.*,
                  (SELECT GROUP_CONCAT(a.agent_type || ':' || a.probability, '|')
                   FROM agent_outputs a WHERE a.prediction_id = p.id) as agents_raw
           FROM predictions p
           WHERE p.id IN (
               SELECT MAX(p2.id) FROM predictions p2
               WHERE p2.prediction_date <= ?
               GROUP BY p2.country_iso3
           )
           ORDER BY p.calibrated_probability DESC""",
        (date,),
    ).fetchall()

    predictions = []
    for row in rows:
        iso3 = row["country_iso3"]
        config = configs.get(iso3, {})
        reasoning = json.loads(row["reasoning_summary"]) if row["reasoning_summary"] else {}

        agents = {}
        if row["agents_raw"]:
            for pair in row["agents_raw"].split("|"):
                parts = pair.split(":")
                if len(parts) == 2:
                    try:
                        agents[parts[0]] = float(parts[1])
                    except ValueError:
                        pass

        predictions.append({
            "iso3": iso3,
            "name": config.get("name", iso3),
            "prediction_date": row["prediction_date"],
            "window_end_date": row["window_end_date"],
            "track_a": row["track_a_probability"],
            "track_b": row["track_b_probability"],
            "fused": row["fused_probability"],
            "calibrated": row["calibrated_probability"],
            "confidence": reasoning.get("confidence"),
            "executive_summary": reasoning.get("executive_summary", ""),
            "resolved": bool(row["resolved"]),
            "actual_outcome": row["actual_outcome"],
            "brier_score": row["brier_score"],
            "agents": agents,
            "risk_level": _risk_level(row["calibrated_probability"]),
        })

    date_rows = conn.execute(
        "SELECT DISTINCT prediction_date FROM predictions ORDER BY prediction_date"
    ).fetchall()
    available_dates = [r["prediction_date"] for r in date_rows]

    conn.close()
    return {"date": date, "predictions": predictions, "available_dates": available_dates}


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


@app.get("/evaluation/learning")
def get_learning():
    from evaluation.learning import analyze_factor_predictiveness, analyze_agent_accuracy
    conn = get_connection()
    factors = analyze_factor_predictiveness(conn)
    agents = analyze_agent_accuracy(conn)
    conn.close()
    return {"factors": factors, "agents": agents}


@app.get("/alerts")
def get_alerts(days: int = 30):
    conn = get_connection()
    cursor = conn.execute(
        """SELECT * FROM change_alerts
           WHERE alert_date >= date('now', ? || ' days')
           ORDER BY alert_date DESC, ABS(delta) DESC""",
        (f"-{days}",),
    )
    alerts = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"alerts": alerts}


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
