#!/usr/bin/env python3
"""
Scrape PCS rider data (specialty points + bio) for every rider seen in any
startlist.

Outputs:
  - data/riders.json: indexed by rider_url, contains bio + specialty points

Reads:
  - data/startlists/*.json (to discover which riders to scrape)
  - data/riders.json     (existing — used as a 7-day freshness cache)

Specialty point keys from PCS (via procyclingstats library):
  one_day_races, gc, time_trial, sprint, climber, hills

Usage:
  python scrapers/scrape_riders.py

Politeness: 2s delay between PCS requests.
Cache: skips riders scraped within the last 7 days (so daily runs are cheap
after the initial seed).
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
RIDERS_FILE = DATA_DIR / "riders.json"
DELAY_BETWEEN_REQUESTS = 2
CACHE_DAYS = 7
SAVE_EVERY = 50  # checkpoint riders.json this often so a crash keeps progress

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def collect_rider_urls() -> set:
    """Walk all startlist files and return the deduped set of rider_url values."""
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


def load_existing() -> dict:
    """Load existing data/riders.json or return an empty index skeleton."""
    if RIDERS_FILE.exists():
        try:
            return json.loads(RIDERS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Could not parse existing riders.json, starting fresh: {e}")
    return {"updated_at": None, "total_riders": 0, "riders": {}}


def is_fresh(entry: dict, days: int = CACHE_DAYS) -> bool:
    """True if entry was scraped within the last N days."""
    ts = entry.get("_scraped_at")
    if not ts:
        return False
    try:
        scraped = datetime.fromisoformat(ts)
        return (datetime.now() - scraped) < timedelta(days=days)
    except Exception:
        return False


def scrape_rider(rider_url: str) -> Optional[dict]:
    """Fetch one rider's bio + specialty points from PCS. Returns None on failure."""
    try:
        r = Rider(rider_url)
        data = r.parse()
        return {
            "name": data.get("name"),
            "nationality": data.get("nationality"),
            "birthdate": data.get("birthdate"),
            "weight": data.get("weight"),
            "height": data.get("height"),
            "image_url": data.get("image_url"),
            "specialty": data.get("points_per_speciality") or {},
            "_scraped_at": datetime.now().isoformat(),
        }
    except Exception as e:
        log.warning(f"Failed to scrape {rider_url}: {e}")
        return None


def _save(index: dict, riders: dict):
    index["riders"] = riders
    index["total_riders"] = len(riders)
    index["updated_at"] = datetime.now().isoformat()
    with open(RIDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    rider_urls = collect_rider_urls()
    log.info(f"Found {len(rider_urls)} unique riders across all startlists")

    index = load_existing()
    riders = index.get("riders", {})

    fresh = scraped = failed = 0
    total = len(rider_urls)

    for i, url in enumerate(sorted(rider_urls), 1):
        existing = riders.get(url)
        if existing and is_fresh(existing):
            fresh += 1
            continue

        log.info(f"[{i}/{total}] Scraping: {url}")
        entry = scrape_rider(url)
        time.sleep(DELAY_BETWEEN_REQUESTS)

        if entry is None:
            failed += 1
            continue

        riders[url] = entry
        scraped += 1

        if scraped % SAVE_EVERY == 0:
            _save(index, riders)
            log.info(f"  → Checkpoint saved ({len(riders)} riders in index)")

    _save(index, riders)

    print("\n" + "=" * 64)
    print(f"  RIDER SCRAPE SUMMARY")
    print(f"  Riders in startlists:  {total}")
    print(f"  Cached (skipped):      {fresh}")
    print(f"  Newly scraped:         {scraped}")
    print(f"  Failed:                {failed}")
    print(f"  Total in riders.json:  {len(riders)}")
    print("=" * 64)


if __name__ == "__main__":
    main()
