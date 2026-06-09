"""THROWAWAY smoke test for the Turso re-architecture (build-order step 0).

Proves the whole chain works in GitHub Actions:
  secrets -> libsql client install -> Turso write/read -> commit-back.

Same code runs locally against a SQLite FILE when no Turso URL is set
(mirrors the local-vs-remote switch scrapers/db.py will use later):
  - remote: TURSO_DATABASE_URL (libsql://...) + TURSO_AUTH_TOKEN
  - local:  no env vars -> falls back to file:_smoke_local.db

Delete this file (and .github/workflows/turso-smoke.yml) at step 6.
"""

import datetime
import json
import os

import libsql_client

url = os.environ.get("TURSO_DATABASE_URL") or "file:_smoke_local.db"
auth = os.environ.get("TURSO_AUTH_TOKEN")  # None for the local file path

where = "remote Turso" if url.startswith(("libsql:", "http:", "https:")) else "local file"
print(f"Connecting to {where}: {url}")

client = libsql_client.create_client_sync(url=url, auth_token=auth)

client.execute(
    "CREATE TABLE IF NOT EXISTS _smoke "
    "(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT, ts TEXT)"
)

stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
client.execute("INSERT INTO _smoke (note, ts) VALUES (?, ?)", ["hello from actions", stamp])

count = client.execute("SELECT COUNT(*) FROM _smoke").rows[0][0]
last = client.execute("SELECT note, ts FROM _smoke ORDER BY id DESC LIMIT 1").rows[0]
client.close()

print(f"row count: {count}")
print(f"last row:  {last[0]} @ {last[1]}")

os.makedirs("data", exist_ok=True)
with open("data/_turso_ok.json", "w", encoding="utf-8") as f:
    json.dump(
        {
            "ok": True,
            "where": where,
            "rows": count,
            "last_note": last[0],
            "last_ts": last[1],
            "checked_at": stamp,
        },
        f,
        indent=2,
    )
print("wrote data/_turso_ok.json")
