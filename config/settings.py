"""
Central configuration for the geopolitical risk prediction system.
All API keys loaded from environment variables -- never hardcoded.
"""

import os
import json
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# --- Paths ---
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "geopolitical.db"
COUNTRIES_DIR = Path(__file__).parent / "countries"
PROMPTS_DIR = Path(__file__).parent / "prompts"

# --- API Keys (from environment) ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ALERT_FROM_EMAIL = os.environ.get("ALERT_FROM_EMAIL", "alerts@precursion.ai")

# --- LLM Model Versions (pinned for reproducibility) ---
MODEL_SONNET = "claude-sonnet-4-20250514"
MODEL_OPUS = "claude-opus-4-20250514"
MODEL_HAIKU = "claude-haiku-4-5-20251001"

# --- Fusion Parameters ---
FUSION_WEIGHT_TRACK_A = 0.6  # Weight favoring Track A (structural model)
EXTREMIZING_PARAMETER = 1.0  # 1.0 = no extremizing; >1.0 = push away from 0.5

# Load persisted weight override if it exists
_WEIGHT_OVERRIDE_PATH = DATA_DIR / "fusion_weight_override.json"
if _WEIGHT_OVERRIDE_PATH.exists():
    try:
        import json as _json
        _override = _json.loads(_WEIGHT_OVERRIDE_PATH.read_text())
        FUSION_WEIGHT_TRACK_A = _override["fusion_weight_track_a"]
    except (json.JSONDecodeError, KeyError, TypeError, OSError) as e:
        import logging as _logging
        _logging.getLogger("georisk").warning(
            "Failed to load fusion weight override from %s: %s", _WEIGHT_OVERRIDE_PATH, e
        )

# --- Prediction Parameters ---
PREDICTION_WINDOW_DAYS = 30
EVENT_THRESHOLD_PERCENTILE = 90  # 90th percentile of 12-month country average
NEIGHBORHOOD_LOOKBACK_DAYS = 90

# --- Event Type Resolution (source-agnostic) ---
# Maps each event type to data source config for resolution.
# Sources: 'gdelt' (CAMEO-coded), 'internal' (LLM-classified articles).
# threshold_method: 'percentile_90' (count exceeds 90th pct) or 'occurrence' (binary).
EVENT_TYPE_RESOLUTION = {
    "ACE": {
        "name": "Armed Conflict Escalation",
        "sources": ["gdelt", "internal"],
        "gdelt_query_terms": ["conflict", "military", "attack", "violence", "battle"],
        "internal_categories": ["armed_clash", "bombing", "shelling", "targeted_violence"],
        "require_fatalities": True,
        "threshold_method": "percentile_90",
    },
    "MCU": {
        "name": "Mass Civil Unrest",
        "sources": ["gdelt", "internal"],
        "gdelt_query_terms": ["protest", "riot", "strike", "demonstration"],
        "internal_categories": ["protest", "riot", "strike", "demonstration"],
        "require_fatalities": False,
        "threshold_method": "percentile_90",
    },
    "REC": {
        "name": "Regime / Executive Change",
        "sources": ["gdelt", "internal"],
        "gdelt_query_terms": ["coup", "government change", "resignation", "succession"],
        "internal_categories": ["coup", "forced_resignation", "emergency_succession",
                                "contested_transfer"],
        "require_fatalities": False,
        "threshold_method": "occurrence",
    },
    "PSS": {
        "name": "Policy / Sanctions Shift",
        "sources": ["internal"],
        "internal_categories": ["sanctions", "trade_restriction", "regulatory_change",
                                "policy_reversal"],
        "require_fatalities": False,
        "threshold_method": None,  # qualitative only -- no automated resolution
    },
    "CID": {
        "name": "Critical Infrastructure Disruption",
        "sources": ["internal"],
        "internal_categories": ["infrastructure_attack", "power_outage", "port_closure",
                                "telecom_disruption", "financial_system_failure"],
        "require_fatalities": False,
        "threshold_method": None,  # qualitative only -- no automated resolution
    },
}

# Event categories eligible for calibrated probability scores and Brier scoring.
# PSS and CID are qualitative-only until reliable resolution methods exist.
CALIBRATED_EVENT_TYPES = ["ACE", "MCU", "REC"]

# --- Countries ---
COUNTRIES = [
    "nigeria", "bangladesh", "pakistan", "philippines", "turkey",
    "ethiopia", "myanmar", "iraq", "colombia", "sudan",
    "ukraine", "somalia", "yemen", "egypt", "kenya",
    # Iran-Gulf Energy Corridor (V2 vertical)
    "iran", "saudiarabia", "uae", "kuwait", "bahrain", "qatar", "oman",
]

# --- Verticals ---
# Named subsets of countries with sector-specific framing.
VERTICALS = {
    "iran_gulf": {
        "name": "Iran-Gulf Energy Corridor",
        "countries": ["iran", "iraq", "saudiarabia", "uae", "kuwait",
                      "bahrain", "qatar", "oman", "yemen"],
        "sectors": {
            "energy": "Oil/gas supply, refinery operations, pipeline security, LNG shipments",
            "shipping": "Strait of Hormuz transit, war-risk insurance, re-routing costs",
            "finance": "Currency stability, sovereign debt, sanctions exposure",
            "security": "Personnel safety, facility protection, evacuation planning",
        },
        "anchor_assessment": "Strait of Hormuz transit risk",
        "description": "Nine countries spanning the Persian Gulf energy corridor, "
                       "including the Strait of Hormuz chokepoint through which ~20% "
                       "of global oil supply transits.",
    },
}

# --- Prompt Versions ---
PROMPT_VERSIONS = {
    "extraction": "v2",
    "champs_baserate": "v1",
    "champs_analogy": "v1",
    "champs_decomp": "v1",
    "champs_devil": "v1",
    "supervisor": "v1",
    "contradiction": "v1",
    "ask_baserate": "v1",
    "ask_analogy": "v1",
    "ask_decomp": "v1",
    "ask_devil": "v1",
    "ask": "v1",
}

# --- Data Source URLs ---
GDELT_API_BASE = "http://api.gdeltproject.org/api/v2/doc/doc"
WORLD_BANK_API_BASE = "https://api.worldbank.org/v2"


def load_country_config(country_name: str) -> dict:
    """Load a country configuration JSON file."""
    path = COUNTRIES_DIR / f"{country_name}.json"
    with open(path, "r") as f:
        return json.load(f)


def load_prompt(prompt_name: str) -> str:
    """Load a prompt template file."""
    version = PROMPT_VERSIONS.get(prompt_name, "v1")
    path = PROMPTS_DIR / f"{prompt_name}_{version}.txt"
    with open(path, "r") as f:
        return f.read()


def load_all_country_configs() -> dict:
    """Load all country configs, keyed by ISO3 code."""
    configs = {}
    for country in COUNTRIES:
        cfg = load_country_config(country)
        configs[cfg["iso3"]] = cfg
    return configs


def load_all_available_country_configs() -> dict:
    """Load ALL country configs from the countries directory, not just active ones."""
    configs = {}
    for f in COUNTRIES_DIR.iterdir():
        if f.suffix == ".json":
            try:
                cfg = load_country_config(f.stem)
                configs[cfg["iso3"]] = cfg
            except (json.JSONDecodeError, KeyError, OSError):
                pass
    return configs
