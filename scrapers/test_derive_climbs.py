#!/usr/bin/env python3
"""
No-network logic test for derive_climbs.py.

Builds a synthetic GPX with known climbs (so detection is asserted exactly) and
checks the pure transforms + the foot->summit detection + the output mapping.
Run:  python scrapers/test_derive_climbs.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import derive_climbs as dc

LAT0, LON0 = 45.0, 5.0
DEG_PER_KM = 1.0 / 111.195   # latitude degrees per km (lon held constant)


def build_gpx(elevations, dx_km=0.25):
    """GPX text walking due north so each step is ~dx_km along latitude only."""
    rows = []
    for i, ele in enumerate(elevations):
        lat = LAT0 + i * dx_km * DEG_PER_KM
        rows.append(f'<trkpt lat="{lat:.9f}" lon="{LON0:.9f}"><ele>{ele:.3f}</ele></trkpt>')
    return "<gpx><trk><trkseg>" + "".join(rows) + "</trkseg></trk></gpx>"


def ramp(a, b, n):
    """n elevations linearly from a to b inclusive."""
    return [a + (b - a) * k / (n - 1) for k in range(n)]


def test_parse_gpx_extracts_points():
    pts = dc.parse_gpx(build_gpx([100, 110, 120]))
    assert len(pts) == 3, pts
    assert abs(pts[0][2] - 100) < 1e-6 and abs(pts[2][2] - 120) < 1e-6


def test_parse_gpx_skips_points_without_ele():
    txt = ('<gpx><trkpt lat="45.0" lon="5.0"><ele>100</ele></trkpt>'
           '<trkpt lat="45.1" lon="5.0"></trkpt></gpx>')
    assert len(dc.parse_gpx(txt)) == 1


def test_haversine_one_degree_lat():
    # ~111 km per degree of latitude
    assert abs(dc.haversine_km(45.0, 5.0, 46.0, 5.0) - 111.2) < 0.5


def test_cumulative_distance_scale():
    pts = dc.parse_gpx(build_gpx([0, 0, 0, 0], dx_km=0.25))
    dist = dc.cumulative_distance(pts)
    assert dist[0] == 0.0
    assert abs(dist[-1] - 0.75) < 0.02, dist   # 3 steps * 0.25 km


def test_smooth_reduces_spikes():
    dist = [i * 0.05 for i in range(11)]          # 50 m spacing
    ele = [100] * 5 + [200] + [100] * 5           # single 100 m spike
    sm = dc.smooth_elevation(dist, ele, window_m=200)
    assert sm[5] < 160, sm[5]                     # spike pulled down by neighbours


def _profile():
    # 0-4 km flat @100; 4-9 km climb to 400 (5 km, 6%); 9-13 km descent to 150;
    # 13-13.5 km small +8 m bump; 13.5-15 km flat. dx = 0.25 km.
    flat1 = [100] * 16
    climb = ramp(100, 400, 21)[1:]      # 5 km up
    desc  = ramp(400, 150, 17)[1:]      # 4 km down
    bump  = ramp(150, 158, 3)[1:]       # 0.5 km tiny bump
    flat2 = [158] * 6
    return flat1 + climb + desc + bump + flat2


def test_detect_finds_real_climb_ignores_bump():
    txt = build_gpx(_profile())
    climbs = dc.climbs_for_gpx(txt)
    assert len(climbs) == 1, climbs            # the 8 m bump is filtered out
    c = climbs[0]
    assert abs(c["length_km"] - 5.0) < 0.4, c
    assert abs(c["steepness"] - 6.0) < 0.7, c
    assert abs(c["top_m"] - 400) < 5, c


def test_km_before_finish_anchored_to_finish():
    txt = build_gpx(_profile())                # total 15 km, summit at km 9
    climbs = dc.climbs_for_gpx(txt)
    assert abs(climbs[0]["km_before_finish"] - 6.0) < 0.4, climbs[0]


def test_summit_finish_has_zero_km_before_finish():
    # climb all the way to the final point -> km_before_finish == 0
    txt = build_gpx([100] * 8 + ramp(100, 500, 25)[1:])
    climbs = dc.climbs_for_gpx(txt)
    assert climbs, "expected a summit-finish climb"
    assert climbs[-1]["km_before_finish"] == 0.0, climbs[-1]


def test_stage_files_skips_one_day_route():
    entry = {"files": [
        {"stage": 1, "local_path": "gpx/x/stage-1-route.gpx"},
        {"stage": None, "local_path": "gpx/x/route.gpx"},
        {"local_path": "gpx/x/route.gpx"},
    ]}
    assert [f["stage"] for f in dc.stage_files(entry)] == [1]


def test_normalize_pool_keeps_named_with_altitude_or_length():
    raw = [
        {"climb_name": "Chommle", "top": 684, "length": 3.0},
        {"climb_name": None, "top": 700, "length": 2.0},      # no name → drop
        {"climb_name": "NoTop", "top": None, "length": 1.0},  # no altitude but has
                                                              # length → KEEP (top_m=0)
        {"climb_name": "Empty", "top": None, "length": None}, # nothing usable → drop
    ]
    pool = dc.normalize_pool(raw)
    assert [p["name"] for p in pool] == ["Chommle", "NoTop"], pool
    assert pool[0]["top_m"] == 684.0
    assert pool[1]["top_m"] == 0.0 and pool[1]["length_km"] == 1.0


def test_assign_names_matches_by_length_when_pool_has_no_altitude():
    # the Tour case: pool entries carry names + lengths but top=0 → match on length
    climbs = [
        {"name": "Climb", "top_m": 828, "length_km": 11.8},   # ~Col 11.9
        {"name": "Climb", "top_m": 1364, "length_km": 8.4},   # ~Couz 8.5
        {"name": "Climb", "top_m": 1206, "length_km": 3.0},   # >2 km from any → none
    ]
    pool = [
        {"name": "Col d'Engins", "top_m": 0.0, "length_km": 11.9},
        {"name": "Col de Couz",  "top_m": 0.0, "length_km": 8.5},
    ]
    out = dc.assign_names(climbs, pool)
    assert [c["name"] for c in out] == ["Col d'Engins", "Col de Couz", "Climb"], out


def test_assign_names_altitude_match_beats_length_match():
    # a pool with both an altitude entry and a no-altitude entry: the altitude
    # match must win for the climb it fits, even if a length match also exists.
    climbs = [{"name": "Climb", "top_m": 700, "length_km": 5.0}]
    pool = [
        {"name": "ByLen", "top_m": 0.0, "length_km": 5.0},    # length match
        {"name": "ByAlt", "top_m": 695, "length_km": 9.0},    # altitude match (wins)
    ]
    assert dc.assign_names(climbs, pool)[0]["name"] == "ByAlt"


def test_assign_names_matches_by_altitude():
    climbs = [
        {"name": "Climb", "top_m": 686, "length_km": 3.1},   # ~Chommle 684
        {"name": "Climb", "top_m": 730, "length_km": 3.0},   # ~Oberarig 727
        {"name": "Climb", "top_m": 300, "length_km": 2.0},   # no pool match
    ]
    pool = [
        {"name": "Chommle", "top_m": 684, "length_km": 3.0},
        {"name": "Oberarig", "top_m": 727, "length_km": 3.2},
    ]
    out = dc.assign_names(climbs, pool)
    assert [c["name"] for c in out] == ["Chommle", "Oberarig", "Climb"], out


def test_assign_names_no_double_use_of_pool_entry():
    # two detected climbs near one pool altitude -> only the closest gets the name
    climbs = [{"name": "Climb", "top_m": 700, "length_km": 2.0},
              {"name": "Climb", "top_m": 690, "length_km": 2.0}]
    pool = [{"name": "Solo", "top_m": 695, "length_km": 2.0}]
    out = dc.assign_names(climbs, pool)
    assert sorted(c["name"] for c in out) == ["Climb", "Solo"], out


def test_assign_names_respects_tolerance():
    climbs = [{"name": "Climb", "top_m": 500, "length_km": 2.0}]
    pool = [{"name": "FarAway", "top_m": 800, "length_km": 2.0}]  # 300 m gap
    assert dc.assign_names(climbs, pool)[0]["name"] == "Climb"


def test_assign_names_empty_pool_is_noop():
    climbs = [{"name": "Climb", "top_m": 500, "length_km": 2.0}]
    assert dc.assign_names(climbs, [])[0]["name"] == "Climb"


def test_needs_processing_new_or_empty_is_processed():
    assert dc.needs_processing(None, None) is True            # never derived
    assert dc.needs_processing({}, None) is True
    assert dc.needs_processing({"stages": {}}, None) is True  # no climbs yet
    assert dc.needs_processing({"stages": {"1": []}}, None) is True


def test_needs_processing_unnamed_no_pool_is_processed():
    # derived but every climb still "Climb" and no pool cached → names pending
    payload = {"stages": {"1": [{"name": "Climb", "top_m": 500}]}}
    assert dc.needs_processing(payload, None) is True
    assert dc.needs_processing(payload, []) is True


def test_needs_processing_named_is_skipped():
    # at least one real name → naming applied → skip (no re-derive, no PCS fetch)
    payload = {"stages": {"1": [{"name": "Chommle", "top_m": 684},
                                {"name": "Climb", "top_m": 300}]}}
    assert dc.needs_processing(payload, None) is False


def test_needs_processing_unnamed_but_pool_cached_is_skipped():
    # derived, all unmatched "Climb", but the PCS pool was already fetched →
    # remaining "Climb"s will never match, so don't re-fetch forever.
    payload = {"stages": {"1": [{"name": "Climb", "top_m": 500}]}}
    pool = [{"name": "SomeCol", "top_m": 999, "length_km": 3.0}]
    assert dc.needs_processing(payload, pool) is False


def test_climbs_to_output_shape():
    detected = [{"km_start": 4.0, "km_top": 9.0, "length_km": 5.0,
                 "gain_m": 300.0, "avg_grade": 6.0, "top_m": 400.0}]
    out = dc.climbs_to_output(detected, total_km=15.0)
    assert out == [{"name": "Climb", "km_before_finish": 6.0,
                    "length_km": 5.0, "steepness": 6.0, "top_m": 400}], out


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()
