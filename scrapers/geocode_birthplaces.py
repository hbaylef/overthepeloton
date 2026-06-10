#!/usr/bin/env python3
"""
Geocode rider birthplaces (town name -> lat/lon) for the "local riders" section.

`scrape_riders.py` embeds each rider's `place_of_birth` (a town name only — PCS
gives no coordinates). This script resolves those towns to coordinates via
OpenStreetMap **Nominatim** and writes `birthplace_lat` / `birthplace_lon` onto
each rider in the startlist files. Results are cached in `birthplaces_cache.json`
(keyed by town|countrycode) so it's a one-time cost — daily runs then re-apply
coordinates from cache with no network calls (only genuinely new towns hit
Nominatim).

Run order: scrape_races -> scrape_riders -> geocode_birthplaces.

⚠️ Nominatim usage policy: max ~1 req/sec, a real User-Agent, and caching — all
honoured here. The big first pass (~1k towns) is best run once locally; after that
the cache carries it. On this machine's TLS-intercepting proxy use --insecure
(Nominatim is not behind Cloudflare, so unlike LFR this works).

Usage:
  python scrapers/geocode_birthplaces.py                 # geocode + embed
  python scrapers/geocode_birthplaces.py --insecure      # behind a TLS proxy
  python scrapers/geocode_birthplaces.py --embed-only     # re-apply cache, no net
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional, Tuple

import requests

import db  # local module: Turso/SQLite store (build-order step 2)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Legacy file, read once to seed the Turso cache on first run; no longer written.
CACHE_FILE = DATA_DIR / "birthplaces_cache.json"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "overthepeloton/1.0 (cycling dashboard; contact via github paludes)"
DELAY_S = 1.1            # Nominatim policy: <= 1 req/sec

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ===========================================================================
#  Pure helpers (no network — unit-tested)
# ===========================================================================
def cache_key(place: str, nationality: Optional[str]) -> str:
    """Stable cache key: 'town|cc' (cc lowercased, '' when unknown)."""
    cc = (nationality or "").strip().lower()
    return f"{(place or '').strip().lower()}|{cc}"


def parse_nominatim(payload) -> Tuple[Optional[float], Optional[float]]:
    """First result's (lat, lon) as floats, or (None, None) if empty/invalid."""
    if isinstance(payload, list) and payload:
        try:
            return float(payload[0]["lat"]), float(payload[0]["lon"])
        except (KeyError, ValueError, TypeError):
            return None, None
    return None, None


def needs_coords(rider: dict) -> bool:
    """True if the rider has a birthplace town but no coordinates yet."""
    return bool(rider.get("place_of_birth")) and rider.get("birthplace_lat") is None


def has_coords(entry: Optional[dict]) -> bool:
    """True if a cache entry holds real (non-null) coordinates.

    A town's lat/lon never changes once resolved, so such an entry is PERMANENT —
    it is skipped forever (no re-query). An absent entry, or one cached as
    {lat: None} from a past failed lookup, has *no* coordinates and is therefore
    still genuinely missing → it should be (re)queried. We no longer persist a
    failed lookup as a None entry, so failures naturally retry next run."""
    return bool(entry) and entry.get("lat") is not None and entry.get("lon") is not None


def plan_geocode(startlists: dict, cache: dict) -> Tuple[set, set]:
    """Split the towns referenced by the startlists into (to_fetch, resolved).

    `resolved` = unique town keys we already have coordinates for (skip, no
    network). `to_fetch` = unique town keys still missing coordinates (new towns,
    or ones a past lookup couldn't resolve) — the only ones that hit Nominatim.
    Pure: drives the dry-run report and the real loop's skip decision."""
    resolved, to_fetch = set(), set()
    for d in startlists.values():
        for r in d.get("riders", []):
            place = r.get("place_of_birth")
            if not place:
                continue
            key = cache_key(place, r.get("nationality"))
            (resolved if has_coords(cache.get(key)) else to_fetch).add(key)
    return to_fetch, resolved


# ===========================================================================
#  Network + IO
# ===========================================================================
def geocode(session: requests.Session, place: str,
            nationality: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    params = {"q": place, "format": "json", "limit": 1}
    cc = (nationality or "").strip().lower()
    if cc:
        params["countrycodes"] = cc
    try:
        r = session.get(NOMINATIM_URL, params=params, timeout=20)
        if r.status_code == 200:
            return parse_nominatim(r.json())
        log.warning(f"    Nominatim HTTP {r.status_code} for '{place}' ({cc})")
    except Exception as e:
        log.warning(f"    geocode error '{place}': {e}")
    return None, None


def load_cache(client) -> dict:
    """Load the birthplaces cache from the store, seeding it once from the
    legacy data/birthplaces_cache.json so the ~473 already-geocoded towns aren't
    re-fetched from Nominatim on the first run."""
    cached = db.get_cache(client, db.CACHE_BIRTHPLACES)
    if cached is None and CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            log.info(f"Seeded birthplaces cache from legacy {CACHE_FILE.name}")
        except Exception:
            pass
    return cached or {}


def save_cache(client, cache: dict):
    db.put_cache(client, db.CACHE_BIRTHPLACES, cache)


def main():
    ap = argparse.ArgumentParser(description="Geocode rider birthplaces (Nominatim).")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification (local TLS-proxy workaround)")
    ap.add_argument("--embed-only", action="store_true",
                    help="only re-apply cached coords to startlists (no network)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report how many towns would be skipped vs queried, then "
                         "exit (no network, no writes)")
    args = ap.parse_args()
    insecure = args.insecure or os.environ.get("LFR_INSECURE") == "1"

    client = db.open_db()
    log.info(f"Geocode store: {'remote Turso' if db.is_remote() else 'local SQLite file'}")
    cache = load_cache(client)
    startlists = db.get_all_documents(client, db.KIND_STARTLIST)

    # Idempotency report: a resolved town is fixed forever (skip); only towns we
    # still have no coordinates for ever hit Nominatim.
    to_fetch, resolved = plan_geocode(startlists, cache)
    log.info(f"Towns resolved (skip): {len(resolved)} · "
             f"towns still missing coords (would query): {len(to_fetch)}")
    if args.dry_run:
        print("\n" + "=" * 60)
        print("  BIRTHPLACE GEOCODE — DRY RUN")
        print(f"  towns SKIPPED (already resolved): {len(resolved)}")
        print(f"  towns to FETCH (missing coords):  {len(to_fetch)}")
        print(f"  cache size:                       {len(cache)} towns")
        print("=" * 60)
        client.close()
        return

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    if insecure:
        session.verify = False
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        log.warning("TLS verification DISABLED (--insecure).")

    geocoded = from_cache = embedded = failed = 0
    attempted = set()   # towns hit this run (avoid re-querying within one pass)

    for slug, d in startlists.items():
        changed = False
        for r in d.get("riders", []):
            place = r.get("place_of_birth")
            if not place:
                continue
            key = cache_key(place, r.get("nationality"))
            entry = cache.get(key)
            if has_coords(entry):
                from_cache += 1            # resolved town — permanent skip
            elif args.embed_only or key in attempted:
                pass                       # no network now / already tried this run
            else:
                lat, lon = geocode(session, place, r.get("nationality"))
                attempted.add(key)
                if lat is not None and lon is not None:
                    entry = {"lat": lat, "lon": lon}
                    cache[key] = entry     # persist ONLY successes → permanent skip
                    geocoded += 1
                    if geocoded % 25 == 0:
                        save_cache(client, cache)
                else:
                    entry = None           # not persisted → retried on a later run
                    failed += 1
                time.sleep(DELAY_S)
            ent = entry or {}
            if r.get("birthplace_lat") != ent.get("lat") or \
               r.get("birthplace_lon") != ent.get("lon"):
                r["birthplace_lat"] = ent.get("lat")
                r["birthplace_lon"] = ent.get("lon")
                changed = True
                embedded += 1
        if changed:
            db.put_document(client, db.KIND_STARTLIST, slug, d)

    save_cache(client, cache)
    client.close()
    print("\n" + "=" * 60)
    print("  BIRTHPLACE GEOCODE")
    print(f"  newly geocoded (network): {geocoded}")
    print(f"  skipped (already resolved): {from_cache}")
    print(f"  still unresolved (retry next run): {failed}")
    print(f"  rider coords embedded:    {embedded}")
    print(f"  cache size:               {len(cache)} towns")
    print("=" * 60)


if __name__ == "__main__":
    main()
