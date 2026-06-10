#!/usr/bin/env python3
"""
No-network tests for the remaining-migrations batch (build-order step 2: gpx,
climbs, start_times, predictions). Exercises the new Turso plumbing against a
temp local SQLite file; network (cyclingstage/PCS) is avoided or injected.

Run:  python scrapers/test_migration2_db.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.pop("TURSO_DATABASE_URL", None)
os.environ.pop("TURSO_AUTH_TOKEN", None)

import db
import scrape_gpx as gpx
import derive_climbs as dc
import scrape_climbs as sc
import scrape_start_times as st
import score_riders as sr


# Clients opened by tests, closed in _run(). libsql's sync client runs a
# NON-DAEMON background thread — an unclosed client keeps the interpreter
# alive forever after the tests finish (observed hang, 2026-06-10).
_clients = []


def _fresh_db():
    p = Path(tempfile.mkdtemp(prefix="otp_m2_")) / "t.db"
    os.environ["OVERTHEPELOTON_DB"] = str(p)
    client = db.open_db()
    _clients.append(client)
    return client


def _tmpdir(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix))


def _climb_gpx(n=25):
    """A GPX of a steady ~7% climb (clears derive_climbs' thresholds)."""
    pts = []
    for i in range(n):
        lat = 45.0 + i * 0.002      # ~0.22 km/step
        ele = 100.0 + i * 15.0      # +360 m over ~5.3 km
        pts.append(f'<trkpt lat="{lat:.5f}" lon="6.00000"><ele>{ele:.1f}</ele></trkpt>')
    return "<gpx><trk><trkseg>" + "".join(pts) + "</trkseg></trk></gpx>"


# --------------------------------------------------------------------------- #
# scrape_gpx: import-from-disk + the has_gpx skip gate
# --------------------------------------------------------------------------- #
def test_gpx_import_disk_and_gate():
    client = _fresh_db()
    tmp = _tmpdir("otp_gpxdata_")
    (tmp / "gpx" / "giro-2026").mkdir(parents=True)
    (tmp / "gpx" / "giro-2026" / "stage-1-route.gpx").write_text(
        "<gpx><trk></trk></gpx>", encoding="utf-8")
    gpx.DATA_DIR = tmp
    disk_index = {"giro-2026": {"source": "cyclingstage", "files": [
        {"filename": "stage-1-route.gpx", "stage": 1, "url": "http://x/s1.gpx",
         "local_path": "gpx/giro-2026/stage-1-route.gpx"}]}}

    assert db.has_gpx(client, "giro-2026") is False
    assert gpx.import_disk_gpx(client, "giro-2026", disk_index) == 1
    assert db.has_gpx(client, "giro-2026") is True        # gate now trips → skip
    meta = db.list_gpx(client, "giro-2026")
    assert meta == [{"filename": "stage-1-route.gpx", "stage": 1,
                     "source": "cyclingstage", "url": "http://x/s1.gpx"}]
    # No disk entry → nothing imported.
    assert gpx.import_disk_gpx(client, "missing-2026", disk_index) == 0


# --------------------------------------------------------------------------- #
# derive_climbs: read GPX from the store, merge into the climbs doc
# --------------------------------------------------------------------------- #
def test_derive_build_stage_climbs_from_store():
    client = _fresh_db()
    db.put_gpx(client, "giro-2026", "stage-1-route.gpx", _climb_gpx(), stage=1)
    files = [f for f in db.list_gpx(client, "giro-2026") if f["stage"] is not None]
    stages = dc.build_stage_climbs(client, "giro-2026", files, pool=[])
    assert "1" in stages and len(stages["1"]) >= 1
    assert stages["1"][0]["name"] == "Climb"     # no pool → unnamed


def test_derive_write_race_climbs_merges_and_drops_oneday_key():
    client = _fresh_db()
    # A pre-existing doc (as scrape_climbs would leave it) with a one-day key.
    db.put_document(client, db.KIND_CLIMBS, "giro-2026",
                    {"race": "Giro", "climbs": ["stale"], "source": "procyclingstats"})
    dc.write_race_climbs(client, "giro-2026", "Giro d'Italia", {"1": [{"name": "Climb"}]})
    out = db.get_document(client, db.KIND_CLIMBS, "giro-2026")
    assert out["stages"] == {"1": [{"name": "Climb"}]}
    assert out["source"] == "gpx_derived"
    assert "climbs" not in out                    # one-day key dropped for stage races


# --------------------------------------------------------------------------- #
# Caches that move to the caches table (seed once from disk, then persist)
# --------------------------------------------------------------------------- #
def test_climbs_cache_seed_and_save():
    client = _fresh_db()
    legacy = _tmpdir("otp_cc_") / "climbs_cache.json"
    legacy.write_text(json.dumps({"updated_at": "x", "urls": {"u1": {"climbs": []}}}),
                      encoding="utf-8")
    sc.CACHE_FILE = legacy
    cache = sc.load_cache(client)
    assert "u1" in cache["urls"]
    cache["urls"]["u2"] = {"climbs": [{"name": "X"}]}
    sc.save_cache(client, cache)
    assert set(db.get_cache(client, db.CACHE_CLIMBS)["urls"]) == {"u1", "u2"}


def test_start_times_cache_seed_and_save():
    client = _fresh_db()
    legacy = _tmpdir("otp_stc_") / "start_times_cache.json"
    legacy.write_text(json.dumps({"updated_at": "x", "urls": {"u1": {"start_time": "14:00"}}}),
                      encoding="utf-8")
    st.CACHE_FILE = legacy
    cache = st.load_cache(client)
    assert cache["urls"]["u1"]["start_time"] == "14:00"
    cache["urls"]["u2"] = {"start_time": "09:30"}
    st.save_cache(client, cache)
    assert set(db.get_cache(client, db.CACHE_START_TIMES)["urls"]) == {"u1", "u2"}


# --------------------------------------------------------------------------- #
# score_riders: predict_race reads the startlist from the store
# --------------------------------------------------------------------------- #
def test_score_predict_race_reads_startlist_from_store():
    client = _fresh_db()
    race = {"slug": "il-lombardia-2026", "name": "Il Lombardia",
            "is_one_day_race": True, "stage_type": "climber"}
    db.put_document(client, db.KIND_STARTLIST, "il-lombardia-2026", {"riders": [
        {"name": "A", "rider_url": "rider/a", "specialties": {"career": {
            "one_day_races": 9000, "gc": 5000, "tt": 100, "sprint": 0,
            "climber": 9999, "hills": 4000}}},
        {"name": "B", "rider_url": "rider/b", "specialties": {"career": {
            "one_day_races": 100, "gc": 100, "tt": 100, "sprint": 9000,
            "climber": 100, "hills": 100}}},
    ]})
    pred = sr.predict_race(client, race)
    assert pred is not None and pred["is_one_day_race"] is True
    assert pred["scored_rider_count"] == 2
    # The climber should outrank the sprinter on a climber's race.
    assert pred["riders"][0]["name"] == "A"
    # Missing startlist → None.
    assert sr.predict_race(client, {"slug": "nope-2026", "is_one_day_race": True}) is None


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    try:
        for t in tests:
            t()
            print(f"  [ok] {t.__name__}")
            passed += 1
        print(f"\n{passed}/{len(tests)} passed")
    finally:
        for c in _clients:          # else the process never exits (see _clients)
            try:
                c.close()
            except Exception:
                pass


if __name__ == "__main__":
    _run()
