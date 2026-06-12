#!/usr/bin/env python3
"""Quick read-only check of the GPX store after an LFR run.

Prints the total number of races with GPX and lists the files stored for
tour-de-romandie-2026. Reads TURSO_* from the environment (same as db.py).
Run:  python scrapers/verify_lfr_store.py
"""
import db

c = db.open_db()
print("total gpx races:", len(db.gpx_slugs(c)))
print("romandie has_gpx:", db.has_gpx(c, "tour-de-romandie-2026"))
for f in db.list_gpx(c, "tour-de-romandie-2026"):
    print("  ", f)
c.close()
