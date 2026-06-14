#!/usr/bin/env python3
"""
No-network test for scrape_races.py's Turso-backed race store (build-order
step 2, races). PCS is unreachable here, so we test the pure store logic that
WILL run in Actions: the one-time seed, the freeze index, and the freeze
boundary. Everything runs against a temp local SQLite file via db.py.

Run:  python scrapers/test_scrape_races_db.py
"""
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# db.py switches to a local file when TURSO_DATABASE_URL is absent.
os.environ.pop("TURSO_DATABASE_URL", None)
os.environ.pop("TURSO_AUTH_TOKEN", None)

import db
import scrape_races as sr


# Clients opened by tests, closed in _run(). libsql's sync client runs a
# NON-DAEMON background thread — an unclosed client keeps the interpreter
# alive forever after the tests finish (observed hang, 2026-06-10).
_clients = []


def _fresh_db():
    """A db client on a brand-new temp SQLite file (per-test isolation)."""
    p = Path(tempfile.mkdtemp(prefix="otp_races_")) / "t.db"
    os.environ["OVERTHEPELOTON_DB"] = str(p)
    client = db.open_db()
    _clients.append(client)
    return client


def _legacy_races_file(races):
    """Write a temp legacy races.json and point sr.RACES_FILE at it."""
    p = Path(tempfile.mkdtemp(prefix="otp_legacy_")) / "races.json"
    p.write_text(json.dumps({"year": 2026, "races": races}), encoding="utf-8")
    sr.RACES_FILE = p
    return p


def test_is_mens_race_filters_women():
    # Explicit women's category → dropped.
    assert sr.is_mens_race({"category": "Women Elite"}) is False
    # Men's / unknown / missing → kept (men-only discovery, don't drop legit races).
    assert sr.is_mens_race({"category": "Men Elite"}) is True
    assert sr.is_mens_race({"category": None}) is True
    assert sr.is_mens_race({}) is True


def test_refresh_startlists_only_reapplies_cache_and_skips_finished():
    import geocode_birthplaces as geo
    client = _fresh_db()
    # One upcoming race (re-scraped) and one finished race (skipped).
    db.put_document(client, sr.DB_RACE_KIND, "up-2026", {
        "slug": "up-2026", "name": "Up Race",
        "pcs_url": "race/up/2026", "enddate": "2099-12-31"})
    db.put_document(client, sr.DB_RACE_KIND, "old-2026", {
        "slug": "old-2026", "name": "Old Race",
        "pcs_url": "race/old/2026", "enddate": "2020-01-01"})
    # Cached enrichment: rider specialties + birthplace coords (no network).
    db.put_cache(client, db.CACHE_RIDERS, {"riders": {
        "rider/a": {"career": {"gc": 100}, "birthdate": "1995-01-01",
                    "place_of_birth": "Townville"}}})
    db.put_cache(client, db.CACHE_BIRTHPLACES, {
        geo.cache_key("Townville", "FR"): {"lat": 1.5, "lon": 2.5}})

    calls = []
    def fake_sl(url):
        calls.append(url)
        return [{"name": "A", "rider_url": "rider/a", "nationality": "FR", "team": "T"}]
    orig_sl, orig_sleep = sr.scrape_startlist, sr.time.sleep
    sr.scrape_startlist, sr.time.sleep = fake_sl, (lambda *a, **k: None)
    try:
        n = sr.refresh_startlists_only(client)
    finally:
        sr.scrape_startlist, sr.time.sleep = orig_sl, orig_sleep

    assert n == 1                          # only the upcoming race
    assert calls == ["race/up/2026"]       # finished race skipped (no scrape)
    r = db.get_document(client, db.KIND_STARTLIST, "up-2026")["riders"][0]
    assert r["specialties"]["career"] == {"gc": 100}      # cached specialties applied
    assert r["birthdate"] == "1995-01-01" and r["place_of_birth"] == "Townville"
    assert r["birthplace_lat"] == 1.5 and r["birthplace_lon"] == 2.5  # cached coords
    # The finished race never got a startlist written.
    assert db.get_document(client, db.KIND_STARTLIST, "old-2026") is None


def test_seed_imports_legacy_and_is_idempotent():
    client = _fresh_db()
    _legacy_races_file([
        {"slug": "giro-d-italia-2026", "cyclingstage_slug": "giro"},
        {"slug": "tour-de-france-2026", "cyclingstage_slug": "tour-de-france"},
    ])
    assert sr.seed_races_from_json_if_empty(client) == 2
    assert db.list_slugs(client, sr.DB_RACE_KIND) == [
        "giro-d-italia-2026", "tour-de-france-2026"]
    # Second call is a no-op: the table is already populated.
    assert sr.seed_races_from_json_if_empty(client) == 0


def test_seed_skips_when_table_already_has_rows():
    client = _fresh_db()
    db.put_document(client, sr.DB_RACE_KIND, "vuelta-a-espana-2026",
                    {"slug": "vuelta-a-espana-2026", "cyclingstage_slug": "vuelta"})
    _legacy_races_file([{"slug": "giro-d-italia-2026", "cyclingstage_slug": "giro"}])
    assert sr.seed_races_from_json_if_empty(client) == 0  # not empty -> skip
    assert db.list_slugs(client, sr.DB_RACE_KIND) == ["vuelta-a-espana-2026"]


def test_seed_no_legacy_file_is_safe():
    client = _fresh_db()
    sr.RACES_FILE = Path(tempfile.mkdtemp(prefix="otp_none_")) / "missing.json"
    assert sr.seed_races_from_json_if_empty(client) == 0
    assert db.list_slugs(client, sr.DB_RACE_KIND) == []


def test_load_existing_indexes_by_cyclingstage_slug():
    client = _fresh_db()
    db.put_document(client, sr.DB_RACE_KIND, "tour-de-france-2026",
                    {"slug": "tour-de-france-2026", "cyclingstage_slug": "tour-de-france"})
    db.put_document(client, sr.DB_RACE_KIND, "il-lombardia-2026",
                    {"slug": "il-lombardia-2026", "cyclingstage_slug": "tour-of-lombardy"})
    idx = sr.load_existing_races_by_cs(client)
    assert set(idx) == {"tour-de-france", "tour-of-lombardy"}
    assert idx["tour-de-france"]["slug"] == "tour-de-france-2026"


def test_is_finished_freeze_boundary():
    today = date(2026, 6, 9)
    # FREEZE_GRACE_DAYS == 2: finished only when enddate < today - 2 days.
    assert sr.is_finished({"enddate": "2026-06-06"}, today) is True   # 3 days ago
    assert sr.is_finished({"enddate": "2026-06-07"}, today) is False  # 2 days ago (grace)
    assert sr.is_finished({"enddate": "2026-06-09"}, today) is False  # today
    assert sr.is_finished({"enddate": "2026-07-01"}, today) is False  # future
    assert sr.is_finished({"enddate": None}, today) is False
    assert sr.is_finished({}, today) is False


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
