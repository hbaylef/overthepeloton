#!/usr/bin/env python3
"""
Publish the THIN PUBLIC SLICES from the private Turso store (build-order step 4).

The raw scraped data (PCS JSON, full .gpx, caches) lives only in Turso. The
public static site must never see the raw data — it reads small derived slices
committed into data/ and served by GitHub Pages. This script regenerates those
slices from the store:

  data/races.json              <- race docs            (the race calendar)
  data/startlists/{slug}.json  <- startlist docs       (riders shown on the page)
  data/climbs/{slug}.json      <- climbs docs          (profile highlights)
  data/climbs_index.json       <- derived from climbs docs
  data/routes/{slug}.json      <- DOWNSAMPLED from the raw gpx_files (NOT the
                                  original .gpx — just enough points to draw the
                                  map line + elevation profile)
  data/routes_index.json       <- which races have a route + per-stage distance
  data/predictions/{slug}.json <- prediction docs (when present)

Routes are the key privacy transform: the original .gpx never leaves Turso; the
public slice is a reduced point list (lat, lon, ele), rounded.

Run:
  python scrapers/publish.py
Connection follows db.py (remote Turso when TURSO_DATABASE_URL is set, else a
local SQLite file — so this can be tested locally against a seeded file).
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import db
from derive_climbs import parse_gpx, cumulative_distance

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STARTLISTS_DIR = DATA_DIR / "startlists"
CLIMBS_DIR = DATA_DIR / "climbs"
ROUTES_DIR = DATA_DIR / "routes"
PREDICTIONS_DIR = DATA_DIR / "predictions"

# Max points kept per route in the public slice. Plenty for a smooth map line
# and elevation profile; the full-resolution track stays private in Turso.
MAX_ROUTE_POINTS = 1500

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, obj, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(obj, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def _strip_ts(obj):
    """Deep copy with every 'updated_at' key removed, so two payloads that differ
    ONLY by their publish timestamp compare equal (no timestamp-only churn)."""
    if isinstance(obj, dict):
        return {k: _strip_ts(v) for k, v in obj.items() if k != "updated_at"}
    if isinstance(obj, list):
        return [_strip_ts(v) for v in obj]
    return obj


def _write_if_changed(path: Path, payload, compact: bool = False) -> bool:
    """Write payload only if its content (ignoring timestamps) differs from
    what's already on disk. Returns True if a write happened. Keeps the public
    repo from churning on days when nothing actually changed."""
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if _strip_ts(old) == _strip_ts(payload):
                return False
        except Exception:
            pass
    _write_json(path, payload, compact=compact)
    return True


# --------------------------------------------------------------------------- #
# Routes: downsample raw gpx -> thin public point list
# --------------------------------------------------------------------------- #
def downsample(points: list, max_points: int = MAX_ROUTE_POINTS) -> list:
    """Reduce a point list to exactly max_points evenly-spaced points (when it's
    longer), always including the first and last point so the distance/profile
    endpoints stay exact."""
    n = len(points)
    if n <= max_points:
        return points
    return [points[round(i * (n - 1) / (max_points - 1))] for i in range(max_points)]


def round_point(p) -> list:
    """(lat, lon, ele) -> [lat5, lon5, ele] — ~1 m horizontal precision, integer
    metres of elevation. Small payload, plenty for the map line + profile."""
    return [round(p[0], 5), round(p[1], 5), round(p[2])]


def total_distance_km(points: list) -> float:
    return cumulative_distance(points)[-1] if len(points) > 1 else 0.0


def build_route_doc(client, slug: str, name: str):
    """Downsampled route slice for one race, or None if it has no usable GPX."""
    routes = []
    for f in db.list_gpx(client, slug):
        text = db.get_gpx(client, slug, f["filename"])
        if not text:
            continue
        pts = parse_gpx(text)
        if len(pts) < 2:
            continue
        ds = [round_point(p) for p in downsample(pts)]
        routes.append({
            "stage": f["stage"],
            "filename": f["filename"],
            "distance_km": round(total_distance_km(pts), 1),
            "point_count": len(ds),
            "points": ds,
        })
    if not routes:
        return None
    # stage first (None last), then filename, for a stable file.
    routes.sort(key=lambda r: (r["stage"] is None, r["stage"] or 0, r["filename"]))
    return {"race_slug": slug, "name": name, "updated_at": _now(), "routes": routes}


# --------------------------------------------------------------------------- #
# Per-dataset publishers
# --------------------------------------------------------------------------- #
def publish_races(client):
    races = list(db.get_all_documents(client, db.KIND_RACE).values())
    races.sort(key=lambda r: r.get("startdate") or "9999-12-31")
    year = next((r.get("year") for r in races if r.get("year")), datetime.now().year)
    changed = _write_if_changed(DATA_DIR / "races.json", {
        "updated_at": _now(),
        "year": year,
        "total_races": len(races),
        "races": races,
    })
    return len(races), int(changed)


def publish_startlists(client):
    docs = db.get_all_documents(client, db.KIND_STARTLIST)
    changed = sum(_write_if_changed(STARTLISTS_DIR / f"{slug}.json", doc)
                  for slug, doc in docs.items())
    return len(docs), changed


def _count_climbs(doc: dict) -> int:
    if doc.get("is_one_day_race"):
        return len(doc.get("climbs", []))
    return sum(len(v) for v in doc.get("stages", {}).values())


def publish_climbs(client):
    docs = db.get_all_documents(client, db.KIND_CLIMBS)
    index = {}
    changed = 0
    for slug, doc in docs.items():
        changed += _write_if_changed(CLIMBS_DIR / f"{slug}.json", doc)
        n = _count_climbs(doc)
        index[slug] = {
            "name": doc.get("race"),
            "climbs_available": n > 0,
            "total_climbs": n,
            "source": doc.get("source"),
        }
    changed += _write_if_changed(DATA_DIR / "climbs_index.json",
                                 {"updated_at": _now(), "source": "mixed", "races": index})
    return len(docs), changed


def publish_routes(client):
    races = db.get_all_documents(client, db.KIND_RACE)
    names = {slug: r.get("name", slug) for slug, r in races.items()}
    index = {}
    published = changed = 0
    for slug in db.gpx_slugs(client):
        doc = build_route_doc(client, slug, names.get(slug, slug))
        if not doc:
            continue
        published += 1
        changed += _write_if_changed(ROUTES_DIR / f"{slug}.json", doc, compact=True)
        index[slug] = {
            "name": doc["name"],
            "route_available": True,
            "stages": [{"stage": r["stage"], "distance_km": r["distance_km"]}
                       for r in doc["routes"]],
        }
    # Mark races with no route too, so the frontend can show "route not available".
    for slug, name in names.items():
        index.setdefault(slug, {"name": name, "route_available": False, "stages": []})
    changed += _write_if_changed(DATA_DIR / "routes_index.json",
                                 {"updated_at": _now(), "races": index})
    return published, changed


def publish_predictions(client):
    docs = db.get_all_documents(client, db.KIND_PREDICTIONS)
    if not docs:
        return 0, 0
    index = {}
    changed = 0
    for slug, doc in docs.items():
        changed += _write_if_changed(PREDICTIONS_DIR / f"{slug}.json", doc)
        index[slug] = {
            "name": doc.get("race"),
            "prediction_available": True,
            "is_one_day_race": doc.get("is_one_day_race"),
            "scored_rider_count": doc.get("scored_rider_count"),
        }
    changed += _write_if_changed(DATA_DIR / "predictions_index.json",
                                 {"updated_at": _now(), "model": "experimental", "races": index})
    return len(docs), changed


def main():
    client = db.open_db()
    log.info(f"Publish source: {'remote Turso' if db.is_remote() else 'local SQLite file'}")

    races = publish_races(client)
    sl = publish_startlists(client)
    climbs = publish_climbs(client)
    routes = publish_routes(client)
    pred = publish_predictions(client)
    client.close()

    print("\n" + "=" * 60)
    print("  PUBLISH PUBLIC SLICES        (total / files rewritten)")
    print(f"  races.json:           {races[0]} / {races[1]}")
    print(f"  startlists:           {sl[0]} / {sl[1]}")
    print(f"  climbs:               {climbs[0]} / {climbs[1]}")
    print(f"  routes (downsampled): {routes[0]} / {routes[1]}")
    print(f"  predictions:          {pred[0]} / {pred[1]}")
    print("=" * 60)


if __name__ == "__main__":
    main()
