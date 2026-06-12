#!/usr/bin/env python3
"""Read-only check of the GPX store after an LFR run.

Prints the total races with GPX and lists every race whose GPX came from La
Flamme Rouge (source='la_flamme_rouge') with its file count. Reads TURSO_* from
the environment (same as db.py).
Run:  python scrapers/verify_lfr_store.py
"""
import db

c = db.open_db()
total = len(db.gpx_slugs(c))
rs = c.execute(
    "SELECT slug, COUNT(*) FROM gpx_files WHERE source=? GROUP BY slug ORDER BY slug",
    ["la_flamme_rouge"],
)
rows = rs.rows
files = sum(r[1] for r in rows)
print(f"total races with GPX in store: {total}")
print(f"LFR-sourced races: {len(rows)} ({files} GPX files)")
for slug, n in rows:
    print(f"   {slug}: {n}")
c.close()
