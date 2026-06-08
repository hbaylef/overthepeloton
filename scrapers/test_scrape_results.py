#!/usr/bin/env python3
"""
No-network logic test for scrape_results.py.

PCS is unreachable from this machine (TLS-intercepting proxy), so we inject a
fake fetcher and assert the pure abandon-derivation logic that WILL run in
GitHub Actions. Run:  python scrapers/test_scrape_results.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_results as sr


def test_stage_label():
    assert sr.stage_label("race/tour-de-france/2026/stage-5") == "S5"
    assert sr.stage_label("race/tour-de-france/2026/stage-12/") == "S12"
    assert sr.stage_label("race/tour-down-under/2026/prologue") == "P"


def test_parse_date():
    assert sr.parse_date("07-05", 2026) == date(2026, 7, 5)
    assert sr.parse_date("2026-07-05", 2026) == date(2026, 7, 5)
    assert sr.parse_date(None, 2026) is None
    assert sr.parse_date("not-a-date", 2026) is None


def _row(rank, status="DF"):
    return {"rank": rank, "status": status}


def test_compute_abandons_last_appearance_wins():
    # Rider A finishes S1+S2 then DNFs S3; B finishes throughout; C DNS on S1.
    scanned = [
        ("S1", {"rider/a": _row(5), "rider/b": _row(2), "rider/c": _row(None, "DNS")}),
        ("S2", {"rider/a": _row(8), "rider/b": _row(1)}),
        ("S3", {"rider/a": _row(None, "DNF"), "rider/b": _row(3)}),
    ]
    ab = sr.compute_abandons(scanned)
    assert ab == {"rider/a": ("DNF", "S3"), "rider/c": ("DNS", "S1")}, ab


def test_compute_abandons_ignores_recovered_status():
    # A "DF" in a later stage clears an earlier non-DF blip (defensive).
    scanned = [("S1", {"rider/a": _row(None, "OTL")}), ("S2", {"rider/a": _row(10)})]
    assert sr.compute_abandons(scanned) == {}


def test_compute_medals_collects_podiums():
    # B wins two stages + a 2nd, A takes a win + a 3rd, C only a 2nd, D nothing.
    scanned = [
        ("S1", {"rider/a": _row(1), "rider/b": _row(2), "rider/c": _row(2), "rider/d": _row(4)}),
        ("S2", {"rider/a": _row(3), "rider/b": _row(1), "rider/c": _row(7)}),
        ("S3", {"rider/b": _row(1), "rider/a": _row(9)}),
    ]
    med = sr.compute_medals(scanned)
    # best rank first; stage order preserved within a rank
    assert med["rider/a"] == [{"rank": 1, "stage": "S1"}, {"rank": 3, "stage": "S2"}]
    assert med["rider/b"] == [{"rank": 1, "stage": "S2"}, {"rank": 1, "stage": "S3"},
                              {"rank": 2, "stage": "S1"}]
    assert med["rider/c"] == [{"rank": 2, "stage": "S1"}]
    assert "rider/d" not in med


def test_apply_results_sets_and_clears():
    riders = [
        {"rider_url": "rider/a", "name": "A"},
        # B is stale: previously abandoned + medalled, now neither → both cleared
        {"rider_url": "rider/b", "name": "B", "status": "DNF",
         "abandoned_stage": "S1", "medals": [{"rank": 1, "stage": "S1"}]},
    ]
    abandons = {"rider/a": ("DNF", "S3")}
    medals = {"rider/a": [{"rank": 1, "stage": "S2"}, {"rank": 1, "stage": "S5"}]}
    n_ab, n_med = sr.apply_results(riders, abandons, medals)
    assert (n_ab, n_med) == (1, 1)
    assert riders[0]["status"] == "DNF" and riders[0]["abandoned_stage"] == "S3"
    assert riders[0]["medals"] == [{"rank": 1, "stage": "S2"}, {"rank": 1, "stage": "S5"}]
    assert "status" not in riders[1] and "abandoned_stage" not in riders[1]
    assert "medals" not in riders[1]


def test_scan_stages_skips_future_and_unstarted():
    today = date(2026, 7, 6)
    race = {
        "year": 2026,
        "stages": [
            {"stage_url": "race/x/2026/stage-1", "date": "07-05"},   # past → scanned
            {"stage_url": "race/x/2026/stage-2", "date": "07-06"},   # today → scanned
            {"stage_url": "race/x/2026/stage-3", "date": "07-07"},   # future → skipped
            {"stage_url": None, "date": "07-05"},                    # no url → skipped
        ],
    }
    seen = []

    def fake_fetch(url):
        seen.append(url)
        return {"rider/a": _row(1)}

    # avoid the politeness sleep in tests
    sr.DELAY_BETWEEN_REQUESTS = 0
    scanned = sr.scan_stages(race, today, fetch=fake_fetch)
    assert seen == ["race/x/2026/stage-1", "race/x/2026/stage-2"], seen
    assert [lbl for lbl, _ in scanned] == ["S1", "S2"], scanned


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  [ok] {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")


if __name__ == "__main__":
    _run()
