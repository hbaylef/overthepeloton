#!/usr/bin/env python3
"""
No-network tests for publish.py (build-order step 4). Pure slice/route helpers
plus a local integration run: seed a temp SQLite store, publish into a temp dir,
assert the public slices + downsampled routes are produced.

Run:  python scrapers/test_publish.py
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
import publish as pub


# Clients opened by tests, closed in _run(). libsql's sync client runs a
# NON-DAEMON background thread — an unclosed client keeps the interpreter
# alive forever after the tests finish (observed hang, 2026-06-10).
_clients = []


def _fresh_db():
    p = Path(tempfile.mkdtemp(prefix="otp_pub_")) / "t.db"
    os.environ["OVERTHEPELOTON_DB"] = str(p)
    client = db.open_db()
    _clients.append(client)
    return client


def _redirect_output() -> Path:
    out = Path(tempfile.mkdtemp(prefix="otp_pubout_"))
    pub.DATA_DIR = out
    pub.STARTLISTS_DIR = out / "startlists"
    pub.CLIMBS_DIR = out / "climbs"
    pub.ROUTES_DIR = out / "routes"
    pub.PREDICTIONS_DIR = out / "predictions"
    return out


def _gpx(n=4000):
    pts = []
    for i in range(n):
        lat = 45.0 + i * 0.0005
        ele = 100.0 + (i % 200)
        pts.append(f'<trkpt lat="{lat:.5f}" lon="6.00000"><ele>{ele:.1f}</ele></trkpt>')
    return "<gpx><trk><trkseg>" + "".join(pts) + "</trkseg></trk></gpx>"


def test_downsample_caps_and_keeps_endpoints():
    pts = [(float(i), 0.0, 0.0) for i in range(5000)]
    ds = pub.downsample(pts, max_points=1500)
    assert len(ds) <= 1500
    assert ds[0] == pts[0] and ds[-1] == pts[-1]
    # Already small -> unchanged.
    assert pub.downsample(pts[:10], 1500) == pts[:10]


def test_round_point():
    assert pub.round_point((45.123456, 6.987654, 123.45)) == [45.12346, 6.98765, 123]


def test_build_route_doc_downsamples():
    client = _fresh_db()
    db.put_gpx(client, "giro-2026", "stage-1-route.gpx", _gpx(4000), stage=1)
    doc = pub.build_route_doc(client, "giro-2026", "Giro d'Italia")
    assert doc["name"] == "Giro d'Italia"
    r = doc["routes"][0]
    assert r["stage"] == 1 and r["point_count"] <= pub.MAX_ROUTE_POINTS
    assert r["distance_km"] > 0
    assert len(r["points"][0]) == 3            # [lat, lon, ele]
    # None when the race has no GPX at all.
    assert pub.build_route_doc(client, "no-gpx-2026", "X") is None


def test_publish_writes_all_slices():
    client = _fresh_db()
    db.put_document(client, db.KIND_RACE, "giro-2026", {
        "slug": "giro-2026", "name": "Giro d'Italia", "year": 2026,
        "startdate": "2026-05-01", "is_one_day_race": False, "stages": [{}]})
    db.put_document(client, db.KIND_STARTLIST, "giro-2026",
                    {"race_slug": "giro-2026", "riders": [{"name": "A"}]})
    db.put_document(client, db.KIND_CLIMBS, "giro-2026",
                    {"race": "Giro d'Italia", "is_one_day_race": False,
                     "stages": {"1": [{"name": "Climb"}]}})
    db.put_document(client, db.KIND_PREDICTIONS, "giro-2026",
                    {"race": "Giro d'Italia", "is_one_day_race": False,
                     "scored_rider_count": 1, "riders": []})
    db.put_gpx(client, "giro-2026", "stage-1-route.gpx", _gpx(3000), stage=1)
    client.close()

    out = _redirect_output()
    pub.main()

    races = json.loads((out / "races.json").read_text(encoding="utf-8"))
    assert races["total_races"] == 1 and races["races"][0]["slug"] == "giro-2026"
    assert (out / "startlists" / "giro-2026.json").exists()

    climbs_idx = json.loads((out / "climbs_index.json").read_text(encoding="utf-8"))
    assert climbs_idx["races"]["giro-2026"]["total_climbs"] == 1

    route = json.loads((out / "routes" / "giro-2026.json").read_text(encoding="utf-8"))
    assert route["routes"][0]["point_count"] <= pub.MAX_ROUTE_POINTS
    routes_idx = json.loads((out / "routes_index.json").read_text(encoding="utf-8"))
    assert routes_idx["races"]["giro-2026"]["route_available"] is True

    assert (out / "predictions" / "giro-2026.json").exists()
    # The raw .gpx must NOT be written anywhere in the public output.
    assert not list(out.rglob("*.gpx"))


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
