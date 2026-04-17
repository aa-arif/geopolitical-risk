"""
Read-only dry-run of the May 15 resolution cycle.

Exercises the existing resolution helpers and the P0.2 ingest-confidence
flag against current production data so bugs surface before real
resolutions land. Does NOT write to the database.

Usage:
    python -m scripts.audit_resolution_readiness
    python -m scripts.audit_resolution_readiness --date 2026-04-17
    python -m scripts.audit_resolution_readiness --verbose
"""
import sys
import argparse
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from config.settings import EVENT_TYPE_RESOLUTION
from utils.db import get_connection, initialize_db
from evaluation.brier import brier_score, brier_score_aggregate

# Read-only usage of internal resolve helpers -- do NOT mutate db.
from evaluation.resolve import (
    _resolve_by_threshold,
    _resolve_by_occurrence,
    _resolve_composite_legacy,
    compute_ingest_deviation,
)


PROB_BUCKETS = [
    (0.0, 0.1, "0.00-0.10"),
    (0.1, 0.25, "0.10-0.25"),
    (0.25, 0.5, "0.25-0.50"),
    (0.5, 0.75, "0.50-0.75"),
    (0.75, 1.01, "0.75-1.00"),
]


def _bucket_for(prob):
    for lo, hi, label in PROB_BUCKETS:
        if lo <= prob < hi:
            return label
    return "0.75-1.00"


def _all_event_types():
    return ["composite", "ACE", "MCU", "REC", "PSS", "CID"]


def _fetch_predictions(conn):
    rows = conn.execute(
        """SELECT id, country_iso3, prediction_date, window_end_date,
                  COALESCE(event_type, 'composite') AS event_type,
                  calibrated_probability, event_threshold, resolved
           FROM predictions
           ORDER BY prediction_date, country_iso3, event_type"""
    ).fetchall()
    return [dict(r) for r in rows]


def section_1_schedule(preds, today):
    print("Section 1 -- Resolution schedule (next 60 days)")
    print()
    end = today + timedelta(days=60)
    by_date = defaultdict(int)
    for p in preds:
        if p["resolved"]:
            continue
        try:
            wed = datetime.strptime(p["window_end_date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if today.date() <= wed <= end.date():
            by_date[wed] += 1

    if not by_date:
        print("  (no unresolved prediction windows closing in the next 60 days)")
        print()
        return

    print(f"  {'Date':<12} {'Resolving':>10} {'Cumulative':>12}")
    print(f"  {'-'*12} {'-'*10} {'-'*12}")
    cumulative = 0
    for d in sorted(by_date):
        cumulative += by_date[d]
        print(f"  {d.isoformat():<12} {by_date[d]:>10d} {cumulative:>12d}")
    print()


def section_2_mix(preds, today):
    print("Section 2 -- Prediction mix for windows closing in next 30 days")
    print()
    horizon = (today + timedelta(days=30)).date()
    in_scope = []
    for p in preds:
        if p["resolved"]:
            continue
        try:
            wed = datetime.strptime(p["window_end_date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if today.date() <= wed <= horizon:
            in_scope.append(p)

    print(f"  Total predictions in scope: {len(in_scope)}")
    print()

    by_country = defaultdict(int)
    by_event_type = defaultdict(int)
    by_bucket = defaultdict(int)
    for p in in_scope:
        by_country[p["country_iso3"]] += 1
        by_event_type[p["event_type"]] += 1
        if p["calibrated_probability"] is not None:
            by_bucket[_bucket_for(p["calibrated_probability"])] += 1

    print("  By country:")
    for iso3 in sorted(by_country):
        print(f"    {iso3:<6} {by_country[iso3]:>4d}")
    print()

    print("  By event type:")
    for et in _all_event_types():
        print(f"    {et:<10} {by_event_type.get(et, 0):>4d}")
    print()

    print("  By calibrated probability:")
    for _, _, label in PROB_BUCKETS:
        print(f"    {label:<12} {by_bucket.get(label, 0):>4d}")
    print()

    return in_scope


def section_3_threshold_coverage(preds, today):
    print("Section 3 -- Stored threshold coverage (percentile_90 types)")
    print()
    percentile_types = [
        et for et, cfg in EVENT_TYPE_RESOLUTION.items()
        if cfg.get("threshold_method") == "percentile_90"
    ]

    pair_stats = defaultdict(lambda: {"total": 0, "null": 0})
    null_predictions = []
    horizon = (today + timedelta(days=30)).date()
    for p in preds:
        if p["resolved"] or p["event_type"] not in percentile_types:
            continue
        try:
            wed = datetime.strptime(p["window_end_date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if wed > horizon:
            continue
        key = (p["country_iso3"], p["event_type"])
        pair_stats[key]["total"] += 1
        if p["event_threshold"] is None:
            pair_stats[key]["null"] += 1
            null_predictions.append(p)

    if not pair_stats:
        print("  (no percentile_90 predictions resolving within 30 days)")
        print()
        return null_predictions

    print(f"  {'Country':<8} {'Type':<6} {'Total':>6} {'NullThr':>8}")
    print(f"  {'-'*8} {'-'*6} {'-'*6} {'-'*8}")
    for key in sorted(pair_stats):
        iso3, et = key
        s = pair_stats[key]
        print(f"  {iso3:<8} {et:<6} {s['total']:>6d} {s['null']:>8d}")
    print()
    print(f"  Predictions with NULL stored threshold: {len(null_predictions)}")
    print()
    return null_predictions


def _dry_run_single(conn, pred, today_str):
    """Dispatch to the right resolve helper. Returns resolution dict or None."""
    iso3 = pred["country_iso3"]
    pred_date = pred["prediction_date"]
    event_type = pred["event_type"]
    stored_threshold = pred["event_threshold"]

    if event_type == "composite":
        return _resolve_composite_legacy(
            conn, iso3, pred_date, today_str, stored_threshold
        )

    if event_type not in EVENT_TYPE_RESOLUTION:
        return None

    config = EVENT_TYPE_RESOLUTION[event_type]
    method = config.get("threshold_method")
    if method == "percentile_90":
        return _resolve_by_threshold(
            conn, iso3, pred_date, today_str, event_type, config,
            stored_threshold=stored_threshold,
        )
    if method == "occurrence":
        return _resolve_by_occurrence(
            conn, iso3, pred_date, today_str, event_type, config,
        )
    return None


def section_4_dry_run(conn, preds, today, verbose):
    print("Section 4 -- Dry-run resolutions (treating today as synthetic window_end)")
    print()
    today_str = today.strftime("%Y-%m-%d")
    cutoff = (today - timedelta(days=1)).date()

    eligible = []
    for p in preds:
        try:
            pd = datetime.strptime(p["prediction_date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if pd <= cutoff:
            eligible.append(p)

    results = []
    errors = []
    for p in eligible:
        try:
            resolution = _dry_run_single(conn, p, today_str)
        except Exception as exc:  # noqa: BLE001 - see all bugs
            errors.append((p["id"], type(exc).__name__, str(exc)))
            continue

        if resolution is None:
            results.append({"pred": p, "resolution": None, "brier": None})
            continue

        cal = p["calibrated_probability"]
        bs = brier_score(cal, resolution["actual_outcome"]) if cal is not None else None
        results.append({"pred": p, "resolution": resolution, "brier": bs})

    resolvable = [r for r in results if r["resolution"] is not None]
    resolved_1 = sum(1 for r in resolvable if r["resolution"]["actual_outcome"] == 1)
    resolved_0 = sum(1 for r in resolvable if r["resolution"]["actual_outcome"] == 0)
    no_data = sum(1 for r in results if r["resolution"] is None)

    briers = [r["brier"] for r in resolvable if r["brier"] is not None]
    agg_brier = float(np.mean(briers)) if briers else None

    print(f"  Total predictions with partial windows: {len(eligible)}")
    print(f"  Would resolve to 1 (event exceeded threshold):     {resolved_1}")
    print(f"  Would resolve to 0 (no exceedance):                {resolved_0}")
    print(f"  No data to resolve against:                        {no_data}")
    print(f"  Dispatch errors (see warnings below):              {len(errors)}")
    if agg_brier is not None:
        print(f"  Aggregate hypothetical Brier: {agg_brier:.4f}")
    else:
        print("  Aggregate hypothetical Brier: n/a")
    print()
    print("  Note: partial windows skew outcomes toward 0 because events that "
          "would have occurred on days 25-30 have not happened yet. This is a "
          "code-path exercise, not an accuracy measurement.")
    print()

    if verbose and resolvable:
        print("  Per-prediction detail:")
        print(f"    {'ID':>5} {'ISO3':<5} {'Type':<9} {'PredDate':<11} "
              f"{'WindowEnd':<11} {'Prob':>6} {'Thresh':>8} "
              f"{'Events':>7} {'Outcome':>8} {'Brier':>7}")
        for r in resolvable:
            p = r["pred"]
            res = r["resolution"]
            cal = p["calibrated_probability"]
            cal_str = f"{cal:.3f}" if cal is not None else "  n/a"
            br_str = f"{r['brier']:.4f}" if r["brier"] is not None else " n/a "
            thr = res.get("threshold")
            thr_str = f"{thr:.2f}" if isinstance(thr, (int, float)) else str(thr)
            print(f"    {p['id']:>5d} {p['country_iso3']:<5} "
                  f"{p['event_type']:<9} {p['prediction_date']:<11} "
                  f"{p['window_end_date']:<11} {cal_str:>6} {thr_str:>8} "
                  f"{res['event_count']:>7d} "
                  f"{res['actual_outcome']:>8d} {br_str:>7}")
        print()

    return results, errors


def section_5_ingest_confidence(conn, preds, today, verbose):
    print("Section 5 -- Ingest confidence preview (P0.2 code path)")
    print()
    today_str = today.strftime("%Y-%m-%d")
    cutoff = (today - timedelta(days=1)).date()

    eligible = []
    for p in preds:
        try:
            pd = datetime.strptime(p["prediction_date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if pd <= cutoff:
            eligible.append(p)

    flag_counts = defaultdict(int)
    by_country = defaultdict(lambda: {"high": 0, "low": 0, "unknown": 0})
    sigmas = []
    errors = []

    for p in eligible:
        event_category = "ACE" if p["event_type"] == "composite" else p["event_type"]
        try:
            sigma, flag = compute_ingest_deviation(
                conn, p["country_iso3"], p["prediction_date"],
                today_str, event_category,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append((p["id"], type(exc).__name__, str(exc)))
            continue

        flag_counts[flag] += 1
        by_country[p["country_iso3"]][flag] += 1
        if sigma is not None:
            sigmas.append(sigma)

    total = sum(flag_counts.values())
    if total == 0:
        print("  (no predictions eligible for ingest-confidence preview)")
        print()
        return flag_counts, by_country, sigmas, errors

    def _pct(n):
        return (100.0 * n / total) if total else 0.0

    print(f"  high:    {flag_counts.get('high', 0):>4d}  ({_pct(flag_counts.get('high', 0)):5.1f}%)")
    print(f"  low:     {flag_counts.get('low', 0):>4d}  ({_pct(flag_counts.get('low', 0)):5.1f}%)")
    print(f"  unknown: {flag_counts.get('unknown', 0):>4d}  ({_pct(flag_counts.get('unknown', 0)):5.1f}%)")
    print()

    print("  Breakdown by country (H/L/U):")
    print(f"    {'ISO3':<6} {'High':>5} {'Low':>5} {'Unk':>5}")
    for iso3 in sorted(by_country):
        c = by_country[iso3]
        print(f"    {iso3:<6} {c['high']:>5d} {c['low']:>5d} {c['unknown']:>5d}")
    print()

    if sigmas:
        arr = np.array(sigmas, dtype=float)
        print("  Sigma distribution (predictions with numeric sigma):")
        print(f"    n:      {len(arr)}")
        print(f"    min:    {arr.min():.3f}")
        print(f"    p25:    {np.percentile(arr, 25):.3f}")
        print(f"    median: {np.percentile(arr, 50):.3f}")
        print(f"    p75:    {np.percentile(arr, 75):.3f}")
        print(f"    max:    {arr.max():.3f}")
    else:
        print("  Sigma distribution: no predictions produced a numeric sigma")
    print()

    if verbose:
        print("  Per-country sigma samples not printed (use db directly for detail).")
        print()

    return flag_counts, by_country, sigmas, errors


def section_6_warnings(preds, today, in_scope_mix, null_thresholds,
                        dry_run_errors, confidence_by_country,
                        confidence_errors):
    print("Section 6 -- WARNINGS")
    print()
    warnings = []

    countries = {p["country_iso3"] for p in preds}
    event_types = set(_all_event_types())

    mix_countries = {p["country_iso3"] for p in in_scope_mix}
    mix_event_types = {p["event_type"] for p in in_scope_mix}

    for iso3 in sorted(countries - mix_countries):
        warnings.append(f"Country {iso3} has zero predictions resolving in next 30 days.")
    for et in sorted(event_types - mix_event_types):
        warnings.append(f"Event type '{et}' has zero predictions resolving in next 30 days.")

    if null_thresholds:
        warnings.append(
            f"{len(null_thresholds)} percentile_90 predictions have NULL event_threshold "
            "(will fall back to live recomputation at resolution time)."
        )

    for country, flags in sorted(confidence_by_country.items()):
        total = flags["high"] + flags["low"] + flags["unknown"]
        if total > 0 and flags["high"] == 0 and flags["low"] == 0:
            warnings.append(
                f"Country {country}: every prediction returns 'unknown' confidence "
                f"({total} predictions). Baseline ingest is too sparse."
            )

    for pid, etype, msg in dry_run_errors:
        warnings.append(f"Dry-run exception on prediction {pid} ({etype}): {msg}")
    for pid, etype, msg in confidence_errors:
        warnings.append(f"Ingest-confidence exception on prediction {pid} ({etype}): {msg}")

    if not warnings:
        print("  (no warnings)")
        print()
        return

    for w in warnings:
        print(f"  - {w}")
    print()


def run_audit(conn, today: datetime, verbose: bool = False):
    """Orchestrate the six report sections. Read-only."""
    today_str = today.strftime("%Y-%m-%d")
    print(f"=== Resolution Readiness Audit ({today_str}) ===")
    print("Read-only. No rows are inserted, updated, or deleted.")
    print()

    preds = _fetch_predictions(conn)
    print(f"Total predictions in db: {len(preds)} "
          f"({sum(1 for p in preds if p['resolved'])} resolved, "
          f"{sum(1 for p in preds if not p['resolved'])} unresolved)")
    print()

    section_1_schedule(preds, today)
    in_scope_mix = section_2_mix(preds, today) or []
    null_thresholds = section_3_threshold_coverage(preds, today) or []
    _, dry_run_errors = section_4_dry_run(conn, preds, today, verbose)
    _, confidence_by_country, _, confidence_errors = section_5_ingest_confidence(
        conn, preds, today, verbose,
    )
    section_6_warnings(
        preds, today, in_scope_mix, null_thresholds,
        dry_run_errors, confidence_by_country, confidence_errors,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date", default=None,
        help="Synthetic current date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print per-prediction detail in Sections 4 and 5.",
    )
    args = parser.parse_args(argv)

    if args.date:
        today = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        today = datetime.now().astimezone().replace(tzinfo=None)

    initialize_db()
    conn = get_connection()
    try:
        run_audit(conn, today, verbose=args.verbose)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
