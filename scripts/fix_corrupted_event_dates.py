"""
Repair corrupted event_date values in conflict_events.

Pattern: GDELT seendate truncated to 10 chars, e.g. '20260414T0'.
Root-cause fix in pipeline/ingest.py stops new corruption; this script
repairs rows already written.

Strategy:
  - Rows where event_date matches YYYYMMDDTN (digits 0-7, 'T' at 8,
    digit at 9): parse YYYY-MM-DD prefix and UPDATE in place.
  - Rows whose event_date can't be salvaged: DELETE (no reliable date
    means the row can't participate in any time-windowed query).

Safe by default. Requires --apply to mutate; prompts for 'yes' before
writing. Expected scope as of 2026-04-17: 459 corrupt rows, all with
source='internal' (verified in production).

Usage:
    python scripts/fix_corrupted_event_dates.py            # dry run
    python scripts/fix_corrupted_event_dates.py --apply    # mutate (asks y/N)
"""
import argparse
import re
import sys
from datetime import datetime

sys.path.insert(0, ".")

from utils.db import get_connection

SALVAGEABLE = re.compile(r"^(\d{4})(\d{2})(\d{2})T\d$")


def _classify(conn):
    """Return (salvageable, unsalvageable) lists scanned from conflict_events.

    salvageable entries are (id, raw, iso); unsalvageable are (id, raw).
    """
    cursor = conn.execute(
        "SELECT id, event_date FROM conflict_events "
        "WHERE event_date NOT LIKE '____-__-__'"
    )
    salvageable, unsalvageable = [], []
    for row in cursor.fetchall():
        raw = row["event_date"] or ""
        m = SALVAGEABLE.match(raw)
        if m:
            iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            try:
                datetime.strptime(iso, "%Y-%m-%d")
                salvageable.append((row["id"], raw, iso))
                continue
            except ValueError:
                pass
        unsalvageable.append((row["id"], raw))
    return salvageable, unsalvageable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually mutate the DB. Without this flag, dry run only.")
    args = ap.parse_args()

    conn = get_connection()
    try:
        salvageable, unsalvageable = _classify(conn)
        total = len(salvageable) + len(unsalvageable)
        print(f"Corrupt rows found: {total}")
        print(f"  Salvageable (will UPDATE): {len(salvageable)}")
        print(f"  Unsalvageable (will DELETE): {len(unsalvageable)}")

        if salvageable:
            print("\nSample salvageable (up to 5):")
            for rid, raw, iso in salvageable[:5]:
                print(f"  id={rid}  '{raw}' -> '{iso}'")
        if unsalvageable:
            print("\nSample unsalvageable (up to 5):")
            for rid, raw in unsalvageable[:5]:
                print(f"  id={rid}  '{raw}'")

        if not args.apply:
            print("\nDry run. Re-run with --apply to mutate.")
            return

        if total == 0:
            print("\nNothing to do.")
            return

        confirm = input(
            f"\nAbout to UPDATE {len(salvageable)} and DELETE "
            f"{len(unsalvageable)} rows. Type 'yes' to proceed: "
        )
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return

        for rid, _raw, iso in salvageable:
            conn.execute(
                "UPDATE conflict_events SET event_date = ? WHERE id = ?",
                (iso, rid),
            )
        if unsalvageable:
            conn.executemany(
                "DELETE FROM conflict_events WHERE id = ?",
                [(rid,) for rid, _ in unsalvageable],
            )
        conn.commit()
        print(f"Updated {len(salvageable)}, deleted {len(unsalvageable)}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
