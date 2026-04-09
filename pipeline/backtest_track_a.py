"""
Track A backtesting pipeline.

Runs the structural model against historical ACLED data (2024-2025)
to evaluate calibration. No LLM calls -- runs in seconds.

For each country-month:
1. Compute Track A probability (static structural inputs)
2. Compute 90th percentile threshold from preceding 12 months
3. Count actual ACLED events in that month
4. Determine outcome: events > threshold -> 1, else 0
5. Compute Brier score

Then aggregate: overall Brier, decomposition, calibration curve.
"""

import sys
import numpy as np
from datetime import datetime

sys.path.insert(0, ".")

from config.settings import COUNTRIES, load_country_config
from utils.db import initialize_db, get_connection
from track_a.pitf_model import compute_pitf_probability
from track_a.fearon_laitin import compute_structural_vulnerability
from track_a.gpr_trend import get_gpr_trend
from track_a.predict import predict_track_a
from evaluation.brier import brier_score, brier_score_aggregate
from evaluation.decompose import decompose_brier
from evaluation.calibration_curve import compute_calibration_curve


def get_monthly_event_counts(conn, country_iso3):
    """Get event counts grouped by month for a country."""
    cursor = conn.execute(
        """SELECT strftime('%Y-%m', event_date) as month,
                  COUNT(*) as cnt,
                  SUM(fatalities) as fatalities
           FROM acled_events
           WHERE country_iso3 = ?
           GROUP BY month
           ORDER BY month""",
        (country_iso3,),
    )
    return {r["month"]: {"count": r["cnt"], "fatalities": r["fatalities"] or 0}
            for r in cursor.fetchall()}


def compute_rolling_threshold(monthly_counts, target_month, lookback=12):
    """
    Compute 90th percentile threshold from months preceding target_month.
    Uses up to `lookback` prior months.
    """
    sorted_months = sorted(monthly_counts.keys())
    prior = [monthly_counts[m]["count"] for m in sorted_months if m < target_month]

    if len(prior) < 2:
        return None  # Not enough history

    # Use last `lookback` months
    prior = prior[-lookback:]
    return float(np.percentile(prior, 90))


def run_backtest():
    initialize_db()
    conn = get_connection()

    # Create backtest results table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_iso3 TEXT NOT NULL,
            month TEXT NOT NULL,
            track_a_probability REAL,
            event_count INTEGER,
            threshold REAL,
            outcome INTEGER,
            brier_score REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("DELETE FROM backtest_results")  # Clear previous runs
    conn.commit()

    all_predictions = []
    all_actuals = []
    all_rows = []

    print("=" * 100)
    print("TRACK A BACKTESTING: Structural Model vs ACLED 2024-2025")
    print("=" * 100)

    for country_name in COUNTRIES:
        config = load_country_config(country_name)
        iso3 = config["iso3"]

        # Get Track A probability (same for every month -- static inputs)
        track_a = predict_track_a(config, conn)
        p_a = track_a["probability"]

        # Get monthly event counts
        monthly = get_monthly_event_counts(conn, iso3)
        if not monthly:
            print(f"\n{config['name']}: No ACLED data available. Skipping.")
            continue

        sorted_months = sorted(monthly.keys())

        print(f"\n{'=' * 100}")
        print(f"{config['name']} ({iso3})  |  Track A = {p_a*100:.1f}%  |  "
              f"PITF = {track_a['components']['pitf_base']*100:.1f}%  |  "
              f"Months: {sorted_months[0]} to {sorted_months[-1]}")
        print(f"{'=' * 100}")
        print(f"  {'Month':>7}  {'P(A)':>6}  {'Events':>7}  {'Threshold':>10}  "
              f"{'Outcome':>8}  {'Brier':>8}  {'Note':>20}")
        print(f"  {'-'*7}  {'-'*6}  {'-'*7}  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*20}")

        country_preds = []
        country_actuals = []

        for month in sorted_months:
            events = monthly[month]["count"]
            threshold = compute_rolling_threshold(monthly, month, lookback=12)

            if threshold is None:
                print(f"  {month:>7}  {p_a*100:>5.1f}%  {events:>7}  {'(no hist)':>10}  "
                      f"{'--':>8}  {'--':>8}  {'skip: <2 prior months':>20}")
                continue

            outcome = 1 if events > threshold else 0
            bs = brier_score(p_a, outcome)

            note = ""
            if outcome == 1:
                note = f"EXCEEDED ({events}>{threshold:.0f})"
            else:
                note = f"below ({events}<={threshold:.0f})"

            all_predictions.append(p_a)
            all_actuals.append(outcome)
            country_preds.append(p_a)
            country_actuals.append(outcome)

            all_rows.append({
                "country_iso3": iso3,
                "month": month,
                "track_a_probability": p_a,
                "event_count": events,
                "threshold": threshold,
                "outcome": outcome,
                "brier_score": bs,
            })

            outcome_str = "YES" if outcome == 1 else "no"
            print(f"  {month:>7}  {p_a*100:>5.1f}%  {events:>7}  {threshold:>10.0f}  "
                  f"  {outcome_str:>6}  {bs:>7.4f}  {note:>20}")

        if country_preds:
            c_brier = brier_score_aggregate(country_preds, country_actuals)
            c_base_rate = sum(country_actuals) / len(country_actuals)
            print(f"  {'':>7}  {'':>6}  {'':>7}  {'':>10}  "
                  f"{'':>8}  {'':>8}")
            print(f"  Country Brier: {c_brier:.4f}  |  "
                  f"Base rate: {c_base_rate:.2f}  |  "
                  f"Months evaluated: {len(country_preds)}  |  "
                  f"Instability months: {sum(country_actuals)}")

    # Save to database
    for row in all_rows:
        conn.execute(
            """INSERT INTO backtest_results
               (country_iso3, month, track_a_probability, event_count,
                threshold, outcome, brier_score)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (row["country_iso3"], row["month"], row["track_a_probability"],
             row["event_count"], row["threshold"], row["outcome"],
             row["brier_score"]),
        )
    conn.commit()

    # Aggregate statistics
    print("\n" + "=" * 100)
    print("AGGREGATE RESULTS")
    print("=" * 100)

    if not all_predictions:
        print("No predictions to evaluate.")
        conn.close()
        return

    agg_brier = brier_score_aggregate(all_predictions, all_actuals)
    base_rate = sum(all_actuals) / len(all_actuals)
    decomp = decompose_brier(all_predictions, all_actuals, n_bins=5)
    cal = compute_calibration_curve(all_predictions, all_actuals, n_bins=5)

    print(f"  Total country-months evaluated:  {len(all_predictions)}")
    print(f"  Instability months (outcome=1):  {sum(all_actuals)}")
    print(f"  Base rate:                       {base_rate:.3f}")
    print(f"  Aggregate Brier score:           {agg_brier:.4f}")
    print(f"  Always-predict-base-rate Brier:  {base_rate * (1 - base_rate):.4f}")
    print(f"  Skill vs naive:                  {'YES' if agg_brier < base_rate * (1 - base_rate) else 'NO'}")
    print()
    print("  Brier Decomposition:")
    print(f"    Uncertainty:  {decomp['uncertainty']:.4f}")
    print(f"    Reliability:  {decomp['reliability']:.4f}  (lower = better calibrated)")
    print(f"    Resolution:   {decomp['resolution']:.4f}  (higher = better discrimination)")
    print()

    print("  Calibration Curve (5 bins):")
    print(f"  {'Bin':>12}  {'Predicted':>10}  {'Actual':>10}  {'Count':>6}  {'Error':>8}")
    print(f"  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*6}  {'-'*8}")
    for b in cal["bins"]:
        print(f"  {b['bin_lower']:.2f}-{b['bin_upper']:.2f}  "
              f"{b['avg_predicted']*100:>9.1f}%  "
              f"{b['avg_actual']*100:>9.1f}%  "
              f"{b['count']:>6}  "
              f"{b['calibration_error']*100:>7.1f}%")
    print(f"  Expected Calibration Error (ECE): {cal['expected_calibration_error']*100:.2f}%")

    print()
    print("  Interpretation:")
    if agg_brier < base_rate * (1 - base_rate):
        print("  The structural model has positive skill over a naive base-rate predictor.")
    else:
        print("  The structural model does NOT outperform a naive base-rate predictor.")
        print("  This is expected: Track A outputs the same probability every month per country.")
        print("  It provides calibrated base rates, not month-to-month discrimination.")
        print("  Track B (LLM agents) is needed for temporal discrimination.")

    print()
    print(f"  Results saved to backtest_results table ({len(all_rows)} rows).")

    conn.close()


if __name__ == "__main__":
    run_backtest()
