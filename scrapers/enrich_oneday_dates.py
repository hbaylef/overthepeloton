#!/usr/bin/env python3
"""
LOCAL one-off: cache each one-day race's DATE so score_history.py can place its
results in the rolling-window recency buckets. The results docs store
date=None for one-day races (only stage-race stages carry dates), so we fetch
the race date from PCS once and write data/oneday_dates.json (slug → 'YYYY-MM-DD').

Read-only on Turso; reads dates from PCS (needs the proxy TLS relax locally).
Incremental: a re-run only fetches slugs not already cached.

Usage:
  python scrapers/enrich_oneday_dates.py            # all missing one-day dates
  python scrapers/enrich_oneday_dates.py --limit 2  # quick test
"""

import argparse
import json
import sys
import time
from pathlib import Path

import db
import scrape_history as sh
from procyclingstats import Race

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT = Path(__file__).resolve().parent.parent / "data" / "oneday_dates.json"
DELAY = 2


def main():
    ap = argparse.ArgumentParser(description="Cache one-day race dates from PCS.")
    ap.add_argument("--limit", type=int, default=None, help="max races (testing)")
    args = ap.parse_args()

    sh._relax_tls()  # PCS via cloudscraper through the dev proxy
    client = db.connect()
    docs = db.get_all_documents(client, db.KIND_RESULTS)
    client.close()

    cache = {}
    if OUT.exists():
        cache = json.loads(OUT.read_text(encoding="utf-8"))

    oneday = {slug: d for slug, d in docs.items() if d.get("is_one_day_race")}
    todo = [s for s in sorted(oneday) if s not in cache]
    if args.limit:
        todo = todo[:args.limit]
    print(f"one-day docs: {len(oneday)} | cached: {len(cache)} | to fetch: {len(todo)}")

    for i, slug in enumerate(todo, 1):
        d = oneday[slug]
        url = d.get("pcs_url") or f"race/{d.get('pcs_slug')}/{d.get('year')}"
        try:
            race = Race(url)
            date = race.startdate() or race.enddate()
        except Exception as e:  # noqa: BLE001 — missing page / parse error
            print(f"  [{i}/{len(todo)}] {slug}: FAILED ({e})")
            time.sleep(DELAY)
            continue
        cache[slug] = date
        print(f"  [{i}/{len(todo)}] {slug} -> {date}")
        time.sleep(DELAY)
        if i % 20 == 0:
            OUT.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    OUT.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({len(cache)} dates total)")


if __name__ == "__main__":
    main()
