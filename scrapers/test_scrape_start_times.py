#!/usr/bin/env python3
"""
No-network logic test for scrape_start_times.py.

PCS is unreachable from this machine, so we inject a fake fetcher and assert the
pure parse / cache / annotate logic that WILL run in GitHub Actions.
Run:  python scrapers/test_scrape_start_times.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_start_times as st


def test_parse_start_time_variants():
    assert st.parse_start_time("17:00 (17:00 CET)") == "17:00"
    assert st.parse_start_time("9:05 (10:05 CEST)") == "09:05"
    assert st.parse_start_time("13:15") == "13:15"
    assert st.parse_start_time("") is None
    assert st.parse_start_time(None) is None
    assert st.parse_start_time("TBD") is None
    assert st.parse_start_time("99:99 weird") is None


def test_cached_time_fresh_stale_empty():
    now = datetime.now().isoformat()
    old = (datetime.now() - timedelta(days=40)).isoformat()
    cache = {"urls": {
        "fresh": {"start_time": "14:00", "_scraped_at": now},
        "stale": {"start_time": "14:00", "_scraped_at": old},
        "empty": {"start_time": None, "_scraped_at": now},
    }}
    assert st.cached_time(cache, "fresh") == "14:00"   # fresh+nonempty → hit
    assert st.cached_time(cache, "stale") is None       # stale → refetch
    assert st.cached_time(cache, "empty") is None       # empty → always retry
    assert st.cached_time(cache, "missing") is None


def test_get_start_time_records_and_reuses():
    st.DELAY_BETWEEN_REQUESTS = 0
    cache = {"urls": {}}
    calls = []

    def fake(url):
        calls.append(url)
        return "15:30"

    assert st.get_start_time(cache, "u1", fake) == "15:30"
    assert cache["urls"]["u1"]["start_time"] == "15:30"
    # second call served from cache, no extra fetch
    assert st.get_start_time(cache, "u1", fake) == "15:30"
    assert calls == ["u1"]


def test_get_start_time_caches_misses_and_retries():
    st.DELAY_BETWEEN_REQUESTS = 0
    cache = {"urls": {}}
    calls = []

    def fake(url):
        calls.append(url)
        return None             # not published yet

    assert st.get_start_time(cache, "u2", fake) is None
    assert cache["urls"]["u2"]["start_time"] is None
    # a cached miss is retried (not served from cache)
    assert st.get_start_time(cache, "u2", fake) is None
    assert calls == ["u2", "u2"]


def test_in_weather_window():
    today = "2026-06-06"
    assert st.in_weather_window("2026-01-20", today)        # past → include
    assert st.in_weather_window("2026-06-06", today)        # today
    assert st.in_weather_window("2026-06-20", today)        # within 18 d
    assert not st.in_weather_window("2026-07-04", today)    # far future
    assert st.in_weather_window(None, today)                # unknown → include
    assert st.in_weather_window("garbage", today)           # unparseable → include


def test_entry_date_stage_and_one_day():
    race = {"year": 2026, "startdate": "2026-02-28"}
    assert st.entry_date(race, {"date": "03-22"}) == "2026-03-22"
    assert st.entry_date(race, None) == "2026-02-28"


def test_annotate_stage_race_and_default_fallback():
    st.DELAY_BETWEEN_REQUESTS = 0
    st.LOOKAHEAD_DAYS = 100000        # disable the window gate for this test
    cache = {"urls": {}}

    def fake(url):
        return "13:15" if "stage-1" in url else None   # stage-2 has no time yet

    races = [{
        "is_one_day_race": False, "year": 2026, "startdate": "2026-01-20",
        "stages": [
            {"stage_url": "race/x/2026/stage-1", "date": "01-20"},
            {"stage_url": "race/x/2026/stage-2", "date": "01-21"},
        ],
    }]
    scraped = st.annotate_start_times(races, cache, fake)
    s = races[0]["stages"]
    assert s[0]["start_time"] == "13:15" and s[0]["start_time_source"] == "pcs"
    assert s[1]["start_time"] == st.DEFAULT_START and s[1]["start_time_source"] == "default"
    assert scraped == 1


def test_annotate_skips_far_future():
    st.DELAY_BETWEEN_REQUESTS = 0
    st.LOOKAHEAD_DAYS = 18
    cache = {"urls": {}}
    calls = []

    far = (datetime.now() + timedelta(days=60)).date().isoformat()
    races = [{"is_one_day_race": True, "pcs_url": "race/future/2026",
              "startdate": far, "stages": []}]
    st.annotate_start_times(races, cache, lambda u: calls.append(u) or "10:00")
    assert races[0]["start_time"] is None
    assert races[0]["start_time_source"] == "pending"
    assert calls == []                # never fetched


def test_annotate_one_day_race():
    st.DELAY_BETWEEN_REQUESTS = 0
    st.LOOKAHEAD_DAYS = 100000
    cache = {"urls": {}}
    races = [{"is_one_day_race": True, "pcs_url": "race/omloop/2026",
              "startdate": "2026-02-28", "stages": []}]
    st.annotate_start_times(races, cache, lambda u: "11:10")   # fetch returns parsed HH:MM
    assert races[0]["start_time"] == "11:10"
    assert races[0]["start_time_source"] == "pcs"


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
