#!/usr/bin/env python3
"""
R4 — Categorised climbs per race / stage, for the elevation-profile highlights.

Mirrors the cobbles feature (data/cobbles/{slug}.json) but, unlike pavé, climbs
are scraped from procyclingstats rather than hand-curated: PCS publishes a
route/climbs page with each climb's position, length, steepness and altitude.

Source (the ONLY one with placement data):
  procyclingstats `RaceClimbs` → climbs() returns, per climb:
    climb_name, climb_url, length (km), steepness (%), top (m),
    km_before_finnish (km from the top of the climb to the finish).

  URL patterns (derived from data/races.json fields):
    one-day race :  {pcs_url}/route/climbs      e.g. race/il-lombardia/2026/route/climbs
    stage        :  {stage_url}/route/climbs    e.g. race/tour-de-france/2026/stage-5/route/climbs

  NOTE: Stage.climbs() is NOT used — it returns KOM *results*, which only exist
  after a stage is raced and carry no km placement. Useless for upcoming routes.

Output  data/climbs/{slug}.json:
    {
      "race": "...", "race_slug": "...", "source": "procyclingstats",
      "updated_at": "ISO", "is_one_day_race": true|false,
      # one-day races:
      "climbs": [ { "name", "climb_url", "km_before_finish",
                    "length_km", "steepness", "top_m" }, ... ],
      # stage races (keyed by stage number, as a string):
      "stages": { "5": [ { ...climb... }, ... ], ... }
    }

  We store km_before_finish (NOT an absolute km from start). The frontend places
  each climb at  x = total_km - km_before_finish  using the GPX length it draws,
  so a climb lands correctly even when PCS's route distance and the GPX differ.

Also writes data/climbs_index.json (which races have climbs).

Politeness: 2 s between PCS requests.
Cache:      data/climbs_cache.json — a fetched URL is reused for 7 days, BUT an
            empty/failed result is always retried (a route not yet published
            today may appear tomorrow).

Must run where PCS is reachable (GitHub Actions). This machine's TLS-intercepting
proxy breaks Python cert verification, so live runs fail locally — see
PROJECT_CONTEXT.md.

Usage:
  python scrapers/scrape_climbs.py
"""

import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from procyclingstats import RaceClimbs

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RACES_FILE = DATA_DIR / "races.json"
CLIMBS_DIR = DATA_DIR / "climbs"
INDEX_FILE = DATA_DIR / "climbs_index.json"
CACHE_FILE = DATA_DIR / "climbs_cache.json"

DELAY_BETWEEN_REQUESTS = 2
CACHE_DAYS = 7

# PCS climbs() field name -> our output field name. (Also fixes PCS's
# "finnish" typo, and tags km/%/m units onto the ambiguous names.)
PCS_TO_OUT = {
    "climb_name":        "name",
    "climb_url":         "climb_url",
    "km_before_finnish": "km_before_finish",
    "length":            "length_km",
    "steepness":         "steepness",
    "top":               "top_m",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure transforms (no network — unit-testable)
# ---------------------------------------------------------------------------
def normalize_climb(raw: dict) -> dict:
    """Map one PCS climb dict to our output shape, keeping only known fields."""
    return {out_key: raw.get(pcs_key) for pcs_key, out_key in PCS_TO_OUT.items()}


def normalize_climbs(raw_list: list) -> List[dict]:
    """Normalize a list of PCS climbs, dropping rows with no usable placement."""
    out = []
    for raw in raw_list or []:
        c = normalize_climb(raw)
        # km_before_finish is what positions the climb; without it we can't draw it.
        if c.get("km_before_finish") is None:
            continue
        out.append(c)
    return out


def climbs_url(base_url: str) -> str:
    """`race/x/2026` or `race/x/2026/stage-5` -> that page's /route/climbs URL."""
    return base_url.rstrip("/") + "/route/climbs"


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
def fetch_climbs(url: str) -> Optional[List[dict]]:
    """
    Fetch + normalize climbs for one route/climbs URL.

    Returns the normalized list (possibly empty if the page lists no climbs),
    or None when the page can't be loaded / isn't a valid climbs page (so the
    caller knows to retry it next run rather than caching a real "no climbs").
    """
    try:
        raw = RaceClimbs(url).climbs()
    except Exception as e:
        log.warning(f"  ! {url}: {e}")
        return None
    return normalize_climbs(raw)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Could not parse {CACHE_FILE.name}, starting fresh: {e}")
    return {"updated_at": None, "urls": {}}


def save_cache(cache: dict):
    cache["updated_at"] = datetime.now().isoformat()
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def cached_climbs(cache: dict, url: str) -> Optional[List[dict]]:
    """Return a fresh, NON-empty cached result for url, else None (→ refetch)."""
    entry = cache.get("urls", {}).get(url)
    if not entry:
        return None
    if not entry.get("climbs"):           # empty/failed → always retry
        return None
    ts = entry.get("_scraped_at")
    try:
        if datetime.now() - datetime.fromisoformat(ts) < timedelta(days=CACHE_DAYS):
            return entry["climbs"]
    except Exception:
        pass
    return None


def get_climbs(cache: dict, url: str, fetch: Callable[[str], Optional[List[dict]]]) -> List[dict]:
    """Cache-aware fetch. Records the result; returns [] on miss/failure."""
    hit = cached_climbs(cache, url)
    if hit is not None:
        log.info(f"  cached: {url} ({len(hit)} climbs)")
        return hit

    log.info(f"  fetch:  {url}")
    result = fetch(url)
    fetched = result if result is not None else []
    cache.setdefault("urls", {})[url] = {
        "climbs": fetched,
        "_scraped_at": datetime.now().isoformat(),
    }
    time.sleep(DELAY_BETWEEN_REQUESTS)
    return fetched


# ---------------------------------------------------------------------------
# Per-race assembly
# ---------------------------------------------------------------------------
def build_race_entry(race: dict, cache: dict,
                     fetch: Callable[[str], Optional[List[dict]]]) -> dict:
    """
    Build the data/climbs/{slug}.json payload for one race (no file IO).
    Returns the payload dict (with `climbs` or `stages` possibly empty).
    """
    is_one_day = bool(race.get("is_one_day_race"))
    payload = {
        "race": race.get("name"),
        "race_slug": race.get("slug"),
        "source": "procyclingstats",
        "updated_at": datetime.now().isoformat(),
        "is_one_day_race": is_one_day,
    }

    if is_one_day:
        base = race.get("pcs_url")
        payload["climbs"] = get_climbs(cache, climbs_url(base), fetch) if base else []
    else:
        stages = {}
        for idx, stage in enumerate(race.get("stages", []), 1):
            base = stage.get("stage_url")
            if not base:
                continue
            climbs = get_climbs(cache, climbs_url(base), fetch)
            if climbs:
                stages[str(idx)] = climbs
        payload["stages"] = stages

    return payload


def count_climbs(payload: dict) -> int:
    if payload.get("is_one_day_race"):
        return len(payload.get("climbs", []))
    return sum(len(v) for v in payload.get("stages", {}).values())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not RACES_FILE.exists():
        log.error(f"No {RACES_FILE} — run scrape_races.py first.")
        return

    races = json.loads(RACES_FILE.read_text(encoding="utf-8")).get("races", [])
    CLIMBS_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_cache()

    index_races = {}
    with_climbs = 0

    for i, race in enumerate(races, 1):
        slug = race.get("slug")
        if not slug:
            continue
        log.info(f"[{i}/{len(races)}] {slug}")

        payload = build_race_entry(race, cache, fetch_climbs)
        n = count_climbs(payload)

        out_path = CLIMBS_DIR / f"{slug}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        index_races[slug] = {
            "name": race.get("name"),
            "climbs_available": n > 0,
            "total_climbs": n,
        }
        if n > 0:
            with_climbs += 1

        save_cache(cache)   # checkpoint after every race (cheap, crash-safe)

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": datetime.now().isoformat(),
            "source": "procyclingstats",
            "races": index_races,
        }, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 64)
    print("  CLIMBS SCRAPE SUMMARY")
    print(f"  Races processed:       {len(index_races)}")
    print(f"  Races with climbs:     {with_climbs}")
    print(f"  Total climbs scraped:  {sum(r['total_climbs'] for r in index_races.values())}")
    print("=" * 64)


if __name__ == "__main__":
    main()
