#!/usr/bin/env python3
"""Database helper for overthepeloton's Turso re-architecture.

Connection is switched by the presence of TURSO_DATABASE_URL:
  - remote (GitHub Actions): TURSO_DATABASE_URL (libsql://...) + TURSO_AUTH_TOKEN
  - local  (dev/testing):    no env vars -> a local SQLite FILE
Same code both ways, so logic can be tested locally without the TLS proxy or
network. See the project_turso_rearchitecture memory for the why.

Schema is deliberately pragmatic (not over-normalized):
  - race_data : per-race JSON-blob rows, keyed by (kind, slug)
                kind in {race, startlist, climbs, predictions, cobbles, ...}
  - caches    : singleton scrape caches, keyed by name
                (riders, climbs, climbs_names, birthplaces, start_times, ...)
  - gpx_files : one row per stage GPX, keyed by (slug, filename)

Every writer is change-aware: it hashes the canonical JSON/text and skips the
write when the stored hash already matches, so the published slices barely churn.
"""

import datetime
import hashlib
import json
import os

import libsql_client

# Local SQLite file used when no TURSO_DATABASE_URL is set. Override with
# OVERTHEPELOTON_DB (handy for tests).
DEFAULT_LOCAL_PATH = "data/overthepeloton.db"


def _load_local_dotenv():
    """Local-dev convenience: if a project-root .env exists, load its KEY=VALUE
    lines into os.environ WITHOUT overriding already-set vars. Lets you run a
    scraper/scorer from the IDE or a plain terminal and still reach Turso.

    No-op in CI: there's no .env in the repo (it's gitignored), and the real
    secrets are already in the environment (setdefault never clobbers them)."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def _relax_tls_strict():
    """LOCAL ONLY (gated by OVERTHEPELOTON_INSECURE_TLS=1 in .env). This dev
    machine's TLS-intercepting proxy presents a CA cert that Python 3.14's
    strict X509 verification rejects ("Basic Constraints of CA cert not marked
    critical"), which blocks libsql/aiohttp from reaching Turso. Drop JUST the
    VERIFY_X509_STRICT flag (normal verification + the Windows trust store still
    apply) on every default SSL context the process builds. NEVER set the env
    var in CI — Actions has clean egress and full strict verification."""
    import ssl
    _orig = ssl.create_default_context

    def _ctx(*args, **kwargs):
        ctx = _orig(*args, **kwargs)
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return ctx

    ssl.create_default_context = _ctx

    # aiohttp (libsql's HTTP backend) builds its SSL contexts at IMPORT time —
    # already done before this runs — so also clear the flag on the existing
    # module-level context the connector actually uses.
    try:
        from aiohttp import connector as _aioc
        for _name in ("_SSL_CONTEXT_VERIFIED",):
            _c = getattr(_aioc, _name, None)
            if _c is not None:
                _c.verify_flags &= ~ssl.VERIFY_X509_STRICT
    except Exception:
        pass


_load_local_dotenv()
if os.environ.get("OVERTHEPELOTON_INSECURE_TLS"):
    _relax_tls_strict()

# race_data "kind" values — the contract shared across scrapers.
KIND_RACE = "race"
KIND_STARTLIST = "startlist"
KIND_CLIMBS = "climbs"
KIND_PREDICTIONS = "predictions"
KIND_COBBLES = "cobbles"
# Historical per-edition results (one doc per race-year, slug="{race}-{year}").
# Feeds the results-based rider rating model (scrape_history.py / score_history.py).
KIND_RESULTS = "results"

# Race pcs_slugs to EXCLUDE from the results history + its ratings. The model is
# men's WorldTour + ProSeries only. CALENDAR (hand-coded) leaks two:
#   - "setmana-ciclista-valenciana": actually the WOMEN'S Valencia stage race
#     (men's "Volta a la Comunitat Valenciana" has a different slug).
#   - "gran-camino" (O Gran Camiño): class 2.1, below ProSeries.
# (A full UCI-class scan of the store found these are the ONLY non-WT/ProSeries.)
EXCLUDE_RESULT_PCS_SLUGS = {"setmana-ciclista-valenciana", "gran-camino"}

# caches table names.
CACHE_RIDERS = "riders"
CACHE_BIRTHPLACES = "birthplaces"
CACHE_CLIMBS = "climbs"
CACHE_CLIMBS_NAMES = "climbs_names"
CACHE_START_TIMES = "start_times"


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #
def _resolve_url():
    """Return (url, auth_token) for the active environment."""
    url = os.environ.get("TURSO_DATABASE_URL")
    if url:
        # Force the HTTP transport: the sync client can pick a websocket backend
        # for libsql:// URLs that hangs in CI. https:// hits the same host.
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        elif url.startswith(("ws://", "wss://")):
            url = "https://" + url.split("://", 1)[1]
        return url, os.environ.get("TURSO_AUTH_TOKEN")

    path = os.environ.get("OVERTHEPELOTON_DB", DEFAULT_LOCAL_PATH)
    return "file:" + path, None


def is_remote():
    """True when pointed at a remote Turso DB (vs a local file)."""
    return bool(os.environ.get("TURSO_DATABASE_URL"))


def connect():
    """Open a sync libsql client (does not create the schema)."""
    url, auth = _resolve_url()
    return libsql_client.create_client_sync(url=url, auth_token=auth)


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS race_data (
        kind         TEXT NOT NULL,
        slug         TEXT NOT NULL,
        content      TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        PRIMARY KEY (kind, slug)
    )""",
    """CREATE TABLE IF NOT EXISTS caches (
        name         TEXT PRIMARY KEY,
        content      TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS gpx_files (
        slug         TEXT NOT NULL,
        filename     TEXT NOT NULL,
        stage        INTEGER,
        source       TEXT,
        url          TEXT,
        content      TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        PRIMARY KEY (slug, filename)
    )""",
]


def init_schema(client):
    """Create the tables if they don't exist (idempotent)."""
    for stmt in SCHEMA:
        client.execute(stmt)


def open_db():
    """Connect and ensure the schema exists. The usual entry point."""
    client = connect()
    init_schema(client)
    return client


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _canonical(obj):
    # Stable serialization so the hash is content-addressed, not key-order.
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Per-race JSON documents  (race_data)
# --------------------------------------------------------------------------- #
def put_document(client, kind, slug, obj):
    """Upsert a per-race JSON doc. Writes only when content changed.

    Returns True if a row was written, False if it was already up to date.
    """
    content = _canonical(obj)
    h = _hash_text(content)
    rs = client.execute(
        "SELECT content_hash FROM race_data WHERE kind=? AND slug=?", [kind, slug]
    )
    if rs.rows and rs.rows[0][0] == h:
        return False
    client.execute(
        "INSERT INTO race_data (kind, slug, content, content_hash, updated_at) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(kind, slug) DO UPDATE SET "
        "content=excluded.content, content_hash=excluded.content_hash, "
        "updated_at=excluded.updated_at",
        [kind, slug, content, h, _now()],
    )
    return True


def get_document(client, kind, slug):
    """Return one JSON doc as a Python object, or None if absent."""
    rs = client.execute(
        "SELECT content FROM race_data WHERE kind=? AND slug=?", [kind, slug]
    )
    return json.loads(rs.rows[0][0]) if rs.rows else None


def delete_document(client, kind, slug):
    """Delete one JSON doc. Needs a WRITE-capable Turso token (a read-only token
    returns BLOCKED). Returns nothing."""
    client.execute(
        "DELETE FROM race_data WHERE kind=? AND slug=?", [kind, slug]
    )


def has_document(client, kind, slug):
    """Cheap existence check (doesn't load the content)."""
    rs = client.execute(
        "SELECT 1 FROM race_data WHERE kind=? AND slug=? LIMIT 1", [kind, slug]
    )
    return bool(rs.rows)


def delete_document(client, kind, slug):
    """Delete one race_data doc by (kind, slug). Returns rows deleted (0 or 1).

    There is no pruning in the scrapers — publish.py emits every stored doc — so
    removing a race from the live site (e.g. a mis-mapped women's race) means
    deleting its rows here. Pair with publish.py to regenerate the slices.
    """
    rs = client.execute(
        "DELETE FROM race_data WHERE kind=? AND slug=?", [kind, slug]
    )
    return rs.rows_affected


def list_slugs(client, kind):
    """All slugs stored for a kind, sorted."""
    rs = client.execute(
        "SELECT slug FROM race_data WHERE kind=? ORDER BY slug", [kind]
    )
    return [r[0] for r in rs.rows]


def get_all_documents(client, kind):
    """All docs for a kind as {slug: obj}."""
    rs = client.execute(
        "SELECT slug, content FROM race_data WHERE kind=? ORDER BY slug", [kind]
    )
    return {r[0]: json.loads(r[1]) for r in rs.rows}


# --------------------------------------------------------------------------- #
# Singleton scrape caches  (caches)
# --------------------------------------------------------------------------- #
def put_cache(client, name, obj):
    """Upsert a named cache document. Writes only when content changed."""
    content = _canonical(obj)
    h = _hash_text(content)
    rs = client.execute("SELECT content_hash FROM caches WHERE name=?", [name])
    if rs.rows and rs.rows[0][0] == h:
        return False
    client.execute(
        "INSERT INTO caches (name, content, content_hash, updated_at) "
        "VALUES (?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET "
        "content=excluded.content, content_hash=excluded.content_hash, "
        "updated_at=excluded.updated_at",
        [name, content, h, _now()],
    )
    return True


def get_cache(client, name, default=None):
    """Return a named cache as a Python object, or `default` if absent."""
    rs = client.execute("SELECT content FROM caches WHERE name=?", [name])
    return json.loads(rs.rows[0][0]) if rs.rows else default


# --------------------------------------------------------------------------- #
# GPX files  (gpx_files)
# --------------------------------------------------------------------------- #
def has_gpx(client, slug):
    """True if any GPX is already stored for this race (the over-scraping gate)."""
    rs = client.execute("SELECT 1 FROM gpx_files WHERE slug=? LIMIT 1", [slug])
    return bool(rs.rows)


def put_gpx(client, slug, filename, content, stage=None, source=None, url=None):
    """Upsert one GPX file (content is the raw .gpx XML text).

    Returns True if a row was written, False if unchanged.
    """
    h = _hash_text(content)
    rs = client.execute(
        "SELECT content_hash FROM gpx_files WHERE slug=? AND filename=?",
        [slug, filename],
    )
    if rs.rows and rs.rows[0][0] == h:
        return False
    client.execute(
        "INSERT INTO gpx_files "
        "(slug, filename, stage, source, url, content, content_hash, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(slug, filename) DO UPDATE SET "
        "stage=excluded.stage, source=excluded.source, url=excluded.url, "
        "content=excluded.content, content_hash=excluded.content_hash, "
        "updated_at=excluded.updated_at",
        [slug, filename, stage, source, url, content, h, _now()],
    )
    return True


def delete_gpx(client, slug=None, source=None):
    """Delete GPX rows, narrowed by slug and/or source. Returns rows deleted.

    Both filters are optional but at least one must be given (refusing to wipe the
    whole table by accident). Used by purge_cyclingstage_gpx.py to drop the
    retired cyclingstage routes so publish.py regenerates them from LFR only.
    """
    if not slug and not source:
        raise ValueError("delete_gpx needs a slug and/or a source filter")
    where, params = [], []
    if slug:
        where.append("slug=?")
        params.append(slug)
    if source:
        where.append("source=?")
        params.append(source)
    rs = client.execute(
        "DELETE FROM gpx_files WHERE " + " AND ".join(where), params
    )
    return rs.rows_affected


def get_gpx(client, slug, filename):
    """Return the raw .gpx text for one file, or None."""
    rs = client.execute(
        "SELECT content FROM gpx_files WHERE slug=? AND filename=?", [slug, filename]
    )
    return rs.rows[0][0] if rs.rows else None


def list_gpx(client, slug):
    """Metadata rows for a race's GPX files (no content), sorted by stage then name."""
    rs = client.execute(
        "SELECT filename, stage, source, url FROM gpx_files WHERE slug=? "
        "ORDER BY stage, filename",
        [slug],
    )
    return [
        {"filename": r[0], "stage": r[1], "source": r[2], "url": r[3]}
        for r in rs.rows
    ]


def gpx_slugs(client):
    """All slugs that have at least one stored GPX file."""
    rs = client.execute("SELECT DISTINCT slug FROM gpx_files ORDER BY slug")
    return [r[0] for r in rs.rows]


if __name__ == "__main__":
    # Quick connectivity check. With no env vars this opens the local file DB.
    c = open_db()
    where = "remote Turso" if is_remote() else "local file"
    print(f"Connected to {where}. Schema ready.")
    for kind in ("race", "startlist", "climbs", "predictions", "cobbles"):
        print(f"  race_data[{kind}]: {len(list_slugs(c, kind))} rows")
    print(f"  gpx races: {len(gpx_slugs(c))}")
    c.close()
