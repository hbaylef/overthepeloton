#!/usr/bin/env python3
"""
R5 (step 1) — Per-stage / per-race START TIME, for the weather pass-time model.

The weather overlay needs to know WHEN the race happens so it can pull wind/rain
at the hour the peloton is actually out on the road. PCS publishes a start time
on each stage page, but the procyclingstats `Race.stages()` table does NOT carry
it — so we fetch `Stage(stage_url).start_time()` per stage (and per one-day race).

`Stage.start_time()` returns e.g. ``"17:00 (17:00 CET)"``; we keep the local
``HH:MM``. We write it back into data/races.json as:
  - stage races:   stages[].start_time + stages[].start_time_source
  - one-day races: race-level start_time + start_time_source
`start_time_source` is "pcs" when scraped, "default" when PCS hasn't published a
time yet (we fall back to DEFAULT_START so the weather model still has an anchor).

Politeness: 2 s between PCS requests.
Cache: data/start_times_cache.json — a fetched URL is reused for 30 days, BUT a
missing/empty time is always retried (times publish closer to race day).

Must run where PCS is reachable (GitHub Actions). This machine's TLS-intercepting
proxy breaks Python cert verification, so live runs fail locally — see
PROJECT_CONTEXT.md. The pure helpers (parse / cache / annotate) are no-network
unit-tested in test_scrape_start_times.py.

Usage:
  python scrapers/scrape_start_times.py
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import db  # local module: Turso/SQLite store (build-order step 2)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Legacy file, read once to seed the start-times cache into the store.
CACHE_FILE = DATA_DIR / "start_times_cache.json"

DELAY_BETWEEN_REQUESTS = 2
CACHE_DAYS = 30
DEFAULT_START = "12:00"          # local-time anchor when PCS has no time yet
LOOKAHEAD_DAYS = 18             # only fetch races within the weather window
                               # (past + next ~18 d; forecast horizon is 16 d).
                               # Far-future races show "closer to race day" and
                               # don't need a start time yet — don't hammer PCS.

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers (no network — unit-testable)
# ---------------------------------------------------------------------------
def parse_start_time(raw: Optional[str]) -> Optional[str]:
    """'17:00 (17:00 CET)' -> '17:00'; empty / unparseable -> None.
    Keeps the first HH:MM (the local clock time)."""
    if not raw:
        return None
    m = _TIME_RE.search(raw)
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    if h > 23 or mn > 59:
        return None
    return f"{h:02d}:{mn:02d}"


def entry_date(race: dict, stage: Optional[dict]) -> Optional[str]:
    """Best 'YYYY-MM-DD' for a race/stage. One-day races use the race startdate;
    stages combine the race year with the stage's 'MM-DD'."""
    if stage is None:
        return race.get("startdate")
    d = stage.get("date")                       # 'MM-DD'
    year = race.get("year")
    if d and year and "-" in d:
        return f"{year}-{d}"
    return race.get("startdate")


def in_weather_window(date_str: Optional[str], today: Optional[str] = None,
                      ahead: int = LOOKAHEAD_DAYS) -> bool:
    """True for past races and races within `ahead` days (the weather window).
    Unparseable dates default to True (fetch rather than silently skip)."""
    if not date_str:
        return True
    try:
        d = datetime.fromisoformat(date_str).date()
    except Exception:
        return True
    now = datetime.fromisoformat(today).date() if today else datetime.now().date()
    return d <= now + timedelta(days=ahead)


def cached_time(cache: dict, url: str) -> Optional[str]:
    """Return a fresh, NON-empty cached start time for url, else None (→refetch).
    A cached None (time not published when we last looked) is always retried."""
    entry = cache.get("urls", {}).get(url)
    if not entry or not entry.get("start_time"):
        return None
    try:
        if datetime.now() - datetime.fromisoformat(entry["_scraped_at"]) < \
                timedelta(days=CACHE_DAYS):
            return entry["start_time"]
    except Exception:
        pass
    return None


def get_start_time(cache: dict, url: str,
                   fetch: Callable[[str], Optional[str]]) -> Optional[str]:
    """Cache-aware fetch. Records the result (even None); returns the time or None."""
    hit = cached_time(cache, url)
    if hit is not None:
        return hit
    result = fetch(url)
    cache.setdefault("urls", {})[url] = {
        "start_time": result,
        "_scraped_at": datetime.now().isoformat(),
    }
    time.sleep(DELAY_BETWEEN_REQUESTS)
    return result


def annotate_start_times(races: list, cache: dict,
                         fetch: Callable[[str], Optional[str]]) -> int:
    """Write start_time + start_time_source onto each race/stage (in place).
    Returns the count of entries that got a real (PCS) time."""
    scraped = 0
    for race in races:
        if race.get("is_one_day_race"):
            if not in_weather_window(entry_date(race, None)):
                race["start_time"] = None
                race["start_time_source"] = "pending"   # far future — fetch later
                continue
            t = get_start_time(cache, race.get("pcs_url", ""), fetch)
            race["start_time"] = t or DEFAULT_START
            race["start_time_source"] = "pcs" if t else "default"
            scraped += 1 if t else 0
        else:
            for stage in race.get("stages", []):
                if not in_weather_window(entry_date(race, stage)):
                    stage["start_time"] = None
                    stage["start_time_source"] = "pending"
                    continue
                url = stage.get("stage_url")
                t = get_start_time(cache, url, fetch) if url else None
                stage["start_time"] = t or DEFAULT_START
                stage["start_time_source"] = "pcs" if t else "default"
                scraped += 1 if t else 0
    return scraped


# ---------------------------------------------------------------------------
# Network + cache IO
# ---------------------------------------------------------------------------
def fetch_start_time(url: str) -> Optional[str]:
    """Fetch + parse a stage/race start time from PCS. None on any failure."""
    try:
        from procyclingstats import Stage    # lazy: only needed in Actions
        return parse_start_time(Stage(url).start_time())
    except Exception as e:
        log.warning(f"  ! {url}: {e}")
        return None


def load_cache(client) -> dict:
    """Load the start-times cache from the store, seeding once from the legacy file."""
    cached = db.get_cache(client, db.CACHE_START_TIMES)
    if cached is None and CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            log.info(f"Seeded start-times cache from legacy {CACHE_FILE.name}")
        except Exception as e:
            log.warning(f"Could not parse {CACHE_FILE.name}, starting fresh: {e}")
    return cached or {"updated_at": None, "urls": {}}


def save_cache(client, cache: dict):
    cache["updated_at"] = datetime.now().isoformat()
    db.put_cache(client, db.CACHE_START_TIMES, cache)


def main():
    client = db.open_db()
    log.info(f"Start-times store: {'remote Turso' if db.is_remote() else 'local SQLite file'}")

    races = list(db.get_all_documents(client, db.KIND_RACE).values())
    if not races:
        log.error("No races in the store — run scrape_races.py first.")
        client.close()
        return

    cache = load_cache(client)
    scraped = annotate_start_times(races, cache, fetch_start_time)

    # Write each race doc back (change-aware: only rows that actually changed).
    for race in races:
        slug = race.get("slug")
        if slug:
            db.put_document(client, db.KIND_RACE, slug, race)
    save_cache(client, cache)
    client.close()

    total = sum(1 if r.get("is_one_day_race") else len(r.get("stages", []))
                for r in races)
    print("\n" + "=" * 60)
    print("  START TIMES")
    print(f"  Entries annotated: {total}  (real PCS time: {scraped}, "
          f"default: {total - scraped})")
    print("=" * 60)


if __name__ == "__main__":
    main()
