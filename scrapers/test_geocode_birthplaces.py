#!/usr/bin/env python3
"""No-network tests for geocode_birthplaces.py pure helpers.
Run: python scrapers/test_geocode_birthplaces.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import geocode_birthplaces as g


def test_cache_key_normalizes():
    assert g.cache_key("Roskilde", "DK") == "roskilde|dk"
    assert g.cache_key("  Bilbao ", "es") == "bilbao|es"
    assert g.cache_key("Nowhere", None) == "nowhere|"


def test_parse_nominatim_first_result():
    payload = [{"lat": "55.6415", "lon": "12.0803"}, {"lat": "1", "lon": "2"}]
    lat, lon = g.parse_nominatim(payload)
    assert abs(lat - 55.6415) < 1e-6 and abs(lon - 12.0803) < 1e-6


def test_parse_nominatim_empty_or_bad():
    assert g.parse_nominatim([]) == (None, None)
    assert g.parse_nominatim({}) == (None, None)
    assert g.parse_nominatim([{"lat": "x"}]) == (None, None)


def test_needs_coords():
    assert g.needs_coords({"place_of_birth": "Roskilde"}) is True
    assert g.needs_coords({"place_of_birth": "Roskilde", "birthplace_lat": 55.6}) is False
    assert g.needs_coords({"place_of_birth": None}) is False
    assert g.needs_coords({}) is False


def test_has_coords_only_true_for_real_coordinates():
    # a resolved town is permanent (skip forever)
    assert g.has_coords({"lat": 55.6, "lon": 12.1}) is True
    # a past failed lookup (None) has no coords → still genuinely missing → fetch
    assert g.has_coords({"lat": None, "lon": None}) is False
    assert g.has_coords({"lat": 55.6, "lon": None}) is False
    assert g.has_coords(None) is False
    assert g.has_coords({}) is False


def test_plan_geocode_splits_resolved_and_missing():
    startlists = {
        "race-a": {"riders": [
            {"place_of_birth": "Bilbao", "nationality": "ES"},   # resolved
            {"place_of_birth": "Bilbao", "nationality": "ES"},   # dup town → one key
            {"place_of_birth": "Komenda", "nationality": "SI"},  # missing
            {"place_of_birth": None},                            # no town → ignored
            {"nationality": "FR"},                               # no town → ignored
        ]},
        "race-b": {"riders": [
            {"place_of_birth": "Oslo", "nationality": "NO"},     # failed-before (None)
        ]},
    }
    cache = {
        "bilbao|es": {"lat": 43.26, "lon": -2.93},   # resolved
        "oslo|no": {"lat": None, "lon": None},       # previously failed → re-fetch
    }
    to_fetch, resolved = g.plan_geocode(startlists, cache)
    assert resolved == {"bilbao|es"}, resolved
    assert to_fetch == {"komenda|si", "oslo|no"}, to_fetch


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t(); print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1; print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()
