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
ACLED_EMAIL = os.environ.get("ACLED_EMAIL", "")
ACLED_PASSWORD = os.environ.get("ACLED_PASSWORD", "")
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")

# --- LLM Model Versions (pinned for reproducibility) ---
MODEL_SONNET = "claude-sonnet-4-20250514"
MODEL_OPUS = "claude-opus-4-20250514"
MODEL_HAIKU = "claude-haiku-4-5-20251001"

# --- Fusion Parameters ---
FUSION_WEIGHT_TRACK_A = 0.6  # Weight favoring Track A (structural model)
EXTREMIZING_PARAMETER = 1.0  # 1.0 = no extremizing; >1.0 = push away from 0.5

# --- Prediction Parameters ---
PREDICTION_WINDOW_DAYS = 30
ACLED_THRESHOLD_PERCENTILE = 90  # 90th percentile of 12-month country average
NEIGHBORHOOD_LOOKBACK_DAYS = 90

# --- Countries ---
COUNTRIES = [
    "nigeria", "bangladesh", "pakistan", "philippines", "turkey",
    "ethiopia", "myanmar", "iraq", "colombia", "sudan",
    "cod", "egypt", "thailand", "kenya", "ukraine",
    "somalia", "yemen", "afghanistan", "libya", "mali",
    "mozambique", "venezuela", "haiti", "lebanon", "southafrica",
    "india", "mexico", "niger", "cameroon", "chad",
]

# --- Prompt Versions ---
PROMPT_VERSIONS = {
    "extraction": "v2",
    "champs_baserate": "v1",
    "champs_analogy": "v1",
    "champs_decomp": "v1",
    "champs_devil": "v1",
    "supervisor": "v1",
    "contradiction": "v1",
}

# --- Data Source URLs ---
ACLED_API_BASE = "https://acleddata.com/api/acled/read"
ACLED_TOKEN_URL = "https://acleddata.com/oauth/token"
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
