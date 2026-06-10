#!/usr/bin/env python3
"""No-network tests for scrape_riders.py skip/preserve logic.

Covers the idempotency rules: re-fetch ONLY for evolving career points (7-day
staleness), never just to backfill an immutable birth field; and never clobber a
stored birthdate/place_of_birth when a flaky re-fetch loses it.
Run: python scrapers/test_scrape_riders.py"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_riders as sr


def _ago(days):
    return (datetime.now() - timedelta(days=days)).isoformat()


def test_needs_refetch_missing_entry():
    assert sr.needs_refetch(None) is True
    assert sr.needs_refetch({}) is True          # no _scraped_at → stale


def test_needs_refetch_respects_staleness():
    assert sr.needs_refetch({"_scraped_at": _ago(1)}) is False    # fresh → skip
    assert sr.needs_refetch({"_scraped_at": _ago(8)}) is True     # >7d → refetch


def test_needs_refetch_ignores_missing_birth_fields():
    # a FRESH entry that happens to lack birthdate must NOT trigger a network call
    # just to backfill an immutable field — it fills in on the next career refresh.
    fresh_no_birth = {"career": {"gc": 1}, "_scraped_at": _ago(1)}
    assert "birthdate" not in fresh_no_birth
    assert sr.needs_refetch(fresh_no_birth) is False


def test_merge_preserving_birth_keeps_old_when_refetch_empty():
    existing = {"career": {"gc": 10}, "birthdate": "1998-09-21",
                "place_of_birth": "Komenda"}
    new = {"career": {"gc": 25}, "birthdate": None, "place_of_birth": None}
    merged = sr.merge_preserving_birth(new, existing)
    assert merged["career"] == {"gc": 25}              # evolving → takes fresh value
    assert merged["birthdate"] == "1998-09-21"         # immutable → preserved
    assert merged["place_of_birth"] == "Komenda"


def test_merge_preserving_birth_takes_new_when_present():
    existing = {"birthdate": None, "place_of_birth": None}
    new = {"career": None, "birthdate": "2000-01-02", "place_of_birth": "Nice"}
    merged = sr.merge_preserving_birth(new, existing)
    assert merged["birthdate"] == "2000-01-02"
    assert merged["place_of_birth"] == "Nice"


def test_merge_preserving_birth_no_existing_is_passthrough():
    new = {"career": None, "birthdate": None, "place_of_birth": None}
    assert sr.merge_preserving_birth(new, None) == new


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
