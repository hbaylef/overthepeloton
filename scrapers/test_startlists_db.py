#!/usr/bin/env python3
"""
No-network test for the startlist + cache migration to Turso (build-order
step 2, startlists). Covers the DB plumbing in scrape_races, scrape_riders,
scrape_results and geocode_birthplaces — everything against a temp local
SQLite file via db.py. Network calls (PCS/Nominatim) are injected or avoided.

Run:  python scrapers/test_startlists_db.py
"""
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.pop("TURSO_DATABASE_URL", None)
os.environ.pop("TURSO_AUTH_TOKEN", None)

import db
import scrape_races as sr
import scrape_riders as ri
import scrape_results as re_
import geocode_birthplaces as ge


# Clients opened by tests, closed in _run(). libsql's sync client runs a
# NON-DAEMON background thread — an unclosed client keeps the interpreter
# alive forever after the tests finish (observed hang, 2026-06-10).
_clients = []


def _fresh_db():
    p = Path(tempfile.mkdtemp(prefix="otp_sl_")) / "t.db"
    os.environ["OVERTHEPELOTON_DB"] = str(p)
    client = db.open_db()
    _clients.append(client)
    return client


def _tmpdir(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix))


# --------------------------------------------------------------------------- #
# scrape_races: startlist seed
# --------------------------------------------------------------------------- #
def test_seed_startlists_from_disk_is_idempotent():
    client = _fresh_db()
    d = _tmpdir("otp_sl_files_")
    (d / "giro-d-italia-2026.json").write_text(json.dumps(
        {"race_slug": "giro-d-italia-2026", "riders": [{"rider_url": "rider/a"}]}),
        encoding="utf-8")
    (d / "vuelta-a-espana-2026.json").write_text(json.dumps(
        {"race_slug": "vuelta-a-espana-2026", "riders": []}), encoding="utf-8")
    sr.STARTLISTS_DIR = d
    assert sr.seed_startlists_from_json_if_empty(client) == 2
    assert db.list_slugs(client, db.KIND_STARTLIST) == [
        "giro-d-italia-2026", "vuelta-a-espana-2026"]
    # Idempotent: table now populated -> no-op.
    assert sr.seed_startlists_from_json_if_empty(client) == 0


def test_carry_over_results_reads_prior_startlist_from_store():
    client = _fresh_db()
    db.put_document(client, db.KIND_STARTLIST, "giro-2026", {"riders": [
        {"rider_url": "rider/a", "status": "DNF", "abandoned_stage": "S3",
         "medals": [{"rank": 1, "stage": "S1"}]}]})
    fresh = [{"rider_url": "rider/a", "name": "A"}, {"rider_url": "rider/b"}]
    sr.carry_over_results(client, fresh, "giro-2026")
    assert fresh[0]["status"] == "DNF" and fresh[0]["abandoned_stage"] == "S3"
    assert fresh[0]["medals"] == [{"rank": 1, "stage": "S1"}]
    assert "status" not in fresh[1]  # no prior entry -> untouched


# --------------------------------------------------------------------------- #
# scrape_riders: collect, embed, cache
# --------------------------------------------------------------------------- #
def test_collect_rider_urls_from_store():
    client = _fresh_db()
    db.put_document(client, db.KIND_STARTLIST, "a-2026",
                    {"riders": [{"rider_url": "rider/x"}, {"rider_url": "rider/y"}]})
    db.put_document(client, db.KIND_STARTLIST, "b-2026",
                    {"riders": [{"rider_url": "rider/y"}, {"rider_url": None}]})
    assert ri.collect_rider_urls(client) == {"rider/x", "rider/y"}


def test_embed_specialties_round_trip():
    client = _fresh_db()
    db.put_document(client, db.KIND_STARTLIST, "a-2026",
                    {"riders": [{"rider_url": "rider/x"}, {"rider_url": "rider/z"}]})
    cache_riders = {"rider/x": {"career": {"gc": 100}, "birthdate": "1998-01-01",
                                "place_of_birth": "Komenda"}}
    ri.embed_specialties_into_startlists(client, cache_riders)
    out = db.get_document(client, db.KIND_STARTLIST, "a-2026")["riders"]
    assert out[0]["specialties"] == {"career": {"gc": 100}}
    assert out[0]["birthdate"] == "1998-01-01" and out[0]["place_of_birth"] == "Komenda"
    assert out[1]["specialties"] == {"career": None}  # no cache entry


def test_riders_cache_seed_and_save():
    client = _fresh_db()
    legacy = _tmpdir("otp_rc_") / "riders_cache.json"
    legacy.write_text(json.dumps({"updated_at": "x", "total_cached": 1,
                                  "riders": {"rider/x": {"career": None}}}),
                      encoding="utf-8")
    ri.CACHE_FILE = legacy
    cache = ri.load_cache(client)                 # seeds from legacy file
    assert "rider/x" in cache["riders"]
    cache["riders"]["rider/y"] = {"career": {"gc": 5}}
    ri.save_cache(client, cache)
    again = db.get_cache(client, db.CACHE_RIDERS)  # persisted to the store
    assert set(again["riders"]) == {"rider/x", "rider/y"}
    assert again["total_cached"] == 2


# --------------------------------------------------------------------------- #
# scrape_results: process_race against the store (injected fetch)
# --------------------------------------------------------------------------- #
def test_process_race_writes_medals_and_abandons_to_store():
    client = _fresh_db()
    db.put_document(client, db.KIND_STARTLIST, "giro-2026", {"race_slug": "giro-2026",
        "riders": [{"rider_url": "rider/x"}, {"rider_url": "rider/y"}]})
    race = {"slug": "giro-2026", "year": 2026,
            "stages": [{"stage_url": "race/giro/2026/stage-1", "date": "2026-05-01"}]}

    def fake_fetch(_url):
        return {"rider/x": {"rank": 1, "status": "DF"},
                "rider/y": {"rank": None, "status": "DNF"}}

    res = re_.process_race(client, race, date(2026, 6, 9), fetch=fake_fetch)
    assert res == (1, 1)
    out = {r["rider_url"]: r for r in
           db.get_document(client, db.KIND_STARTLIST, "giro-2026")["riders"]}
    assert out["rider/x"]["medals"] == [{"rank": 1, "stage": "S1"}]
    assert out["rider/y"]["status"] == "DNF" and out["rider/y"]["abandoned_stage"] == "S1"


def test_process_race_no_startlist_returns_none():
    client = _fresh_db()
    race = {"slug": "missing-2026", "year": 2026, "stages": []}
    assert re_.process_race(client, race, date(2026, 6, 9), fetch=lambda u: None) is None


# --------------------------------------------------------------------------- #
# geocode: cache seed + save
# --------------------------------------------------------------------------- #
def test_birthplaces_cache_seed_and_save():
    client = _fresh_db()
    legacy = _tmpdir("otp_bp_") / "birthplaces_cache.json"
    legacy.write_text(json.dumps({"komenda|si": {"lat": 46.2, "lon": 14.5}}),
                      encoding="utf-8")
    ge.CACHE_FILE = legacy
    cache = ge.load_cache(client)                 # seeds from legacy file
    assert cache["komenda|si"]["lat"] == 46.2
    cache["bilbao|es"] = {"lat": 43.2, "lon": -2.9}
    ge.save_cache(client, cache)
    again = db.get_cache(client, db.CACHE_BIRTHPLACES)
    assert set(again) == {"komenda|si", "bilbao|es"}


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
