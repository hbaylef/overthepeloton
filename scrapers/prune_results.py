#!/usr/bin/env python3
"""
Delete excluded race-editions (women's / non-men's races) from the results
store in Turso — the docs listed in db.EXCLUDE_RESULT_PCS_SLUGS, across all
years (e.g. the women's "setmana-ciclista-valenciana" that the CALENDAR
mis-maps onto tour-of-valencia).

DRY-RUN by default: lists what WOULD be deleted (read-only — works with any
token). Pass --apply to actually delete, which needs a WRITE-capable Turso
token (a read-only token returns "BLOCKED"). Once scrape_history.py is fixed,
deleted editions are NOT re-added.

Usage:
  python scrapers/prune_results.py            # dry-run (safe)
  python scrapers/prune_results.py --apply    # delete (needs a write token)
"""

import argparse
import re
import sys

import db

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def targets(client):
    """KIND_RESULTS slugs whose pcs base is excluded (slug = '{pcs}-{year}')."""
    pats = [re.compile(rf"^{re.escape(p)}-\d{{4}}$") for p in db.EXCLUDE_RESULT_PCS_SLUGS]
    return [s for s in db.list_slugs(client, db.KIND_RESULTS)
            if any(p.match(s) for p in pats)]


def main():
    ap = argparse.ArgumentParser(description="Prune excluded races from KIND_RESULTS.")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (needs a WRITE Turso token); else dry-run")
    args = ap.parse_args()

    client = db.connect()  # read for the listing; delete needs write
    print(f"Store: {'remote Turso' if db.is_remote() else 'local SQLite file'}")
    print(f"Excluded pcs_slugs: {sorted(db.EXCLUDE_RESULT_PCS_SLUGS)}")

    found = targets(client)
    if not found:
        print("Nothing to prune — no matching result docs.")
        client.close()
        return

    print(f"\n{len(found)} result doc(s) match:")
    for s in found:
        print(f"   {s}")

    if not args.apply:
        print("\nDRY-RUN — nothing deleted. Re-run with --apply (write token) to delete.")
        client.close()
        return

    deleted = 0
    for s in found:
        try:
            db.delete_document(client, db.KIND_RESULTS, s)
            deleted += 1
            print(f"   deleted {s}")
        except Exception as e:  # noqa: BLE001 — read-only token → BLOCKED
            print(f"   FAILED {s}: {e}")
            print("\n⚠️  Delete blocked — your token is read-only. Use a WRITE "
                  "token (or run this in GitHub Actions with the write secret).")
            break
    client.close()
    print(f"\nDeleted {deleted}/{len(found)} docs.")


if __name__ == "__main__":
    main()
