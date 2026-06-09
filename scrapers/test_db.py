#!/usr/bin/env python3
"""
No-network test for db.py.

Runs entirely against a temporary local SQLite FILE (the same code path that
talks to Turso in Actions, just a different URL). Proves schema init, the
change-aware writers (write-only-if-changed), and the GPX gate used by the
over-scraping fix. Run:  python scrapers/test_db.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _fresh_client():
    """A db client backed by a brand-new temp SQLite file."""
    import db  # imported after OVERTHEPELOTON_DB is set

    return db.open_db()


# Each test gets its own temp DB via this module-level fixture set in _run().
db = None  # set in _run()
client = None  # set in _run()


def test_switches_to_local_file_without_env():
    assert not db.is_remote()
    url, auth = db._resolve_url()
    assert url.startswith("file:")
    assert auth is None


def test_libsql_url_coerced_to_https(monkey):
    monkey("TURSO_DATABASE_URL", "libsql://example-org.turso.io")
    monkey("TURSO_AUTH_TOKEN", "tok")
    try:
        assert db.is_remote()
        url, auth = db._resolve_url()
        assert url == "https://example-org.turso.io"
        assert auth == "tok"
    finally:
        monkey("TURSO_DATABASE_URL", None)
        monkey("TURSO_AUTH_TOKEN", None)


def test_put_document_is_change_aware():
    obj = {"name": "Tour de France", "stages": [1, 2, 3]}
    assert db.put_document(client, "race", "tour-de-france-2026", obj) is True
    # Same content (even reordered keys) -> no write.
    assert db.put_document(client, "race", "tour-de-france-2026",
                           {"stages": [1, 2, 3], "name": "Tour de France"}) is False
    # Changed content -> write.
    obj["stages"].append(4)
    assert db.put_document(client, "race", "tour-de-france-2026", obj) is True
    assert db.get_document(client, "race", "tour-de-france-2026") == obj


def test_get_document_missing_returns_none():
    assert db.get_document(client, "race", "does-not-exist") is None


def test_list_and_get_all_documents():
    db.put_document(client, "startlist", "giro-2026", {"riders": []})
    db.put_document(client, "startlist", "vuelta-2026", {"riders": []})
    assert db.list_slugs(client, "startlist") == ["giro-2026", "vuelta-2026"]
    alld = db.get_all_documents(client, "startlist")
    assert set(alld) == {"giro-2026", "vuelta-2026"}
    assert alld["giro-2026"] == {"riders": []}


def test_cache_round_trip_and_default():
    assert db.get_cache(client, "riders", default="MISSING") == "MISSING"
    payload = {"updated_at": "2026-06-09", "riders": {"pogacar": 1}}
    assert db.put_cache(client, "riders", payload) is True
    assert db.put_cache(client, "riders", dict(payload)) is False  # unchanged
    assert db.get_cache(client, "riders") == payload


def test_gpx_gate_and_round_trip():
    slug = "il-lombardia-2026"
    assert db.has_gpx(client, slug) is False
    xml = "<gpx><trk>...</trk></gpx>"
    assert db.put_gpx(client, slug, "route.gpx", xml, stage=1,
                      source="cyclingstage", url="http://x/route.gpx") is True
    assert db.has_gpx(client, slug) is True
    assert db.put_gpx(client, slug, "route.gpx", xml, stage=1) is False  # unchanged
    assert db.get_gpx(client, slug, "route.gpx") == xml
    meta = db.list_gpx(client, slug)
    assert meta == [{"filename": "route.gpx", "stage": 1,
                     "source": "cyclingstage", "url": "http://x/route.gpx"}]
    assert db.gpx_slugs(client) == [slug]


def _run():
    global db, client

    # Point db.py at a throwaway SQLite file BEFORE importing it.
    tmpdir = tempfile.mkdtemp(prefix="otp_db_test_")
    os.environ.pop("TURSO_DATABASE_URL", None)
    os.environ.pop("TURSO_AUTH_TOKEN", None)
    os.environ["OVERTHEPELOTON_DB"] = str(Path(tmpdir) / "test.db")

    import db as _db
    db = _db
    client = db.open_db()

    def monkey(key, value):
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        # Inject the monkey helper for the one test that needs env twiddling.
        if t.__code__.co_argcount == 1:
            t(monkey)
        else:
            t()
        print(f"  [ok] {t.__name__}")
        passed += 1

    client.close()
    print(f"\n{passed}/{len(tests)} passed")


if __name__ == "__main__":
    _run()
