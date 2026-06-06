#!/usr/bin/env python3
"""
R4 — Per-stage climbs for stage races, DERIVED from the GPX elevation profile.

Why this exists
---------------
PCS and cyclingstage both publish per-stage climbs ONLY as images (a profile
JPG with the climbs painted on it) — there is no structured climb text on either
site to scrape (verified 2026-06-06; see PROJECT_CONTEXT §0). The whole-race PCS
`route/climbs` page has the stats but no stage assignment. So for stage races we
detect the climbs ourselves from the GPX we already download.

One-day races keep their real, named PCS climbs from scrape_climbs.py — this
script only fills in stage races (which scrape_climbs leaves empty).

What it does
------------
For each stage race in gpx_index.json, for each stage GPX file, it:
  1. parses lat/lon/ele track points,
  2. builds a cumulative-distance + smoothed-elevation series (matching the
     frontend's 6371 km haversine + ~250 m grade smoothing),
  3. detects sustained climbs via hysteresis (a climb runs from a foot to its
     summit, tolerating small dips), and
  4. writes them into data/climbs/{slug}.json under `stages` in the SAME shape
     the frontend already renders for PCS climbs:
        { "name", "km_before_finish", "length_km", "steepness", "top_m" }
     km_before_finish is total_km − summit_km, so the frontend anchors each climb
     to its drawn GPX finish exactly as it does for one-day climbs.

Stages are keyed by the GPX file's stage number (same key the frontend uses:
gpx_index files[].stage).

Climb NAMES: PCS publishes the race's climbs (with altitude) on the race-level
route/climbs page — the same RaceClimbs call one-day races use. We fetch that
pool once per race and attach a name to each detected climb by matching on
altitude (top_m), caching the pool in climbs_names_cache.json. PCS is only
reachable from Actions (this machine's TLS proxy breaks it), so names populate on
the next Actions run; until then (and for any unmatched climb) the name is
"Climb" — the length/avg-gradient stats still carry the meaning.

GPX detection itself needs NO network and runs anywhere the GPX files are
present (locally or in Actions); only the name fetch needs PCS.

Usage:
  python scrapers/derive_climbs.py
"""

import json
import logging
import math
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GPX_INDEX = DATA_DIR / "gpx_index.json"
RACES_FILE = DATA_DIR / "races.json"
CLIMBS_DIR = DATA_DIR / "climbs"
INDEX_FILE = DATA_DIR / "climbs_index.json"
NAMES_CACHE = DATA_DIR / "climbs_names_cache.json"

# Climb names: PCS publishes the race's climbs (with altitude) on the race-level
# route/climbs page — the SAME RaceClimbs call one-day races use. We fetch that
# pool once per race and attach a name to each GPX-detected climb by matching on
# altitude (top_m is absolute, so it doesn't depend on how km/length are
# measured). PCS is only reachable from GitHub Actions (this machine's TLS proxy
# breaks it), so names are CACHED (climbs_names_cache.json) and persist between
# runs; an unmatched climb stays "Climb".
NAMES_CACHE_DAYS = 30      # names don't change — refetch monthly
TOP_MATCH_TOL_M = 40       # max altitude gap (m) to accept a name match

# --- Detection tuning (uncalibrated starting values; output is "experimental") --
SMOOTH_M = 200.0        # elevation smoothing window (m) — kills GPS noise
MIN_LENGTH_KM = 1.0     # ignore rises shorter than this
MIN_GAIN_M = 60.0       # ignore climbs with less total ascent than this
MIN_AVG_GRADE = 3.0     # ignore climbs gentler than this on average (%)
DROP_TOL_M = 25.0       # descent from the running summit tolerated mid-climb (m)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure transforms (no IO / no network — unit-testable)
# ---------------------------------------------------------------------------
def parse_gpx(text: str) -> List[Tuple[float, float, float]]:
    """Extract (lat, lon, ele) track points from GPX text. Points missing an
    elevation are skipped (we can't grade them)."""
    pts = []
    for m in re.finditer(
            r'<trkpt\b[^>]*?lat="([-\d.]+)"[^>]*?lon="([-\d.]+)"[^>]*?>(.*?)</trkpt>',
            text, flags=re.S):
        ele_m = re.search(r'<ele>\s*([-\d.]+)\s*</ele>', m.group(3))
        if not ele_m:
            continue
        pts.append((float(m.group(1)), float(m.group(2)), float(ele_m.group(1))))
    return pts


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (R=6371, matching the frontend)."""
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def cumulative_distance(points: List[Tuple[float, float, float]]) -> List[float]:
    """Cumulative km at each point (first = 0)."""
    dist = [0.0]
    for i in range(1, len(points)):
        dist.append(dist[-1] + haversine_km(points[i - 1][0], points[i - 1][1],
                                             points[i][0], points[i][1]))
    return dist


def smooth_elevation(dist_km: List[float], ele: List[float],
                     window_m: float = SMOOTH_M) -> List[float]:
    """Centred moving average of elevation over a +/- window/2 distance window.
    Two-pointer over the monotonic distance series, so O(n)."""
    n = len(ele)
    if n == 0:
        return []
    half = (window_m / 1000.0) / 2.0
    out = [0.0] * n
    lo = hi = 0
    run = 0.0   # running sum of ele[lo..hi)
    for i in range(n):
        while lo < n and dist_km[lo] < dist_km[i] - half:
            run -= ele[lo]; lo += 1
        while hi < n and dist_km[hi] <= dist_km[i] + half:
            run += ele[hi]; hi += 1
        out[i] = run / max(1, hi - lo)
    return out


def detect_climbs(dist_km: List[float], ele: List[float]) -> List[dict]:
    """
    Detect sustained climbs from a (distance, smoothed-elevation) series.

    Hysteresis walk: from each local foot, extend the climb while the road keeps
    trending up — tracking the running summit and tolerating dips up to
    DROP_TOL_M below it. The climb ends at the summit once the road drops past
    that tolerance (or the route ends). A segment is kept only if it clears the
    length / gain / average-gradient thresholds.

    Returns climbs as {km_start, km_top, length_km, gain_m, avg_grade, top_m},
    in race order. (km_before_finish is added later, once total_km is known.)
    """
    n = len(ele)
    climbs: List[dict] = []
    i = 0
    while i < n - 1:
        if ele[i + 1] <= ele[i]:        # only start a climb where it rises
            i += 1
            continue
        start = i
        summit = i
        summit_ele = ele[i]
        j = i + 1
        while j < n:
            if ele[j] >= summit_ele:
                summit = j
                summit_ele = ele[j]
            elif summit_ele - ele[j] > DROP_TOL_M:
                break                   # dropped off the back of the summit
            j += 1

        length = dist_km[summit] - dist_km[start]
        gain = ele[summit] - ele[start]
        grade = (gain / (length * 1000.0) * 100.0) if length > 0 else 0.0
        if length >= MIN_LENGTH_KM and gain >= MIN_GAIN_M and grade >= MIN_AVG_GRADE:
            climbs.append({
                "km_start": dist_km[start],
                "km_top": dist_km[summit],
                "length_km": length,
                "gain_m": gain,
                "avg_grade": grade,
                "top_m": ele[summit],
            })
        i = summit if summit > start else start + 1
    return climbs


def climbs_to_output(detected: List[dict], total_km: float) -> List[dict]:
    """Map detected climbs to the on-disk/frontend climb shape."""
    out = []
    for c in detected:
        out.append({
            "name": "Climb",
            "km_before_finish": round(total_km - c["km_top"], 1),
            "length_km": round(c["length_km"], 1),
            "steepness": round(c["avg_grade"], 1),
            "top_m": round(c["top_m"]),
        })
    return out


def climbs_for_gpx(text: str) -> List[dict]:
    """Full pipeline for one GPX file -> output climbs (empty if too few points)."""
    points = parse_gpx(text)
    if len(points) < 10:
        return []
    dist = cumulative_distance(points)
    ele_s = smooth_elevation(dist, [p[2] for p in points])
    detected = detect_climbs(dist, ele_s)
    return climbs_to_output(detected, dist[-1])


# ---------------------------------------------------------------------------
# Climb names from PCS (race-level route/climbs pool, matched by altitude)
# ---------------------------------------------------------------------------
def normalize_pool(raw_climbs: list) -> List[dict]:
    """RaceClimbs.climbs() rows -> [{name, top_m, length_km}] (named + altitude
    only; those are all we need to attach a name by altitude)."""
    pool = []
    for c in raw_climbs or []:
        name = c.get("climb_name")
        top = c.get("top")
        if name and top is not None:
            pool.append({"name": name.strip(),
                         "top_m": float(top),
                         "length_km": c.get("length")})
    return pool


def assign_names(climbs: List[dict], pool: List[dict],
                 tol_m: float = TOP_MATCH_TOL_M) -> List[dict]:
    """Attach PCS names to detected climbs by nearest altitude (greedy, no pool
    entry reused). Length breaks altitude ties. Unmatched climbs keep "Climb".
    Mutates and returns `climbs`."""
    if not pool:
        return climbs
    # all candidate (altitude gap, length gap, climb_i, pool_j) within tolerance
    cands = []
    for ci, c in enumerate(climbs):
        for pj, p in enumerate(pool):
            d_top = abs(c["top_m"] - p["top_m"])
            if d_top <= tol_m:
                d_len = (abs((c.get("length_km") or 0) - (p.get("length_km") or 0))
                         if p.get("length_km") is not None else 0.0)
                cands.append((d_top, d_len, ci, pj))
    cands.sort()                       # best altitude match first, then length
    used_c, used_p = set(), set()
    for _, _, ci, pj in cands:
        if ci in used_c or pj in used_p:
            continue
        climbs[ci]["name"] = pool[pj]["name"]
        used_c.add(ci); used_p.add(pj)
    return climbs


def fetch_pool(pcs_url: str) -> Optional[List[dict]]:
    """Fetch the race-level named-climb pool from PCS. Returns None on any
    failure (so the caller falls back to cache rather than caching a miss)."""
    try:
        from procyclingstats import RaceClimbs   # lazy: only needed in Actions
        raw = RaceClimbs(pcs_url.rstrip("/") + "/route/climbs").climbs()
        return normalize_pool(raw)
    except Exception as e:
        log.warning(f"    name pool unavailable ({pcs_url}): {e}")
        return None


def get_pool(cache: dict, slug: str, pcs_url: Optional[str]) -> List[dict]:
    """Cache-aware name pool. Reuses a fresh, non-empty cached pool; otherwise
    tries PCS and records the result. Returns [] when nothing is available
    (e.g. PCS unreachable and nothing cached yet)."""
    entry = cache.get(slug)
    if entry and entry.get("pool"):
        try:
            from datetime import timedelta
            if datetime.now() - datetime.fromisoformat(entry["_at"]) < \
                    timedelta(days=NAMES_CACHE_DAYS):
                return entry["pool"]
        except Exception:
            pass
    if not pcs_url:
        return entry.get("pool", []) if entry else []
    fetched = fetch_pool(pcs_url)
    if fetched:                        # only overwrite cache with a real result
        cache[slug] = {"pool": fetched, "_at": datetime.now().isoformat()}
        return fetched
    return entry.get("pool", []) if entry else []


# ---------------------------------------------------------------------------
# IO / assembly
# ---------------------------------------------------------------------------
def stage_files(race_entry: dict) -> List[dict]:
    """GPX files for a race that carry a stage number (skip one-day route.gpx)."""
    return [f for f in race_entry.get("files", []) if f.get("stage") is not None]


def build_stage_climbs(race_entry: dict, pool: Optional[List[dict]] = None) -> dict:
    """Derive {stage_number(str): [climbs]} for one race from its GPX files,
    attaching PCS names (matched by altitude) when a name pool is given."""
    stages = {}
    for f in stage_files(race_entry):
        path = DATA_DIR / f["local_path"]
        if not path.exists():
            log.warning(f"    missing GPX: {f['local_path']}")
            continue
        climbs = climbs_for_gpx(path.read_text(encoding="utf-8", errors="ignore"))
        if climbs:
            assign_names(climbs, pool or [])
            stages[str(f["stage"])] = climbs
    return stages


def write_race_file(slug: str, name: str, stages: dict):
    """Merge derived stage climbs into data/climbs/{slug}.json (preserving any
    existing fields)."""
    path = CLIMBS_DIR / f"{slug}.json"
    payload = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    payload.update({
        "race": name,
        "race_slug": slug,
        "source": "gpx_derived",
        "updated_at": datetime.now().isoformat(),
        "is_one_day_race": False,
        "stages": stages,
    })
    payload.pop("climbs", None)   # stage races don't use the one-day key
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def update_index(slug_counts: dict):
    """Update climbs_index.json entries for the stage races we derived, leaving
    one-day (PCS) entries untouched."""
    index = {"updated_at": None, "source": "mixed", "races": {}}
    if INDEX_FILE.exists():
        try:
            index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    index.setdefault("races", {})
    for slug, (name, total) in slug_counts.items():
        index["races"][slug] = {
            "name": name,
            "climbs_available": total > 0,
            "total_climbs": total,
            "source": "gpx_derived",
        }
    index["updated_at"] = datetime.now().isoformat()
    index["source"] = "mixed"
    with open(INDEX_FILE, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)


def main():
    if not GPX_INDEX.exists():
        log.error(f"No {GPX_INDEX} — run scrape_gpx.py first.")
        return

    races = json.loads(GPX_INDEX.read_text(encoding="utf-8")).get("races", {})
    CLIMBS_DIR.mkdir(parents=True, exist_ok=True)

    # slug -> PCS url (for the race-level name pool) and a persistent names cache
    pcs_urls = {r["slug"]: r.get("pcs_url")
                for r in (json.loads(RACES_FILE.read_text(encoding="utf-8")).get("races", [])
                          if RACES_FILE.exists() else [])}
    names_cache = (json.loads(NAMES_CACHE.read_text(encoding="utf-8"))
                   if NAMES_CACHE.exists() else {})

    slug_counts = {}
    total_climbs = 0
    total_named = 0
    races_with = 0

    for slug, entry in races.items():
        files = stage_files(entry)
        if not files:                      # one-day race or no GPX → skip
            continue
        name = entry.get("name", slug)
        log.info(f"{slug}: {len(files)} stage GPX file(s)")
        pool = get_pool(names_cache, slug, pcs_urls.get(slug))
        stages = build_stage_climbs(entry, pool)
        climbs = [c for v in stages.values() for c in v]
        n = len(climbs)
        named = sum(1 for c in climbs if c["name"] != "Climb")
        write_race_file(slug, name, stages)
        slug_counts[slug] = (name, n)
        total_climbs += n
        total_named += named
        if n > 0:
            races_with += 1

    update_index(slug_counts)
    with open(NAMES_CACHE, "w", encoding="utf-8") as fh:
        json.dump(names_cache, fh, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("  GPX-DERIVED STAGE CLIMBS")
    print(f"  Stage races processed: {len(slug_counts)}")
    print(f"  Races with climbs:     {races_with}")
    print(f"  Total climbs derived:  {total_climbs}  (named: {total_named})")
    print("=" * 60)


if __name__ == "__main__":
    main()
