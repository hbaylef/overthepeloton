# Test Steps 2 + 3 locally

This bundle lets you **see the website working** with sample data, and
optionally run the **GPX scraper** to fetch real route data.

There are two things here:
- **Step 3 (the website):** view the map + elevation profile in your browser.
- **Step 2 (the GPX scraper):** fetch real GPX route files.

You can do the website test on its own — it already includes sample data.

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

## Part B — Run the GPX scraper (Step 2)  ← optional

This fetches **real** GPX route files from cyclingstage.com and replaces the
sample data.

1. Install the Python tools needed (one time):
   ```
   cd cycling-dashboard
   pip install -r requirements.txt
   ```
   (Use `pip3` if `pip` doesn't work.)

2. The GPX scraper reads `data/races.json` to know which races to fetch. A
   sample `races.json` is already included, so you can run the GPX scraper
   straight away:
   ```
   python scrapers/scrape_gpx.py
   ```
   It will download GPX files into `data/gpx/` and update `data/gpx_index.json`.

3. (Optional) To also refresh the race list itself from procyclingstats.com:
   ```
   python scrapers/scrape_races.py
   ```
   Run this **before** `scrape_gpx.py` if you want a fresh race list.

4. Re-open the website (Part A) to see the real routes.

---

## If something doesn't work

- **"Could not load data. Unexpected token '<'"** → you opened the file
  directly. Use the local server and the `http://localhost:8000/frontend/` URL.
- **`python` not found** → try `python3`. If neither works, install Python
  from python.org.
- **A race shows "route not available"** → that race's GPX isn't on
  cyclingstage.com yet, or the slug needs updating (see PROJECT_CONTEXT.md,
  section 6 "Known limitations").
- **The map area is blank but the profile works** → the map tiles need
  internet access; check your connection.

For the full picture of the project, read **PROJECT_CONTEXT.md**.
