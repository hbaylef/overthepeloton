#!/usr/bin/env python3
"""Export the remote Turso DB to a local SQLite file (and optionally JSON).

Reuses db.py's connection logic, so it talks to the remote Turso when
TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are set in the environment, and to the
local file DB otherwise. It mirrors every table (race_data, caches, gpx_files)
row-for-row into a standalone SQLite file you can open with any SQLite tool or
re-use offline (e.g. publish.py via OVERTHEPELOTON_DB).

Usage (PowerShell):
    $env:TURSO_DATABASE_URL = "libsql://<your-db>.turso.io"
    $env:TURSO_AUTH_TOKEN   = "<your-token>"
    python scrapers/export_turso.py                      # -> data/turso-export.db
    python scrapers/export_turso.py --out backup.db      # custom path
    python scrapers/export_turso.py --json data/dump     # also write JSON docs

Nothing is written back to Turso — this is read-only on the remote side.
"""

import argparse
import json
import os
import sqlite3

import db  # same folder; provides connect() / is_remote() / SCHEMA / table names

TABLES = ["race_data", "caches", "gpx_files"]


def _fetch_table(client, table):
    """Return (columns, rows) for a whole table via the libsql client.

    Returns ([], []) if the table doesn't exist, so the export stays read-only
    on the remote (no CREATE TABLE) and never crashes on an empty source.
    """
    try:
        rs = client.execute(f"SELECT * FROM {table}")
    except Exception as exc:  # noqa: BLE001 — missing table / transient read error
        print(f"  {table}: skipped ({exc})")
        return [], []
    return list(rs.columns), [list(r) for r in rs.rows]


def export_sqlite(client, out_path):
    """Mirror every table into a fresh local SQLite file at out_path."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    if os.path.exists(out_path):
        os.remove(out_path)  # start clean so a re-run never double-inserts

    local = sqlite3.connect(out_path)
    try:
        for stmt in db.SCHEMA:          # identical schema to the remote DB
            local.execute(stmt)

        total = 0
        for table in TABLES:
            cols, rows = _fetch_table(client, table)
            if not rows:
                print(f"  {table}: 0 rows")
                continue
            placeholders = ",".join("?" for _ in cols)
            collist = ",".join(cols)
            local.executemany(
                f"INSERT INTO {table} ({collist}) VALUES ({placeholders})", rows
            )
            total += len(rows)
            print(f"  {table}: {len(rows)} rows")
        local.commit()
        print(f"Wrote {total} rows -> {out_path}")
    finally:
        local.close()


def export_json(client, out_dir):
    """Optional: dump each race_data kind + each cache as readable JSON files."""
    os.makedirs(out_dir, exist_ok=True)
    kinds = [db.KIND_RACE, db.KIND_STARTLIST, db.KIND_CLIMBS,
             db.KIND_PREDICTIONS, db.KIND_COBBLES]
    for kind in kinds:
        docs = db.get_all_documents(client, kind)
        if not docs:
            continue
        path = os.path.join(out_dir, f"{kind}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(docs, fh, ensure_ascii=False, indent=2, sort_keys=True)
        print(f"  json {kind}: {len(docs)} docs -> {path}")


# Characters that can't appear in a Windows filename, plus control chars.
_INVALID = '<>:"/\\|?*'


def _safe(name):
    """Make a slug/filename safe to use as a path component on any OS."""
    name = (name or "").strip()
    out = "".join("_" if (c in _INVALID or ord(c) < 32) else c for c in name)
    return out or "unnamed"


def export_gpx(client, out_dir):
    """Write every stored .gpx back to disk as out_dir/<slug>/<filename>.gpx.

    Reads the raw XML straight from gpx_files.content — one file per row.
    """
    try:
        rs = client.execute(
            "SELECT slug, filename, content FROM gpx_files ORDER BY slug, filename"
        )
    except Exception as exc:  # noqa: BLE001 — missing table / transient read error
        print(f"  gpx: skipped ({exc})")
        return

    count = 0
    for row in rs.rows:
        slug, filename, content = row[0], row[1], row[2]
        sub = os.path.join(out_dir, _safe(slug))
        os.makedirs(sub, exist_ok=True)
        fname = _safe(filename)
        if not fname.lower().endswith(".gpx"):
            fname += ".gpx"
        with open(os.path.join(sub, fname), "w", encoding="utf-8") as fh:
            fh.write(content)
        count += 1
    print(f"  gpx: wrote {count} files -> {out_dir}{os.sep}<slug>{os.sep}<filename>.gpx")


def main():
    ap = argparse.ArgumentParser(description="Export Turso to local SQLite/JSON/GPX.")
    ap.add_argument("--out", default="data/turso-export.db",
                    help="output SQLite file (default: data/turso-export.db)")
    ap.add_argument("--json", metavar="DIR", default=None,
                    help="also write per-kind JSON docs into DIR")
    ap.add_argument("--gpx", metavar="DIR", default=None,
                    help="also extract each stored .gpx into DIR/<slug>/<filename>.gpx")
    ap.add_argument("--no-db", action="store_true",
                    help="skip the SQLite mirror (e.g. pair with --gpx for GPX only)")
    args = ap.parse_args()

    where = "remote Turso" if db.is_remote() else "LOCAL file DB"
    print(f"Source: {where}")
    if not db.is_remote():
        print("  (TURSO_DATABASE_URL not set — exporting the local file, not the "
              "cloud DB. Set the env vars to pull from Turso.)")

    client = db.connect()
    try:
        if not args.no_db:
            export_sqlite(client, args.out)
        if args.json:
            export_json(client, args.json)
        if args.gpx:
            export_gpx(client, args.gpx)
    finally:
        client.close()


if __name__ == "__main__":
    main()
