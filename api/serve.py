"""
FastAPI server for the geopolitical risk prediction system.

Endpoints (all under /api):
  GET /api/countries                    -> all countries with current risk + event-type scores
  GET /api/countries/{iso3}             -> full detail with event-type breakdown, brief, alerts
  GET /api/countries/{iso3}/history     -> time series of predictions
  GET /api/countries/{iso3}/events      -> conflict event daily timeline
  GET /api/predictions/snapshot         -> all predictions for a given date
  GET /api/evaluation                   -> aggregate Brier scores, calibration
  GET /api/evaluation/track-comparison  -> Track A vs B accuracy
  GET /api/evaluation/learning          -> factor predictiveness + agent accuracy
  GET /api/alerts                       -> V2 per-event-type alerts
  GET /api/briefs/latest                -> today's formatted briefs for all countries
  GET /api/health                       -> system status

Also serves the built React frontend from frontend/dist as static files.
"""

import os
import json
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config.settings import load_all_country_configs, COUNTRIES, PROJECT_ROOT
from track_a.predict import predict_track_a
from utils import risk_level as _risk_level
from utils.db import (
    get_connection, initialize_db, get_prediction_history,
    get_event_summary, get_recent_reasoning_chains, get_resolved_predictions,
    get_resolved_predictions_by_confidence,
    get_agent_outputs, get_latest_predictions_all, get_previous_predictions_all,
    get_event_type_predictions,
    get_latest_brief, get_latest_briefs_all, get_recent_alerts_v2,
)
from evaluation.track_comparison import compare_tracks
from evaluation.calibration_curve import compute_calibration_curve
from evaluation.brier import brier_score_aggregate

# --- Static files path ---
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app):
    initialize_db()
    yield


app = FastAPI(
    title="Geopolitical Risk Prediction API",
    version="2.0.0",
    description="Dual-track geopolitical risk forecasting with event-type decomposition",
    lifespan=lifespan,
)

# --- CORS ---
_cors_env = os.environ.get("CORS_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else [
    "http://localhost:3000", "http://localhost:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- API Router (all endpoints under /api) ---
api = APIRouter(prefix="/api")


@api.get("/health")
def health():
    conn = get_connection()
    try:
        pred_count = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        article_count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM conflict_events").fetchone()[0]
        alert_count = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE alert_date >= date('now', '-7 days')"
        ).fetchone()[0]
    except Exception:
        pred_count = article_count = event_count = alert_count = 0
    finally:
        conn.close()

    return {
        "status": "ok",
        "timestamp": datetime.now().astimezone().isoformat(),
        "predictions_count": pred_count,
        "articles_count": article_count,
        "conflict_events_count": event_count,
        "alerts_7d": alert_count,
    }


@api.get("/countries")
def list_countries():
    """List all countries with current composite risk + event-type scores."""
    configs = load_all_country_configs()
    conn = get_connection()
    try:
        # Single query for all latest composite predictions (fixes N+1)
        latest_composites = get_latest_predictions_all(conn, event_type="composite")
        composite_by_iso3 = {r["country_iso3"]: r for r in latest_composites}

        # Previous composite predictions for delta computation
        previous_composites = get_previous_predictions_all(conn, event_type="composite")
        previous_by_iso3 = {r["country_iso3"]: r for r in previous_composites}

        # Single query for all latest event-type predictions
        et_predictions = {}
        for et in ["ACE", "MCU", "REC", "PSS", "CID"]:
            for row in get_latest_predictions_all(conn, event_type=et):
                et_predictions.setdefault(row["country_iso3"], {})[et] = row["calibrated_probability"]

        results = []
        for iso3, config in configs.items():
            latest = composite_by_iso3.get(iso3)
            if latest:
                prob = latest["calibrated_probability"]
                try:
                    reasoning = json.loads(latest["reasoning_summary"]) if latest["reasoning_summary"] else {}
                except (json.JSONDecodeError, TypeError):
                    reasoning = {}

                prev = previous_by_iso3.get(iso3)
                delta = (prob - prev["calibrated_probability"]) if prev else None

                results.append({
                    "iso3": iso3,
                    "name": config["name"],
                    "current_probability": prob,
                    "delta": delta,
                    "track_a": latest["track_a_probability"],
                    "track_b": latest["track_b_probability"],
                    "confidence": reasoning.get("confidence"),
                    "prediction_date": latest["prediction_date"],
                    "window_end": latest["window_end_date"],
                    "risk_level": _risk_level(prob),
                    "event_type_scores": et_predictions.get(iso3, {}),
                    "resolved": bool(latest["resolved"]),
                    "actual_outcome": latest["actual_outcome"],
                    "brier_score": latest["brier_score"],
                })
            else:
                try:
                    track_a_result = predict_track_a(config, conn)
                    track_a_prob = track_a_result["probability"]
                except Exception:
                    track_a_prob = None
                results.append({
                    "iso3": iso3,
                    "name": config["name"],
                    "current_probability": track_a_prob,
                    "delta": None,
                    "track_a": track_a_prob,
                    "track_b": None,
                    "confidence": None,
                    "prediction_date": None,
                    "window_end": None,
                    "risk_level": _risk_level(track_a_prob) if track_a_prob else "LOW",
                    "event_type_scores": {},
                    "track_a_only": True,
                    "resolved": False,
                    "actual_outcome": None,
                    "brier_score": None,
                })

        return {"countries": sorted(results, key=lambda x: x["current_probability"] or 0, reverse=True)}
    finally:
        conn.close()


@api.get("/countries/{iso3}")
def get_country(iso3: str):
    """Full country detail with event-type breakdown, brief, and alerts."""
    iso3 = iso3.upper()
    configs = load_all_country_configs()
    if iso3 not in configs:
        raise HTTPException(status_code=404, detail=f"Country {iso3} not found")

    config = configs[iso3]
    conn = get_connection()
    try:
        history = get_prediction_history(conn, iso3, limit=1)
        latest = None
        reasoning = {}
        if history:
            latest = history[0]
            try:
                reasoning = json.loads(latest["reasoning_summary"]) if latest["reasoning_summary"] else {}
            except (json.JSONDecodeError, TypeError):
                reasoning = {}

        # Event-type breakdown
        et_predictions = get_event_type_predictions(conn, iso3)
        event_type_scores = {}
        for etp in et_predictions:
            et = etp["event_type"]
            event_type_scores[et] = {
                "probability": etp["calibrated_probability"],
                "track_a": etp["track_a_probability"],
                "track_b": etp["track_b_probability"],
            }
            try:
                et_reasoning = json.loads(etp["reasoning_summary"]) if etp.get("reasoning_summary") else {}
                event_type_scores[et]["driver"] = et_reasoning.get("driver", "")
            except (json.JSONDecodeError, TypeError):
                event_type_scores[et]["driver"] = ""

        # Daily brief
        brief = get_latest_brief(conn, iso3)
        brief_data = None
        if brief:
            brief_data = {
                "date": brief["brief_date"],
                "headline": brief["headline"],
                "body": brief["body_markdown"],
            }

        # Recent alerts (7 days)
        alerts = get_recent_alerts_v2(conn, iso3, days=7)
        alert_data = [
            {
                "date": a["alert_date"],
                "event_type": a["event_type"],
                "trigger_type": a["trigger_type"],
                "delta": a["delta"],
                "brief": a["brief_text"],
            }
            for a in alerts
        ]

        # Event data
        event_data = get_event_summary(conn, iso3, days=30)

        # Reasoning chains
        chains = get_recent_reasoning_chains(conn, iso3, days=7)
        chain_data = []
        for c in chains[:10]:
            try:
                chain_data.append(json.loads(c["chain_json"]))
            except (json.JSONDecodeError, KeyError):
                pass

        # Agent outputs
        agent_data = []
        if latest:
            agent_rows = get_agent_outputs(conn, latest["id"])
            for row in agent_rows:
                try:
                    agent_reasoning = json.loads(row["reasoning_json"]) if row.get("reasoning_json") else {}
                except (json.JSONDecodeError, TypeError):
                    agent_reasoning = {}
                agent_data.append({
                    "agent_type": row["agent_type"],
                    "probability": row["probability"],
                    "reasoning": agent_reasoning,
                })

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
            "event_type_scores": event_type_scores,
            "daily_brief": brief_data,
            "alerts_7d": alert_data,
            "reasoning": {
                "track_a_components": reasoning.get("track_a_components", {}),
                "narrative": reasoning.get("supervisor", ""),
                "executive_summary": reasoning.get("executive_summary", ""),
                "key_risk_factors": reasoning.get("key_risk_factors", []),
                "key_stabilizing_factors": reasoning.get("key_stabilizing_factors", []),
                "confidence": reasoning.get("confidence"),
                "event_type_drivers": reasoning.get("event_type_drivers", {}),
            },
            "conflict_events_30d": event_data,
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
    finally:
        conn.close()


@api.get("/countries/{iso3}/history")
def get_country_history(iso3: str, limit: int = 90):
    iso3 = iso3.upper()
    conn = get_connection()
    try:
        # Only return composite predictions for the time series
        cursor = conn.execute(
            """SELECT * FROM predictions
               WHERE country_iso3 = ?
               AND COALESCE(event_type, 'composite') = 'composite'
               ORDER BY prediction_date DESC
               LIMIT ?""",
            (iso3, limit),
        )
        history = [dict(row) for row in cursor.fetchall()]

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
    finally:
        conn.close()


@api.get("/countries/{iso3}/events")
def get_country_events(iso3: str, days: int = 60):
    """Conflict event timeline from the unified conflict_events table."""
    iso3 = iso3.upper()
    conn = get_connection()
    try:
        latest_row = conn.execute(
            "SELECT MAX(event_date) as d FROM conflict_events WHERE country_iso3 = ?",
            (iso3,)
        ).fetchone()
        ref_date = latest_row["d"] if latest_row and latest_row["d"] else None

        if not ref_date:
            return {"iso3": iso3, "days": days, "timeline": []}

        rows = conn.execute(
            """SELECT event_date,
                      COUNT(*) as event_count,
                      SUM(fatalities) as fatalities,
                      event_category
               FROM conflict_events
               WHERE country_iso3 = ?
               AND event_date >= date(?, ? || ' days')
               GROUP BY event_date, event_category
               ORDER BY event_date""",
            (iso3, ref_date, f"-{int(days)}"),
        ).fetchall()

        daily = defaultdict(lambda: {"events": 0, "fatalities": 0, "categories": {}})
        for r in rows:
            d = r["event_date"]
            daily[d]["events"] += r["event_count"]
            daily[d]["fatalities"] += r["fatalities"] or 0
            daily[d]["categories"][r["event_category"]] = r["event_count"]

        return {
            "iso3": iso3,
            "days": days,
            "timeline": [{"date": d, **info} for d, info in sorted(daily.items())],
        }
    finally:
        conn.close()


@api.get("/predictions/snapshot")
def get_prediction_snapshot(date: str = None):
    conn = get_connection()
    try:
        configs = load_all_country_configs()

        if not date:
            latest = conn.execute(
                "SELECT MAX(prediction_date) as d FROM predictions"
            ).fetchone()
            date = latest["d"] if latest and latest["d"] else datetime.now().astimezone().strftime("%Y-%m-%d")

        rows = conn.execute(
            """SELECT p.*,
                      (SELECT GROUP_CONCAT(a.agent_type || ':' || a.probability, '|')
                       FROM agent_outputs a WHERE a.prediction_id = p.id) as agents_raw
               FROM predictions p
               WHERE p.id IN (
                   SELECT MAX(p2.id) FROM predictions p2
                   WHERE p2.prediction_date <= ?
                   AND COALESCE(p2.event_type, 'composite') = 'composite'
                   GROUP BY p2.country_iso3
               )
               ORDER BY p.calibrated_probability DESC""",
            (date,),
        ).fetchall()

        predictions = []
        for row in rows:
            iso3 = row["country_iso3"]
            if iso3 not in configs:
                continue
            config = configs[iso3]
            try:
                reasoning = json.loads(row["reasoning_summary"]) if row["reasoning_summary"] else {}
            except (json.JSONDecodeError, TypeError):
                reasoning = {}

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
                "event_type_scores": reasoning.get("event_type_scores", {}),
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

        return {"date": date, "predictions": predictions, "available_dates": available_dates}
    finally:
        conn.close()


@api.get("/verticals/{vertical_name}")
def get_vertical(vertical_name: str):
    """Get vertical data: all countries with event-type scores + corridor brief."""
    from config.settings import VERTICALS
    from utils.db import get_latest_brief

    vertical = VERTICALS.get(vertical_name)
    if not vertical:
        raise HTTPException(status_code=404, detail=f"Vertical '{vertical_name}' not found")

    conn = get_connection()
    try:
        configs = load_all_country_configs()
        country_names = vertical["countries"]

        # Gather data for vertical countries
        countries_data = []
        for country_name in country_names:
            try:
                from config.settings import load_country_config
                config = load_country_config(country_name)
            except (FileNotFoundError, KeyError):
                continue

            iso3 = config["iso3"]
            if iso3 not in configs:
                configs[iso3] = config

            # Latest composite
            history = get_prediction_history(conn, iso3, limit=1)
            if not history:
                continue
            latest = history[0]
            prob = latest["calibrated_probability"]

            # Event-type scores
            et_preds = get_event_type_predictions(conn, iso3)
            et_scores = {p["event_type"]: p["calibrated_probability"] for p in et_preds}

            # Reasoning
            try:
                reasoning = json.loads(latest["reasoning_summary"]) if latest.get("reasoning_summary") else {}
            except (json.JSONDecodeError, TypeError):
                reasoning = {}

            countries_data.append({
                "iso3": iso3,
                "name": config["name"],
                "composite": prob,
                "risk_level": _risk_level(prob),
                "event_type_scores": et_scores,
                "executive_summary": reasoning.get("executive_summary", ""),
                "prediction_date": latest["prediction_date"],
            })

        countries_data.sort(key=lambda x: x["composite"], reverse=True)

        # Corridor brief
        corridor_brief = None
        brief_row = conn.execute(
            """SELECT * FROM briefs
               WHERE brief_type = ? ORDER BY brief_date DESC LIMIT 1""",
            (f"vertical_{vertical_name}",),
        ).fetchone()
        if brief_row:
            corridor_brief = {
                "date": brief_row["brief_date"],
                "headline": brief_row["headline"],
                "body": brief_row["body_markdown"],
            }

        # Recent alerts for vertical countries
        vertical_iso3s = [c["iso3"] for c in countries_data]
        alerts = get_recent_alerts_v2(conn, days=7)
        vertical_alerts = [a for a in alerts if a["country_iso3"] in vertical_iso3s]

        return {
            "vertical": vertical_name,
            "name": vertical["name"],
            "description": vertical["description"],
            "sectors": vertical["sectors"],
            "countries": countries_data,
            "corridor_brief": corridor_brief,
            "alerts": vertical_alerts[:10],
        }
    finally:
        conn.close()


def _summarize_confidence_bucket(rows: list) -> dict:
    """Brier + count for a confidence-filtered list of resolved predictions."""
    filtered = [r for r in rows if r["actual_outcome"] is not None]
    if not filtered:
        return {"n": 0, "brier_score": None}
    preds = [r["calibrated_probability"] for r in filtered]
    actuals = [r["actual_outcome"] for r in filtered]
    return {
        "n": len(filtered),
        "brier_score": brier_score_aggregate(preds, actuals),
    }


@api.get("/evaluation")
def get_evaluation():
    conn = get_connection()
    try:
        # Composite evaluation (V1 compat)
        resolved = get_resolved_predictions(conn)
        if not resolved:
            return {"n_resolved": 0, "message": "No resolved predictions yet."}

        preds = [r["calibrated_probability"] for r in resolved if r["actual_outcome"] is not None]
        actuals = [r["actual_outcome"] for r in resolved if r["actual_outcome"] is not None]

        brier = brier_score_aggregate(preds, actuals) if preds else None
        calibration = compute_calibration_curve(preds, actuals) if preds else None

        # Per-event-type Brier scores
        per_type_brier = {}
        for et in ["ACE", "MCU", "REC"]:
            et_rows = conn.execute(
                """SELECT calibrated_probability, actual_outcome
                   FROM predictions
                   WHERE resolved = TRUE AND actual_outcome IS NOT NULL
                   AND event_type = ?""",
                (et,),
            ).fetchall()
            if et_rows:
                et_preds = [r["calibrated_probability"] for r in et_rows]
                et_actuals = [r["actual_outcome"] for r in et_rows]
                per_type_brier[et] = {
                    "brier": brier_score_aggregate(et_preds, et_actuals),
                    "n_resolved": len(et_rows),
                    "base_rate": sum(et_actuals) / len(et_actuals),
                }

        aggregate = {
            "n": len(preds),
            "brier_score": brier,
            "base_rate": sum(actuals) / len(actuals) if actuals else None,
            "calibration": calibration,
            "per_event_type": per_type_brier,
        }

        high_rows = get_resolved_predictions_by_confidence(conn, "high")
        low_rows = get_resolved_predictions_by_confidence(conn, "low")
        unknown_rows = get_resolved_predictions_by_confidence(conn, "unknown")

        return {
            # V1-compat top-level fields (existing frontend code reads these)
            "n_resolved": len(preds),
            "brier_aggregate": brier,
            "base_rate": aggregate["base_rate"],
            "calibration": calibration,
            "per_event_type": per_type_brier,
            # P0.2 breakdown by ingest-volume confidence
            "aggregate": aggregate,
            "high_confidence": _summarize_confidence_bucket(high_rows),
            "low_confidence": _summarize_confidence_bucket(low_rows),
            "unknown_confidence": _summarize_confidence_bucket(unknown_rows),
        }
    finally:
        conn.close()


@api.get("/evaluation/track-comparison")
def get_track_comparison():
    conn = get_connection()
    try:
        comparison = compare_tracks(conn)
        return comparison
    finally:
        conn.close()


@api.get("/evaluation/learning")
def get_learning():
    from evaluation.learning import analyze_factor_predictiveness, analyze_agent_accuracy
    conn = get_connection()
    try:
        factors = analyze_factor_predictiveness(conn)
        agents = analyze_agent_accuracy(conn)
        return {"factors": factors, "agents": agents}
    finally:
        conn.close()


@api.get("/alerts")
def get_alerts(days: int = 30, country: str = None):
    """V2 per-event-type alerts."""
    conn = get_connection()
    try:
        iso3 = country.upper() if country else None
        alerts = get_recent_alerts_v2(conn, country_iso3=iso3, days=days)
        return {"alerts": alerts, "count": len(alerts)}
    finally:
        conn.close()


@api.get("/briefs/latest")
def get_briefs_latest():
    """Get today's formatted briefs for all countries."""
    conn = get_connection()
    try:
        briefs = get_latest_briefs_all(conn)
        result = []
        for b in briefs:
            try:
                et_scores = json.loads(b["event_type_scores"]) if b.get("event_type_scores") else {}
            except (json.JSONDecodeError, TypeError):
                et_scores = {}
            try:
                drivers = json.loads(b["key_drivers"]) if b.get("key_drivers") else []
            except (json.JSONDecodeError, TypeError):
                drivers = []

            result.append({
                "iso3": b["country_iso3"],
                "date": b["brief_date"],
                "type": b["brief_type"],
                "headline": b["headline"],
                "body": b["body_markdown"],
                "event_type_scores": et_scores,
                "key_drivers": drivers,
            })
        return {"briefs": result, "count": len(result)}
    finally:
        conn.close()


@api.post("/subscribers")
def create_subscriber(
    email: str,
    name: str = None,
    organization: str = None,
    countries: str = None,
    event_types: str = None,
    tier: str = "free",
):
    """Create a new alert subscriber. Pass countries/event_types as comma-separated."""
    from utils.db import insert_subscriber
    conn = get_connection()
    try:
        countries_list = [c.strip() for c in countries.split(",")] if countries else None
        et_list = [e.strip() for e in event_types.split(",")] if event_types else None

        sid = insert_subscriber(
            conn, email=email, name=name, organization=organization,
            countries=countries_list, event_types=et_list, tier=tier,
        )
        return {"id": sid, "email": email, "status": "active"}
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            raise HTTPException(status_code=409, detail="Email already subscribed")
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@api.get("/subscribers")
def list_subscribers():
    """List all active subscribers."""
    from utils.db import get_active_subscribers
    conn = get_connection()
    try:
        subs = get_active_subscribers(conn)
        return {"subscribers": subs, "count": len(subs)}
    finally:
        conn.close()


@api.delete("/subscribers/{subscriber_id}")
def remove_subscriber(subscriber_id: int):
    """Deactivate a subscriber."""
    from utils.db import delete_subscriber
    conn = get_connection()
    try:
        found = delete_subscriber(conn, subscriber_id)
        if not found:
            raise HTTPException(status_code=404, detail="Subscriber not found")
        return {"id": subscriber_id, "status": "deactivated"}
    finally:
        conn.close()


# --- Mount API router ---
app.include_router(api)


# --- Serve React frontend (production) ---
if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():
    # Serve static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="static-assets")

    # SPA catch-all: any non-API route serves index.html for client-side routing
    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        file_path = FRONTEND_DIST / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(FRONTEND_DIST / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
