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

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STARTLISTS_DIR = DATA_DIR / "startlists"
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


def collect_rider_urls() -> set:
    """Walk startlist files and return the deduped set of rider_url values."""
    urls = set()
    if not STARTLISTS_DIR.exists():
        log.warning(f"No startlists directory at {STARTLISTS_DIR}")
        return urls
    for f in sorted(STARTLISTS_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            for r in d.get("riders", []):
                u = r.get("rider_url")
                if u:
                    urls.add(u)
        except Exception as e:
            log.warning(f"Failed to read {f.name}: {e}")
    return urls


def load_cache() -> dict:
    """Load data/riders_cache.json, or return an empty cache skeleton."""
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Could not parse {CACHE_FILE.name}, starting fresh: {e}")
    return {"updated_at": None, "total_cached": 0, "riders": {}}


def save_cache(cache: dict):
    cache["total_cached"] = len(cache.get("riders", {}))
    cache["updated_at"] = datetime.now().isoformat()
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


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


def embed_specialties_into_startlists(cache_riders: dict):
    """Open each startlist file and embed specialties.career + birthdate +
    place_of_birth onto every rider entry (geocoding is done separately by
    geocode_birthplaces.py, which adds birthplace_lat/lon afterwards)."""
    for f in sorted(STARTLISTS_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Failed to read {f.name}: {e}")
            continue

        for r in d.get("riders", []):
            url = r.get("rider_url")
            ent = cache_riders.get(url) if url else None
            r["specialties"] = {"career": (ent or {}).get("career")}
            r["birthdate"] = (ent or {}).get("birthdate")
            r["place_of_birth"] = (ent or {}).get("place_of_birth")

        with open(f, "w", encoding="utf-8") as out:
            json.dump(d, out, indent=2, ensure_ascii=False)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    rider_urls = collect_rider_urls()
    log.info(f"Found {len(rider_urls)} unique riders across all startlists")

    cache = load_cache()
    cache_riders = cache.setdefault("riders", {})

    fresh = scraped = failed = 0
    total = len(rider_urls)

    for i, url in enumerate(sorted(rider_urls), 1):
        existing = cache_riders.get(url)
        # re-fetch when stale OR when an old-format entry lacks the new birth fields
        if existing and is_fresh(existing) and "birthdate" in existing:
            fresh += 1
            continue

        log.info(f"[{i}/{total}] Scraping: {url}")
        info = fetch_rider_info(url)
        cache_riders[url] = {**info, "_scraped_at": datetime.now().isoformat()}
        time.sleep(DELAY_BETWEEN_REQUESTS)

        if info["career"] is None:
            failed += 1
        else:
            scraped += 1

        if (scraped + failed) % SAVE_EVERY == 0:
            save_cache(cache)
            log.info(f"  → Cache checkpoint ({len(cache_riders)} entries)")

    save_cache(cache)
    embed_specialties_into_startlists(cache_riders)

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
