# Run the site locally

This lets you **see the website working** from the published data already in
`data/`, and explains where that data comes from.

There are two parts here:
- **Part A (the website):** view the map + elevation profile in your browser.
- **Part B (the data):** how the slices are produced (Turso → `publish.py`);
  you normally don't run this — GitHub Actions does.

You can do Part A on its own — the committed slices are all it needs.

---

## Part A — View the website (Step 3)  ← start here

The website needs to be served by a small local web server. You **cannot** just
double-click `index.html` (the browser will block the data files and you'll see
"Could not load data").

1. Open a terminal (Command Prompt on Windows, Terminal on Mac).
2. Go into the project folder:
   ```
   cd cycling-dashboard
   ```
3. Start a local server (Python comes with this built in):
   ```
   python -m http.server
   ```
   (On some systems it's `python3` instead of `python`.)
4. Open your browser and go to:
   ```
   http://localhost:8000/frontend/
   ```
5. You should see the **PELOTON** site. Click a race in the left sidebar —
   try **Tour de Suisse** (3 stages) or **Il Lombardia** (one-day race).
   You'll see the route on the map and the elevation profile below it.
   **Hover your mouse over the elevation profile** — a marker moves along the
   route on the map, and the distance / elevation / gradient update live.

To stop the server, press `Ctrl + C` in the terminal.

> Note: the map needs internet (it loads map tiles online). The sample data is
> made-up routes for testing — Part B fetches the real ones.

---

## Part B — How the data is produced (you normally don't run this)

The raw scraped data (PCS responses, full `.gpx`, caches) now lives in a
**private Turso database**, not in this repo. The website reads only small
**published slices** that are already committed in `data/` (so Part A works
out of the box).

The pipeline runs automatically in **GitHub Actions**, split in two:
- **Daily** (`.github/workflows/scrape.yml`): the volatile data — startlists,
  start times, results — then `publish.py` + commit.
- **Weekly** (`.github/workflows/scrape-weekly.yml`, Mondays): the full refresh —
  race calendar, rider data, climbs — then publish + commit.

To run by hand, **trigger either workflow from the repo's Actions tab** (live
scraping needs procyclingstats.com, which the runner reaches and a TLS-proxied
machine can't). **GPX routes are the exception:** their sole source is La Flamme
Rouge (`scrapers/scrape_lfr.py`), an ATTENDED local run (LFR blocks Actions IPs) —
see PROJECT_CONTEXT.md.

If you just want to regenerate the public slices from the store locally:

1. Install the Python tools (one time):
   ```
   cd cycling-dashboard
   pip install -r requirements.txt
   ```
2. Point at the store (optional — without these it uses a **local** SQLite file
   `data/overthepeloton.db` instead of remote Turso):
   ```
   set TURSO_DATABASE_URL=libsql://...      (Windows: set,  Mac/Linux: export)
   set TURSO_AUTH_TOKEN=...
   ```
3. Write the slices:
   ```
   python scrapers/publish.py
   ```
   This (re)writes `data/races.json`, `data/routes/`, `data/climbs/`, etc.
4. Re-open the website (Part A).

The logic tests are no-network and run against a temp SQLite file:
```
python scrapers/test_db.py        (and the other scrapers/test_*.py)
```

---

## If something doesn't work

- **"Could not load data. Unexpected token '<'"** → you opened the file
  directly. Use the local server and the `http://localhost:8000/frontend/` URL.
- **`python` not found** → try `python3`. If neither works, install Python
  from python.org.
- **A race shows "route not available"** → that race has no route in the store
  yet (La Flamme Rouge hasn't published it, or the attended `scrape_lfr.py` run
  hasn't fetched it). Routes fill in over time; see PROJECT_CONTEXT.md.
- **The map area is blank but the profile works** → the map tiles need
  internet access; check your connection.

For the full picture of the project, read **PROJECT_CONTEXT.md**.
