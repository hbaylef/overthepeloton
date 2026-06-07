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

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STARTLISTS_DIR = DATA_DIR / "startlists"
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


def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cache(cache: dict):
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Geocode rider birthplaces (Nominatim).")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification (local TLS-proxy workaround)")
    ap.add_argument("--embed-only", action="store_true",
                    help="only re-apply cached coords to startlists (no network)")
    args = ap.parse_args()
    insecure = args.insecure or os.environ.get("LFR_INSECURE") == "1"

    cache = load_cache()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    if insecure:
        session.verify = False
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        log.warning("TLS verification DISABLED (--insecure).")

    files = sorted(STARTLISTS_DIR.glob("*.json"))
    geocoded = from_cache = embedded = 0

    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"skip {f.name}: {e}")
            continue
        changed = False
        for r in d.get("riders", []):
            place = r.get("place_of_birth")
            if not place:
                continue
            key = cache_key(place, r.get("nationality"))
            if key not in cache:
                if args.embed_only:
                    continue
                lat, lon = geocode(session, place, r.get("nationality"))
                cache[key] = {"lat": lat, "lon": lon}
                geocoded += 1
                if geocoded % 25 == 0:
                    save_cache(cache)
                time.sleep(DELAY_S)
            else:
                from_cache += 1
            ent = cache.get(key, {})
            if r.get("birthplace_lat") != ent.get("lat") or \
               r.get("birthplace_lon") != ent.get("lon"):
                r["birthplace_lat"] = ent.get("lat")
                r["birthplace_lon"] = ent.get("lon")
                changed = True
                embedded += 1
        if changed:
            f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")

    save_cache(cache)
    print("\n" + "=" * 60)
    print("  BIRTHPLACE GEOCODE")
    print(f"  newly geocoded (network): {geocoded}")
    print(f"  served from cache:        {from_cache}")
    print(f"  rider coords embedded:    {embedded}")
    print(f"  cache size:               {len(cache)} towns")
    print("=" * 60)


if __name__ == "__main__":
    main()
