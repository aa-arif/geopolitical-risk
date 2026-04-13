"""
Post-resolution analysis: validates Track A vs Track B accuracy.
Run after prediction windows close (30+ days after first predictions).
Usage: python -m scripts.analyze_resolution
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import COUNTRIES, load_country_config, DATA_DIR
from utils.db import initialize_db, get_connection, get_resolved_predictions, get_agent_outputs
from evaluation.brier import brier_score, brier_score_aggregate
from evaluation.decompose import decompose_brier
from evaluation.calibration_curve import compute_calibration_curve
from evaluation.track_comparison import compare_tracks
from evaluation.learning import analyze_agent_accuracy


def main():
    initialize_db()
    conn = get_connection()

    resolved = get_resolved_predictions(conn)
    resolved_with_outcome = [r for r in resolved if r.get("actual_outcome") is not None]

    print("=== Prediction Resolution Analysis ===")

    if not resolved_with_outcome:
        print("No resolved predictions yet. Prediction windows are 30 days.")
        print("First predictions were made ~April 7-11, 2026.")
        print("Check back after May 7, 2026 for resolution data.")
        conn.close()
        return

    n = len(resolved_with_outcome)
    actuals = [r["actual_outcome"] for r in resolved_with_outcome]
    base_rate = sum(actuals) / len(actuals)

    print(f"Resolved predictions: {n}")
    print(f"Base rate: {base_rate:.0%} of windows had instability events")
    print()

    # Overall Brier scores
    cal_preds = [r["calibrated_probability"] for r in resolved_with_outcome]
    a_preds = [r["track_a_probability"] for r in resolved_with_outcome]
    b_preds = [r["track_b_probability"] for r in resolved_with_outcome]
    fused_preds = [r["fused_probability"] for r in resolved_with_outcome]

    cal_brier = brier_score_aggregate(cal_preds, actuals)
    a_brier = brier_score_aggregate(a_preds, actuals)
    b_brier = brier_score_aggregate(b_preds, actuals)
    f_brier = brier_score_aggregate(fused_preds, actuals)

    print("Overall Brier Scores:")
    print(f"  Track A (structural):   {a_brier:.4f}")
    print(f"  Track B (LLM ensemble): {b_brier:.4f}")
    print(f"  Fused (w=0.60):         {f_brier:.4f}")
    print(f"  Calibrated:             {cal_brier:.4f}")
    print()

    # Verdict
    if b_brier < a_brier:
        diff = a_brier - b_brier
        print(f"VERDICT: Track B BEATS Track A by {diff:.4f} Brier points")
    elif a_brier < b_brier:
        diff = b_brier - a_brier
        print(f"VERDICT: Track A BEATS Track B by {diff:.4f} Brier points")
    else:
        print("VERDICT: Track A and Track B are tied")
    print()

    # Per-agent performance
    agent_analysis = analyze_agent_accuracy(conn)
    if agent_analysis["agents"]:
        print("Per-Agent Performance:")
        for a in agent_analysis["agents"]:
            print(f"  {a['agent_type']:15s} {a['avg_brier']:.4f} ({a['n_predictions']} predictions)")
        print()

    # Calibration
    cal = compute_calibration_curve(cal_preds, actuals, n_bins=5)
    print(f"Calibration (ECE): {cal['expected_calibration_error']:.3f}")
    print()

    # Decomposition
    decomp = decompose_brier(cal_preds, actuals)
    print("Brier Decomposition:")
    print(f"  Uncertainty:  {decomp['uncertainty']:.4f}")
    print(f"  Reliability:  {decomp['reliability']:.4f}")
    print(f"  Resolution:   {decomp['resolution']:.4f}")
    print()

    # Per-country
    print("Per-Country:")
    for country_name in COUNTRIES:
        config = load_country_config(country_name)
        iso3 = config["iso3"]
        country_resolved = [r for r in resolved_with_outcome if r["country_iso3"] == iso3]
        if not country_resolved:
            continue
        c_actuals = [r["actual_outcome"] for r in country_resolved]
        c_a = brier_score_aggregate([r["track_a_probability"] for r in country_resolved], c_actuals)
        c_b = brier_score_aggregate([r["track_b_probability"] for r in country_resolved], c_actuals)
        c_f = brier_score_aggregate([r["fused_probability"] for r in country_resolved], c_actuals)
        winner = "B wins" if c_b < c_a else "A wins" if c_a < c_b else "tie"
        print(f"  {iso3}: A={c_a:.2f}, B={c_b:.2f}, Fused={c_f:.2f}  ({winner})")

    # Save report
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_resolved": n,
        "base_rate": base_rate,
        "brier_track_a": a_brier,
        "brier_track_b": b_brier,
        "brier_fused": f_brier,
        "brier_calibrated": cal_brier,
        "decomposition": decomp,
        "calibration_ece": cal["expected_calibration_error"],
        "agent_accuracy": agent_analysis,
    }
    report_path = DATA_DIR / "resolution_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nReport saved to {report_path}")

    conn.close()


if __name__ == "__main__":
    main()
