#!/usr/bin/env python3
"""
Embed each startlist rider's PCS career specialty points into the startlist
file itself, per R1_R2_DESIGN.md.

Per-rider shape added inside data/startlists/{slug}.json:

    "specialties": {
      "career": { "one_day_races": int, "gc": int, "tt": int,
                  "sprint": int, "climber": int, "hills": int }
    }

When PCS has no specialty data for a rider, "career" is null (not zeros) so
R2 can distinguish "no data" from "genuinely zero points".

The "recent" half of the spec (last 2 seasons split by specialty) is NOT
exposed cleanly by the procyclingstats library — see spike findings in
the conversation that produced this file. Career-only is the chosen
fallback (Option 3 in that discussion). The "specialties" wrapper is kept
so a "recent" block can be added later without a schema migration.

Reads:
  data/startlists/*.json   (input + output — specialties embedded in place)
  data/riders_cache.json   (bookkeeping — survives scrape_races.py overwrites)

Writes:
  data/startlists/*.json   (same files, with specialties embedded)
  data/riders_cache.json   (updated _scraped_at per rider)

Politeness: 2 s delay between PCS requests.
Cache:      skips riders whose cached entry is < 7 days old.

Usage:
  python scrapers/scrape_riders.py
"""

import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from procyclingstats import Rider

import db  # local module: Turso/SQLite store (build-order step 2)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Legacy file, read once to seed the Turso cache on first run; no longer written.
CACHE_FILE = DATA_DIR / "riders_cache.json"
DELAY_BETWEEN_REQUESTS = 2
CACHE_DAYS = 7
SAVE_EVERY = 50

# Keys returned by procyclingstats `points_per_speciality()`, mapped to the
# spec's preferred names. Only the time_trial → tt rename is meaningful;
# everything else is pass-through.
PCS_KEY_TO_SPEC_KEY = {
    "one_day_races": "one_day_races",
    "gc":            "gc",
    "time_trial":    "tt",
    "sprint":        "sprint",
    "climber":       "climber",
    "hills":         "hills",
}
SPEC_KEYS = list(PCS_KEY_TO_SPEC_KEY.values())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def collect_rider_urls(client) -> set:
    """Walk the startlists in the store and return the deduped set of rider_url
    values."""
    urls = set()
    for d in db.get_all_documents(client, db.KIND_STARTLIST).values():
        for r in d.get("riders", []):
            u = r.get("rider_url")
            if u:
                urls.add(u)
    return urls


def load_cache(client) -> dict:
    """Load the riders cache from the store, seeding it once from the legacy
    data/riders_cache.json if the store has none yet (so we don't needlessly
    re-scrape ~1k riders on the first run)."""
    cached = db.get_cache(client, db.CACHE_RIDERS)
    if cached is None and CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            log.info(f"Seeded riders cache from legacy {CACHE_FILE.name}")
        except Exception as e:
            log.warning(f"Could not parse {CACHE_FILE.name}, starting fresh: {e}")
    return cached or {"updated_at": None, "total_cached": 0, "riders": {}}


def save_cache(client, cache: dict):
    cache["total_cached"] = len(cache.get("riders", {}))
    cache["updated_at"] = datetime.now().isoformat()
    db.put_cache(client, db.CACHE_RIDERS, cache)


def is_fresh(entry: dict, days: int = CACHE_DAYS) -> bool:
    """True if cache entry was scraped within the last N days."""
    ts = entry.get("_scraped_at")
    if not ts:
        return False
    try:
        scraped = datetime.fromisoformat(ts)
        return (datetime.now() - scraped) < timedelta(days=days)
    except Exception:
        return False


def needs_refetch(entry: Optional[dict], days: int = CACHE_DAYS) -> bool:
    """Decide whether to hit PCS for a rider.

    We re-fetch ONLY to refresh the rider's *evolving* career specialty points
    (the 7-day staleness gate), or when we have no entry at all. Birthdate and
    place_of_birth are immutable and ride along in the same page hit, so a
    missing birth field must NEVER on its own trigger a network call — it simply
    fills in on the next natural career refresh. Pure → unit-tested."""
    if not entry:
        return True
    return not is_fresh(entry, days)


def merge_preserving_birth(new_info: dict, existing: Optional[dict]) -> dict:
    """Keep immutable birth fields when a re-fetch loses them.

    birthdate / place_of_birth never change, so if a flaky PCS read returns them
    empty we must not clobber a value we already had. Career points DO evolve, so
    they always take the fresh value."""
    merged = dict(new_info)
    if existing:
        if not merged.get("birthdate"):
            merged["birthdate"] = existing.get("birthdate")
        if not merged.get("place_of_birth"):
            merged["place_of_birth"] = existing.get("place_of_birth")
    return merged


def fetch_rider_info(rider_url: str) -> dict:
    """
    Fetch a rider's career specialty points + birthdate + place of birth from PCS
    in a SINGLE page hit (one Rider object, several parse calls reuse the HTML).

    Returns {"career": {6 keys}|None, "birthdate": "YYYY-MM-DD"|None,
             "place_of_birth": town|None}. `career` is None when PCS has no chart
    for the rider; birthdate/place are None when absent or on any failure.
    """
    try:
        r = Rider(rider_url)
        raw = r.points_per_speciality()
        try:
            birthdate = r.birthdate()
        except Exception:
            birthdate = None
        try:
            place = r.place_of_birth()
        except Exception:
            place = None
    except Exception as e:
        log.warning(f"Failed to scrape {rider_url}: {e}")
        return {"career": None, "birthdate": None, "place_of_birth": None}

    # The library returns {} when the chart is missing, a full 6-key dict normally.
    # Treat anything less than a full set as missing data → null career block.
    career = None
    if raw and len(raw) >= 6:
        career = {spec_key: raw.get(pcs_key)
                  for pcs_key, spec_key in PCS_KEY_TO_SPEC_KEY.items()}
    return {"career": career,
            "birthdate": birthdate or None,
            "place_of_birth": place or None}


def embed_specialties_into_startlists(client, cache_riders: dict):
    """For each startlist in the store, embed specialties.career + birthdate +
    place_of_birth onto every rider entry (geocoding is done separately by
    geocode_birthplaces.py, which adds birthplace_lat/lon afterwards)."""
    for slug, d in db.get_all_documents(client, db.KIND_STARTLIST).items():
        for r in d.get("riders", []):
            url = r.get("rider_url")
            ent = cache_riders.get(url) if url else None
            r["specialties"] = {"career": (ent or {}).get("career")}
            r["birthdate"] = (ent or {}).get("birthdate")
            r["place_of_birth"] = (ent or {}).get("place_of_birth")
        db.put_document(client, db.KIND_STARTLIST, slug, d)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Embed rider career specialties + birth info.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report how many riders would be skipped vs fetched, then "
                         "exit (no network, no writes)")
    ap.add_argument("--embed-only", action="store_true",
                    help="re-apply the CACHED specialties/birthdate/place onto the "
                         "startlists and exit — NO PCS calls. Used by the daily run "
                         "so the site keeps showing rider data without re-scraping; "
                         "run without this flag (weekly/monthly) to actually refresh "
                         "from PCS.")
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    client = db.open_db()
    log.info(f"Rider store: {'remote Turso' if db.is_remote() else 'local SQLite file'}")

    rider_urls = collect_rider_urls(client)
    log.info(f"Found {len(rider_urls)} unique riders across all startlists")

    cache = load_cache(client)
    cache_riders = cache.setdefault("riders", {})
    total = len(rider_urls)

    if args.embed_only:
        # Daily mode: NO PCS calls. scrape_races rebuilds the startlists fresh
        # (dropping specialties/birth), so we re-apply the already-cached values
        # to keep the site's hometown strip + specialty data populated. New
        # riders simply have null blocks until a weekly/monthly full run scrapes
        # them. The cache itself is unchanged, so we don't rewrite it.
        embed_specialties_into_startlists(client, cache_riders)
        client.close()
        log.info(f"Embed-only: re-applied cached rider data to {total} riders' "
                 f"startlist entries (no PCS calls).")
        return

    if args.dry_run:
        to_fetch = [u for u in rider_urls if needs_refetch(cache_riders.get(u))]
        print("\n" + "=" * 64)
        print("  RIDER SCRAPE — DRY RUN")
        print(f"  Riders in startlists:        {total}")
        print(f"  SKIPPED (career still fresh): {total - len(to_fetch)}")
        print(f"  to FETCH (new or >7d stale):  {len(to_fetch)}")
        print("=" * 64)
        client.close()
        return

    fresh = scraped = failed = 0

    for i, url in enumerate(sorted(rider_urls), 1):
        existing = cache_riders.get(url)
        # Re-fetch ONLY for evolving career points (staleness) — never just to
        # backfill an immutable birth field.
        if not needs_refetch(existing):
            fresh += 1
            continue

        log.info(f"[{i}/{total}] Scraping: {url}")
        info = fetch_rider_info(url)
        # Don't let a flaky read drop a birthdate/place we already have.
        info = merge_preserving_birth(info, existing)
        cache_riders[url] = {**info, "_scraped_at": datetime.now().isoformat()}
        time.sleep(DELAY_BETWEEN_REQUESTS)

        if info["career"] is None:
            failed += 1
        else:
            scraped += 1

        if (scraped + failed) % SAVE_EVERY == 0:
            save_cache(client, cache)
            log.info(f"  → Cache checkpoint ({len(cache_riders)} entries)")

    save_cache(client, cache)
    embed_specialties_into_startlists(client, cache_riders)
    client.close()

    print("\n" + "=" * 64)
    print(f"  RIDER SCRAPE SUMMARY")
    print(f"  Riders in startlists:  {total}")
    print(f"  Cached (skipped):      {fresh}")
    print(f"  Newly scraped (ok):    {scraped}")
    print(f"  Failed / no data:      {failed}")
    print(f"  Total in cache:        {len(cache_riders)}")
    print("=" * 64)


if __name__ == "__main__":
    main()
