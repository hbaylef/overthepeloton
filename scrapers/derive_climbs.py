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

import db  # local module: Turso/SQLite store (build-order step 2)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Legacy file, read once to seed the names cache into the store; no longer written.
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
LEN_MATCH_TOL_KM = 2.0     # fallback: max length gap (km) when the pool entry
                           # has no altitude (some races, e.g. the Tour, publish
                           # the climb pool with names + lengths but top=0)

# Public-slice route resolution. The frontend draws the DOWNSAMPLED route
# (publish.py: exactly this many evenly-spaced points, first+last kept) and
# measures every distance — including the profile's total km — along that
# reduced polyline. Climb positions MUST be measured on the SAME polyline, or
# `km_before_finish` lands on a different distance scale than the frontend's
# `totalKm` and every climb drifts (the downsample is ~3% shorter, non-uniformly,
# so anchoring to the finish doesn't save the interior). publish.py imports these
# two so there is one definition of "the points the site sees".
MAX_ROUTE_POINTS = 1500

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


def downsample(points: list, max_points: int = MAX_ROUTE_POINTS) -> list:
    """Reduce a point list to exactly max_points evenly-spaced points (when it's
    longer), always including the first and last point. This is the SAME reduction
    publish.py applies to build the public route slice, so detecting on its output
    measures climbs on the exact polyline the frontend draws (see MAX_ROUTE_POINTS)."""
    n = len(points)
    if n <= max_points:
        return points
    return [points[round(i * (n - 1) / (max_points - 1))] for i in range(max_points)]


def round_point(p) -> list:
    """(lat, lon, ele) -> [lat5, lon5, ele] — the exact rounding publish.py writes
    into the public slice (~1 m horizontal, integer metres). Detecting on rounded
    points keeps the distance/elevation series identical to what the site renders."""
    return [round(p[0], 5), round(p[1], 5), round(p[2])]


def frontend_points(points: list) -> list:
    """The points the public site actually draws: downsampled then rounded,
    exactly as publish.py emits them. Climb detection runs on these so positions
    share the frontend's distance scale."""
    return [round_point(p) for p in downsample(points)]


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
    # Detect on the SAME reduced+rounded polyline the frontend draws, so km_top /
    # total_km (hence km_before_finish) match the frontend's totalKm exactly.
    points = frontend_points(points)
    dist = cumulative_distance(points)
    ele_s = smooth_elevation(dist, [p[2] for p in points])
    detected = detect_climbs(dist, ele_s)
    return climbs_to_output(detected, dist[-1])


# ---------------------------------------------------------------------------
# Climb names from PCS (race-level route/climbs pool, matched by altitude)
# ---------------------------------------------------------------------------
def normalize_pool(raw_climbs: list) -> List[dict]:
    """RaceClimbs.climbs() rows -> [{name, top_m, length_km}]. Keep any named
    climb that has an altitude OR a length: most races give altitudes (matched on
    top_m), but some (e.g. the Tour) publish the pool with names + lengths and
    top=0/None — those are matched on length instead. top_m is 0.0 when absent."""
    pool = []
    for c in raw_climbs or []:
        name = c.get("climb_name")
        top = c.get("top")
        length = c.get("length")
        if name and (top is not None or length is not None):
            pool.append({"name": name.strip(),
                         "top_m": float(top) if top is not None else 0.0,
                         "length_km": length})
    return pool


def assign_names(climbs: List[dict], pool: List[dict],
                 tol_m: float = TOP_MATCH_TOL_M,
                 tol_len_km: float = LEN_MATCH_TOL_KM) -> List[dict]:
    """Attach PCS names to detected climbs (greedy, no pool entry reused).

    Preferred match is by summit altitude (top_m, length breaks ties). When a
    pool entry has no altitude (top_m == 0, as the Tour publishes its pool), fall
    back to matching that entry by length. Altitude matches always win over
    length matches. Unmatched climbs keep "Climb". Mutates and returns `climbs`."""
    if not pool:
        return climbs
    # candidates as (tier, primary_gap, secondary_gap, climb_i, pool_j);
    # tier 0 = altitude match (preferred), tier 1 = length-only match.
    cands = []
    for ci, c in enumerate(climbs):
        c_top = c.get("top_m")
        c_len = c.get("length_km") or 0.0
        for pj, p in enumerate(pool):
            p_top = p.get("top_m") or 0.0
            p_len = p.get("length_km")
            if p_top > 0 and c_top is not None:
                d_top = abs(c_top - p_top)
                if d_top <= tol_m:
                    d_len = abs(c_len - p_len) if p_len is not None else 0.0
                    cands.append((0, d_top, d_len, ci, pj))
            elif p_len is not None:                 # pool entry has no altitude
                d_len = abs(c_len - p_len)
                if d_len <= tol_len_km:
                    cands.append((1, d_len, 0.0, ci, pj))
    cands.sort()                       # altitude matches first, then by gap
    used_c, used_p = set(), set()
    for _tier, _g1, _g2, ci, pj in cands:
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


def needs_processing(payload: Optional[dict], named_pool: Optional[list]) -> bool:
    """Decide whether a stage race needs (re)deriving + naming.

    GPX is immutable in Turso, so derived climbs never change; PCS climb names
    don't change either. A race is DONE — skip it, no re-derive and no PCS name
    fetch — once its stage climbs are derived AND naming has been applied. 'Naming
    applied' means either at least one climb carries a real name, OR a non-empty
    PCS name pool is already cached for the race (so climbs that legitimately match
    no pool entry don't force endless re-fetches). We still process a race with no
    derived climbs yet, or one whose climbs are all unnamed with no pool cached
    (names still pending an earlier run that couldn't reach PCS)."""
    stages = (payload or {}).get("stages") or {}
    climbs = [c for v in stages.values() for c in v]
    if not climbs:
        return True                        # nothing derived yet
    has_real_name = any(c.get("name") and c.get("name") != "Climb" for c in climbs)
    return not (has_real_name or bool(named_pool))


def build_stage_climbs(client, slug: str, files: List[dict],
                       pool: Optional[List[dict]] = None) -> dict:
    """Derive {stage_number(str): [climbs]} for one race from its GPX files (read
    from the store), attaching PCS names (matched by altitude) when a name pool
    is given."""
    stages = {}
    for f in files:
        text = db.get_gpx(client, slug, f["filename"])
        if not text:
            log.warning(f"    missing GPX in store: {slug}/{f['filename']}")
            continue
        climbs = climbs_for_gpx(text)
        if climbs:
            assign_names(climbs, pool or [])
            stages[str(f["stage"])] = climbs
    return stages


def write_race_climbs(client, slug: str, name: str, stages: dict):
    """Merge derived stage climbs into the climbs doc in the store (preserving
    any existing fields, e.g. one-day `climbs` written by scrape_climbs)."""
    payload = db.get_document(client, db.KIND_CLIMBS, slug) or {}
    payload.update({
        "race": name,
        "race_slug": slug,
        "source": "gpx_derived",
        "updated_at": datetime.now().isoformat(),
        "is_one_day_race": False,
        "stages": stages,
    })
    payload.pop("climbs", None)   # stage races don't use the one-day key
    db.put_document(client, db.KIND_CLIMBS, slug, payload)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Derive stage-race climbs from GPX.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report how many stage races would be skipped vs processed, "
                         "then exit (no network, no re-derive, no writes)")
    ap.add_argument("--force", action="store_true",
                    help="re-derive EVERY stage race, ignoring the 'already derived + "
                         "named' skip. Needed to backfill after a detection change "
                         "(GPX is otherwise treated as immutable). Names are re-applied "
                         "from the cached pool, so no extra PCS calls.")
    args = ap.parse_args()

    client = db.open_db()
    log.info(f"Climbs store: {'remote Turso' if db.is_remote() else 'local SQLite file'}")

    # slug -> race name + PCS url (for the race-level name pool), from the store.
    race_docs = db.get_all_documents(client, db.KIND_RACE)
    pcs_urls = {slug: r.get("pcs_url") for slug, r in race_docs.items()}
    names = {slug: r.get("name", slug) for slug, r in race_docs.items()}

    # Persistent names cache lives in the caches table, seeded once from disk.
    names_cache = db.get_cache(client, db.CACHE_CLIMBS_NAMES)
    if names_cache is None:
        names_cache = (json.loads(NAMES_CACHE.read_text(encoding="utf-8"))
                       if NAMES_CACHE.exists() else {})
        if names_cache:
            log.info(f"Seeded climbs-names cache from legacy {NAMES_CACHE.name}")

    # Stage races with GPX. A race already derived + named is fixed (GPX immutable,
    # names don't change) → skip it: no re-derive, no PCS name fetch.
    stage_slugs = [s for s in db.gpx_slugs(client)
                   if any(f.get("stage") is not None for f in db.list_gpx(client, s))]
    todo, done = [], []
    for slug in stage_slugs:
        cached_pool = (names_cache.get(slug) or {}).get("pool")
        payload = db.get_document(client, db.KIND_CLIMBS, slug)
        process = args.force or needs_processing(payload, cached_pool)
        (todo if process else done).append(slug)
    log.info(f"Stage races derived + named (skip): {len(done)} · "
             f"to process (new / names pending): {len(todo)}")
    if args.dry_run:
        print("\n" + "=" * 60)
        print("  GPX-DERIVED STAGE CLIMBS — DRY RUN")
        print(f"  Stage races total:               {len(stage_slugs)}")
        print(f"  SKIPPED (derived + named):       {len(done)}")
        print(f"  to PROCESS (new / names pending): {len(todo)}")
        print("=" * 60)
        client.close()
        return

    slug_counts = {}
    total_climbs = total_named = races_with = 0

    for slug in todo:
        files = [f for f in db.list_gpx(client, slug) if f.get("stage") is not None]
        name = names.get(slug, slug)
        log.info(f"{slug}: {len(files)} stage GPX file(s)")
        pool = get_pool(names_cache, slug, pcs_urls.get(slug))
        stages = build_stage_climbs(client, slug, files, pool)
        climbs = [c for v in stages.values() for c in v]
        n = len(climbs)
        total_named += sum(1 for c in climbs if c["name"] != "Climb")
        write_race_climbs(client, slug, name, stages)
        slug_counts[slug] = (name, n)
        total_climbs += n
        if n > 0:
            races_with += 1

    db.put_cache(client, db.CACHE_CLIMBS_NAMES, names_cache)
    client.close()

    print("\n" + "=" * 60)
    print("  GPX-DERIVED STAGE CLIMBS")
    print(f"  Stage races skipped (done): {len(done)}")
    print(f"  Stage races processed:      {len(slug_counts)}")
    print(f"  Races with climbs:     {races_with}")
    print(f"  Total climbs derived:  {total_climbs}  (named: {total_named})")
    print("=" * 60)


if __name__ == "__main__":
    main()
