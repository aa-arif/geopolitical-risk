"""
Pipeline health check. Runs in <2 seconds, no LLM calls.
Usage: python -m scripts.health_check
"""
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import COUNTRIES, load_country_config, DATA_DIR
from utils.db import initialize_db, get_connection


def main():
    initialize_db()
    conn = get_connection()
    today = datetime.now().astimezone().strftime("%Y-%m-%d")

    print(f"=== GeoRisk Health Check ({today}) ===")
    print()

    # Check today's pipeline log
    log_path = DATA_DIR / "pipeline_logs" / f"daily_{today}.log"
    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        errors = log_text.lower().count("error")
        warnings = log_text.lower().count("warning")
        print(f"Pipeline Status: TODAY'S RUN FOUND ({log_path.name})")
        print(f"  Errors in log: {errors}")
        print(f"  Warnings in log: {warnings}")
    else:
        print(f"Pipeline Status: NO RUN TODAY (expected: {log_path.name})")
    print()

    # Per-country data freshness
    print("Data Freshness:")
    healthy = 0
    warn_count = 0
    error_count = 0

    for country_name in COUNTRIES:
        config = load_country_config(country_name)
        iso3 = config["iso3"]

        # Latest prediction
        pred_row = conn.execute(
            "SELECT prediction_date FROM predictions WHERE country_iso3 = ? ORDER BY prediction_date DESC LIMIT 1",
            (iso3,)
        ).fetchone()
        pred_date = pred_row["prediction_date"] if pred_row else "never"

        # Article count
        article_count = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE country_iso3 = ?", (iso3,)
        ).fetchone()[0]

        # Determine status
        status = "OK"
        if pred_date == "never":
            status = "NO PREDICTIONS"
            error_count += 1
        else:
            from datetime import timedelta
            two_days_ago = (datetime.now().astimezone() - timedelta(days=2)).strftime("%Y-%m-%d")
            if pred_date < two_days_ago:
                status = "STALE"
                warn_count += 1
            else:
                healthy += 1

        if article_count < 5:
            if status == "OK":
                status = "LOW ARTICLES"
            warn_count += 1

        print(f"  {iso3}: last prediction {pred_date}, {article_count} articles  {status}")

    print()

    # GPR data
    gpr_path = DATA_DIR / "data_gpr_export.xls"
    if gpr_path.exists():
        size_mb = gpr_path.stat().st_size / (1024 * 1024)
        print(f"GPR Data: FOUND ({size_mb:.1f} MB)")
    else:
        print("GPR Data: NOT FOUND (download from https://www.matteoiacoviello.com/gpr.htm)")
    # Calibration model
    cal_path = DATA_DIR / "calibration_model.pkl"
    if cal_path.exists():
        print(f"Calibration Model: FOUND")
    else:
        print("Calibration Model: NOT FOUND (need 30+ resolved predictions)")

    # Fusion weight override
    weight_path = DATA_DIR / "fusion_weight_override.json"
    if weight_path.exists():
        try:
            w = json.loads(weight_path.read_text())
            print(f"Fusion Weight Override: {w['fusion_weight_track_a']:.2f} (updated {w.get('updated_at', 'unknown')})")
        except Exception:
            print("Fusion Weight Override: FOUND (parse error)")
    else:
        print("Fusion Weight Override: NOT FOUND (using default 0.60)")

    print()
    total = len(COUNTRIES)
    print(f"Summary: {healthy}/{total} countries healthy, {warn_count} warnings, {error_count} errors")

    conn.close()


if __name__ == "__main__":
    main()
