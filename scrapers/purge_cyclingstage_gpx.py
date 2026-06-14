#!/usr/bin/env python3
"""One-shot maintenance: purge the retired cyclingstage.com GPX from the store.

As of 2026-06-14 La Flamme Rouge (scrape_lfr.py) is the SOLE GPX source; the
cyclingstage scraper has been removed. Some cyclingstage routes were wrong (e.g.
Tour de France stage 4). This script deletes every gpx_files row with
source='cyclingstage' so the next publish.py regenerates routes from LFR only.

It connects via db.py, so it talks to remote Turso when TURSO_DATABASE_URL +
TURSO_AUTH_TOKEN are set (the usual case for this cleanup), and to the local file
DB otherwise.

  ⚠ TLS gotcha (same as scrape_lfr.py): the Python -> Turso write goes through the
  corporate proxy. If it fails with a cert error, point the libsql client at the
  corporate CA bundle first, or set OVERTHEPELOTON_INSECURE_TLS=1 in .env (local
  only; see db._relax_tls_strict).

Usage (PowerShell):
    $env:TURSO_DATABASE_URL = "libsql://<your-db>.turso.io"
    $env:TURSO_AUTH_TOKEN   = "<your-token>"
    python scrapers/purge_cyclingstage_gpx.py            # DRY RUN — shows what would go
    python scrapers/purge_cyclingstage_gpx.py --apply    # actually delete

After applying: re-run scrape_lfr.py (attended, CDP-Chrome) to refill the routes,
then publish.py to regenerate data/routes/*.json.
"""

import argparse
import logging

import db  # same folder: connection + GPX helpers

SOURCE = "cyclingstage"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def list_source_rows(client, source):
    """(slug, filename) for every GPX row from `source`, sorted, for the report."""
    rs = client.execute(
        "SELECT slug, filename FROM gpx_files WHERE source=? ORDER BY slug, filename",
        [source],
    )
    return [(r[0], r[1]) for r in rs.rows]


def main():
    ap = argparse.ArgumentParser(
        description="Purge cyclingstage GPX from the store (dry-run unless --apply).")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default is a dry run that only reports)")
    ap.add_argument("--source", default=SOURCE,
                    help=f"source tag to purge (default: {SOURCE})")
    args = ap.parse_args()

    client = db.open_db()
    where = "remote Turso" if db.is_remote() else "LOCAL file DB"
    log.info(f"Store: {where}")

    rows = list_source_rows(client, args.source)
    slugs = sorted({s for s, _ in rows})
    log.info(f"Found {len(rows)} '{args.source}' GPX file(s) across {len(slugs)} race(s).")
    for slug in slugs:
        files = [f for s, f in rows if s == slug]
        log.info(f"  {slug}: {len(files)} file(s)")

    if not rows:
        log.info("Nothing to purge.")
        client.close()
        return

    if not args.apply:
        log.info("DRY RUN — no rows deleted. Re-run with --apply to delete them.")
        client.close()
        return

    deleted = db.delete_gpx(client, source=args.source)
    remaining = len(list_source_rows(client, args.source))
    log.info(f"Deleted {deleted} row(s). Remaining '{args.source}' rows: {remaining}")
    log.info("Next: run scrape_lfr.py to refill, then publish.py to rebuild routes.")
    client.close()


if __name__ == "__main__":
    main()
