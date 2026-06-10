#!/usr/bin/env python3
"""
No-network logic test for scrape_climbs.py.

PCS is unreachable from this machine (TLS-intercepting proxy), so we inject a
fake fetcher and assert the pure transform + cache + per-race assembly logic
that WILL run in GitHub Actions. Run:  python scrapers/test_scrape_climbs.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_climbs as sc

# A realistic RaceClimbs.climbs() row (note PCS's "finnish" spelling + raw names).
PCS_ROW = {
    "climb_name": "Côte de La Redoute",
    "climb_url": "location/cote-de-la-redoute",
    "length": 2.0,
    "steepness": 8.4,
    "top": 290,
    "km_before_finnish": 34,
}


def test_normalize_renames_and_units():
    c = sc.normalize_climb(PCS_ROW)
    assert c == {
        "name": "Côte de La Redoute",
        "climb_url": "location/cote-de-la-redoute",
        "km_before_finish": 34,
        "length_km": 2.0,
        "steepness": 8.4,
        "top_m": 290,
    }, c


def test_normalize_drops_rows_without_placement():
    rows = [PCS_ROW, {**PCS_ROW, "km_before_finnish": None}]
    out = sc.normalize_climbs(rows)
    assert len(out) == 1, out


def test_climbs_url():
    assert sc.climbs_url("race/il-lombardia/2026") == "race/il-lombardia/2026/route/climbs"
    assert sc.climbs_url("race/tour-de-france/2026/stage-5/") == \
        "race/tour-de-france/2026/stage-5/route/climbs"


def test_one_day_assembly():
    cache = {"urls": {}}
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return sc.normalize_climbs([PCS_ROW])

    race = {"name": "Il Lombardia", "slug": "il-lombardia-2026",
            "pcs_url": "race/il-lombardia/2026", "is_one_day_race": True}
    payload = sc.build_race_entry(race, cache, fake_fetch)
    assert payload["is_one_day_race"] is True
    assert payload["climbs"][0]["name"] == "Côte de La Redoute"
    assert "stages" not in payload
    assert calls == ["race/il-lombardia/2026/route/climbs"]
    assert sc.count_climbs(payload) == 1


def test_stage_assembly_keys_by_stage_and_drops_empty():
    cache = {"urls": {}}

    def fake_fetch(url):
        # only stage-2 has climbs; stage-1 is a flat sprint stage
        return sc.normalize_climbs([PCS_ROW]) if "stage-2" in url else []

    race = {
        "name": "Tour de France", "slug": "tour-de-france-2026",
        "is_one_day_race": False,
        "stages": [
            {"stage_url": "race/tour-de-france/2026/stage-1"},
            {"stage_url": "race/tour-de-france/2026/stage-2"},
        ],
    }
    payload = sc.build_race_entry(race, cache, fake_fetch)
    assert "climbs" not in payload
    assert list(payload["stages"].keys()) == ["2"], payload["stages"]
    assert sc.count_climbs(payload) == 1


def test_cache_is_permanent_for_nonempty_and_retries_empty():
    now = datetime.now().isoformat()
    old = (datetime.now() - timedelta(days=400)).isoformat()
    cache = {"urls": {
        "fresh/route/climbs": {"climbs": [sc.normalize_climb(PCS_ROW)], "_scraped_at": now},
        "old/route/climbs":   {"climbs": [sc.normalize_climb(PCS_ROW)], "_scraped_at": old},
        "empty/route/climbs": {"climbs": [], "_scraped_at": now},
    }}
    assert sc.cached_climbs(cache, "fresh/route/climbs")           # non-empty → hit
    assert sc.cached_climbs(cache, "old/route/climbs")             # non-empty, OLD → still hit (permanent)
    assert sc.cached_climbs(cache, "empty/route/climbs") is None   # empty → always retry

    # get_climbs should fetch on miss and record the result
    fetched = []
    def fake_fetch(url):
        fetched.append(url)
        return sc.normalize_climbs([PCS_ROW])
    out = sc.get_climbs(cache, "empty/route/climbs", fake_fetch)
    assert len(out) == 1 and fetched == ["empty/route/climbs"]


def test_has_stored_climbs():
    # populated → skip (True)
    assert sc.has_stored_climbs({"climbs": [{"name": "X"}]}) is True
    assert sc.has_stored_climbs({"stages": {"5": [{"name": "X"}]}}) is True
    # not yet populated → keep retrying (False)
    assert sc.has_stored_climbs({"climbs": []}) is False
    assert sc.has_stored_climbs({"stages": {}}) is False
    assert sc.has_stored_climbs({"stages": {"5": []}}) is False
    assert sc.has_stored_climbs(None) is False
    assert sc.has_stored_climbs({}) is False


def test_failed_fetch_records_empty_not_none():
    cache = {"urls": {}}
    out = sc.get_climbs(cache, "boom/route/climbs", lambda u: None)
    assert out == []
    assert cache["urls"]["boom/route/climbs"]["climbs"] == []


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    # don't sleep between fake fetches
    sc.DELAY_BETWEEN_REQUESTS = 0
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
