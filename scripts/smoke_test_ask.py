"""
End-to-end smoke test for the ask-a-question pipeline.

Calls generate_ask_forecast() directly (bypassing the API) with hand-written
scenarios across several countries. Prints the result dict for each, plus
elapsed time. Does NOT clean up -- rows stay in the DB for manual inspection.

Run twice to confirm the cache path works: first call goes through the full
4-agent pipeline, second call with the same inputs returns the cached row.

    python -m scripts.smoke_test_ask
"""

import hashlib
import json
import sys
import time
from datetime import datetime

from config.settings import load_all_country_configs
from track_b.ask import generate_ask_forecast
from utils.db import get_connection, initialize_db, insert_subscriber, insert_agent_outputs


SCENARIOS = [
    ("IRN", "significant escalation in Iran-Israel tensions", "2026-07-15"),
    ("SDN", "RSF captures a major city", "2026-06-01"),
    ("BGD", "elections held on schedule without postponement", "2026-05-30"),
    ("PAK", "civilian government dissolution", "2026-08-15"),
]

SMOKE_TEST_EMAIL = "smoketest@precursion.internal"


def _request_hash(iso3, scenario, deadline, email):
    raw = f"{iso3.upper()}|{scenario.strip().lower()}|{deadline}|{email.lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def run_one(country_config, scenario, deadline_iso, conn):
    iso3 = country_config["iso3"]
    request_hash = _request_hash(iso3, scenario, deadline_iso, SMOKE_TEST_EMAIL)

    cached = conn.execute(
        """SELECT id, created_at, reasoning_summary, response_time_ms
           FROM predictions
           WHERE source = 'ask' AND request_hash = ?
           AND datetime(created_at) > datetime('now', '-24 hours')
           ORDER BY id DESC LIMIT 1""",
        (request_hash,),
    ).fetchone()

    if cached:
        stored = json.loads(cached["reasoning_summary"]) if cached["reasoning_summary"] else {}
        print(f"  CACHE HIT id={cached['id']} originally_at={cached['created_at']} "
              f"elapsed_ms={cached['response_time_ms']}")
        print(f"  P={stored.get('probability'):.3f} confidence={stored.get('confidence')}")
        return {"question_id": cached["id"], "cached": True, "result": stored}

    start = time.monotonic()
    result = generate_ask_forecast(
        country_config=country_config,
        scenario=scenario,
        user_deadline=deadline_iso,
        user_email=SMOKE_TEST_EMAIL,
        conn=conn,
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)

    today_iso = datetime.now().astimezone().strftime("%Y-%m-%d")
    stored_payload = {k: v for k, v in result.items() if k != "ensemble_results"}

    cursor = conn.execute(
        """INSERT INTO predictions
             (country_iso3, prediction_date, window_end_date, event_type,
              track_a_probability, track_b_probability, fused_probability,
              extremized_probability, calibrated_probability,
              reasoning_summary, source, custom_scenario, user_email,
              user_deadline, response_time_ms, cost_usd, request_hash)
           VALUES (?, ?, ?, 'ask', ?, ?, ?, NULL, NULL, ?, 'ask', ?, ?, ?, ?, NULL, ?)""",
        (
            iso3, today_iso, deadline_iso,
            result["track_a_baseline"], result["probability"], result["probability"],
            json.dumps(stored_payload, default=str),
            scenario, SMOKE_TEST_EMAIL, deadline_iso, elapsed_ms, request_hash,
        ),
    )
    prediction_id = cursor.lastrowid
    conn.commit()

    insert_agent_outputs(conn, prediction_id, iso3, result["ensemble_results"])

    print(f"  CACHE MISS id={prediction_id} elapsed_ms={elapsed_ms}")
    print(f"  P={result['probability']:.3f} confidence={result['confidence']} "
          f"track_a={result['track_a_baseline']:.3f}")
    print(f"  summary: {result.get('executive_summary','')[:200]}")
    agents = result.get("agent_breakdown", {})
    for atype in ("baserate", "analogy", "decomposition", "devil"):
        p = agents.get(atype, {}).get("probability")
        if p is not None:
            print(f"    {atype}: {p:.3f}")
    return {"question_id": prediction_id, "cached": False, "result": result}


def main():
    initialize_db()
    configs = load_all_country_configs()
    conn = get_connection()
    try:
        try:
            insert_subscriber(conn, email=SMOKE_TEST_EMAIL, tier="free")
        except Exception as e:
            if "UNIQUE constraint" not in str(e):
                raise

        total_start = time.monotonic()
        summary = []
        for iso3, scenario, deadline_iso in SCENARIOS:
            print(f"\n=== {iso3}: {scenario} by {deadline_iso} ===", flush=True)
            if iso3 not in configs:
                print(f"  SKIP: country {iso3} not in load_all_country_configs()")
                summary.append((iso3, "skipped"))
                continue
            try:
                out = run_one(configs[iso3], scenario, deadline_iso, conn)
                summary.append((iso3, "cached" if out["cached"] else "fresh"))
            except Exception as e:
                print(f"  FAILED: {e.__class__.__name__}: {e}")
                summary.append((iso3, f"failed: {e.__class__.__name__}"))

        total_elapsed = time.monotonic() - total_start
        print("\n=== Summary ===")
        for iso3, status in summary:
            print(f"  {iso3}: {status}")
        print(f"Total elapsed: {total_elapsed:.1f}s")
        successes = sum(1 for _, s in summary if s in ("fresh", "cached"))
        print(f"Successful: {successes}/{len(SCENARIOS)}")
        return 0 if successes >= 3 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
