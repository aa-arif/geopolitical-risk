"""
Repair corrupted event_date values in conflict_events.

Two known corruption patterns:
  1. GDELT truncation: event_date like '20260414T0' (10-char slice of
     a YYYYMMDDTHHMMSSZ seendate). Salvageable via regex on event_date
     itself.
  2. RFC 2822 truncation: event_date like 'Mon, 16 Ma' (10-char slice
     of a feedparser entry.published string). The event_date itself
     is unrecoverable, but articles.published_date still holds the
     full RFC 2822 string, which _parse_publication_date can parse.

Strategy:
  - Pattern 1: regex-match YYYYMMDDTN on event_date -> UPDATE.
  - Pattern 2: look up articles.published_date via source_article_id
    and run _parse_publication_date -> UPDATE.
  - Anything still unparseable: FLAG for manual review. Do NOT delete.
    If unsalvageable rows exist, that's a third ingest path we haven't
    found and those rows deserve human eyes.

Safe by default. Requires --apply to mutate; prompts for 'yes' before
writing.

Usage:
    python scripts/fix_corrupted_event_dates.py            # dry run
    python scripts/fix_corrupted_event_dates.py --apply    # mutate (asks y/N)
"""
import argparse
import re
import sys
from datetime import datetime

sys.path.insert(0, ".")

from pipeline.ingest import _parse_publication_date
from utils.db import get_connection

SALVAGEABLE_REGEX = re.compile(r"^(\d{4})(\d{2})(\d{2})T\d$")


def _classify(conn):
    """Return (salvageable, unsalvageable).

    salvageable: list of (id, raw_event_date, iso, recovery_source).
                 recovery_source is "regex" or "article_lookup".
    unsalvageable: list of (id, raw_event_date, source, source_article_id).
    """
    cursor = conn.execute(
        "SELECT id, event_date, source, source_article_id FROM conflict_events "
        "WHERE event_date NOT LIKE '____-__-__'"
    )
    salvageable, unsalvageable = [], []
    for row in cursor.fetchall():
        raw = row["event_date"] or ""

        # Attempt 1: YYYYMMDDTN regex on event_date itself (GDELT truncation).
        m = SALVAGEABLE_REGEX.match(raw)
        if m:
            iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            try:
                datetime.strptime(iso, "%Y-%m-%d")
                salvageable.append((row["id"], raw, iso, "regex"))
                continue
            except ValueError:
                pass

        # Attempt 2: look up article.published_date (RFC 2822 and friends).
        if row["source_article_id"]:
            article = conn.execute(
                "SELECT published_date FROM articles WHERE id = ?",
                (row["source_article_id"],),
            ).fetchone()
            if article and article["published_date"]:
                iso = _parse_publication_date(article["published_date"])
                if iso:
                    salvageable.append((row["id"], raw, iso, "article_lookup"))
                    continue

        unsalvageable.append((row["id"], raw, row["source"], row["source_article_id"]))
    return salvageable, unsalvageable


def _summarize(salvageable, unsalvageable):
    by_source = {"regex": [], "article_lookup": []}
    for entry in salvageable:
        by_source[entry[3]].append(entry)

    total = len(salvageable) + len(unsalvageable)
    print(f"Corrupt rows found: {total}")
    print(f"  Salvageable via regex (GDELT truncation): {len(by_source['regex'])}")
    print(f"  Salvageable via article lookup (RFC 2822 etc.): {len(by_source['article_lookup'])}")
    print(f"  Unsalvageable (will FLAG, NOT delete): {len(unsalvageable)}")

    if by_source["regex"]:
        print("\nSample regex salvages (up to 3):")
        for rid, raw, iso, _ in by_source["regex"][:3]:
            print(f"  id={rid}  '{raw}' -> '{iso}'")
    if by_source["article_lookup"]:
        print("\nSample article-lookup salvages (up to 3):")
        for rid, raw, iso, _ in by_source["article_lookup"][:3]:
            print(f"  id={rid}  '{raw}' -> '{iso}'")

    if unsalvageable:
        print("\n*** UNSALVAGEABLE ROWS (manual review required) ***")
        print("These rows were NOT deleted. Investigate whether there is a")
        print("third ingest path still producing corrupt dates.")
        for rid, raw, source, sa_id in unsalvageable:
            print(f"  id={rid}  source={source!r}  source_article_id={sa_id}  event_date={raw!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually mutate the DB. Without this flag, dry run only.")
    args = ap.parse_args()

    conn = get_connection()
    try:
        salvageable, unsalvageable = _classify(conn)
        _summarize(salvageable, unsalvageable)

        if not args.apply:
            print("\nDry run. Re-run with --apply to UPDATE salvageable rows.")
            return

        if not salvageable:
            print("\nNothing to update.")
            return

        confirm = input(
            f"\nAbout to UPDATE {len(salvageable)} rows (0 deletions). "
            f"{len(unsalvageable)} unsalvageable rows will be LEFT IN PLACE "
            f"for manual review. Type 'yes' to proceed: "
        )
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return

        for rid, _raw, iso, _src in salvageable:
            conn.execute(
                "UPDATE conflict_events SET event_date = ? WHERE id = ?",
                (iso, rid),
            )
        conn.commit()
        print(f"Updated {len(salvageable)} rows. "
              f"{len(unsalvageable)} unsalvageable rows retained for review.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
