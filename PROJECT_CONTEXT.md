# Peloton — UCI World Tour Race Tracker · Project Context

> Upload this document to a new conversation to continue the project with full
> context. It records what we're building, every decision made, the current
> state of the code, what's tested, what isn't, and what's left to do.

---

## 1. What we're building

A **public website** that, for upcoming UCI World Tour cycling races:

1. Fetches the **list of upcoming races** + the **startlist** (riders) for each.
2. Fetches the **GPX route file** for each race / stage.
3. Shows an **interactive map + interactive elevation profile** from the GPX.
4. Shows **betting odds** (race winner) for each race.

**Audience:** the user is a **total beginner** to coding. Explanations should
stay beginner-friendly. Keep prompting/instructions concrete and simple.

**Constraints chosen by the user:**
- Public web page, accessible to anyone.
- Data refreshed **at least daily**.
- **Free** hosting (no paid services).
- Shared publicly.
- No strong tech preference (we chose the stack for them).

---

## 2. Architecture (decided & in progress)

```
┌─────────────────────────────────────────────────────────┐
│  DATA PIPELINE  (Python, runs daily via GitHub Actions)   │
│                                                           │
│  scrape_races.py   → races.json + startlists/*.json       │
│  scrape_gpx.py     → gpx/*/**.gpx + gpx_index.json        │
│  scrape_odds.py    → odds/*.json + odds_index.json        │
│  enter_odds.py     → manual odds fallback                 │
│                                                           │
│  Commits all output as static files back to the repo.     │
└─────────────────────────────────────────────────────────┘
                          │  (static JSON + GPX files)
                          ▼
┌─────────────────────────────────────────────────────────┐
│  FRONTEND  (static site, hosted on GitHub Pages — free)   │
│                                                           │
│  frontend/index.html — single file, no build step.        │
│  Reads the JSON/GPX files and renders:                    │
│    • race list sidebar                                    │
│    • Leaflet interactive map                              │
│    • canvas elevation profile (synced to the map)         │
│    • race-winner odds panel                               │
└─────────────────────────────────────────────────────────┘
```

**Why this design:** GitHub Actions gives free scheduled compute (a daily cron)
on public repos; it runs the scrapers and commits fresh data. GitHub Pages
serves the static frontend for free. Because all data is pre-scraped into static
files, the website itself needs **no backend server at runtime** — which is what
makes free hosting possible.

---

## 3. Data sources (researched & decided)

| Need | Source | How | Notes |
|---|---|---|---|
| Race calendar + startlists | **procyclingstats.com** | `procyclingstats` Python library | Needs `cloudscraper` (Cloudflare). Reliable. |
| GPX routes | **cyclingstage.com** | Scrape HTML for `.gpx` links | **Replaced La Flamme Rouge** (which now needs login + blocks bots). Free, no login, good WT coverage. |
| Betting odds | **bet365.com** | Hub pages first, live engine fallback, manual fallback | Hardest part. Likely blocked from datacenter IPs. |

### Source decisions & history
- **GPX:** user originally suggested La Flamme Rouge (needs login, blocks bots).
  We researched and switched to **cyclingstage.com** — free, no login, GPX files
  served from `cdn.cyclingstage.com/images/{race}/{year}/stage-N-route.gpx`.
- **Odds:** we checked for a clean cycling odds API. **None exists for free** —
  The Odds API doesn't cover cycling; Sportradar/Sportbex are enterprise-priced.
  So we kept Bet365 but target the **public hub pages** (easier) before the
  heavily-defended live betting engine, plus a **manual-entry tool** as a
  guaranteed fallback.

### Key URL patterns
- PCS race: `race/{slug}/{year}` and `race/{slug}/{year}/startlist`
- CyclingStage stage-race GPX index: `cyclingstage.com/{slug}-{year}-gpx/`
- CyclingStage one-day route page: `cyclingstage.com/{slug}-{year}/route-{code}-{year}/`
- CyclingStage GPX file: `cdn.cyclingstage.com/images/{race}/{year}/stage-N-route.gpx`
- Bet365 hub: `bet365.com/hub/en-gb/cycling/cycling-competitions/{slug}`

---

## 4. Current file structure

```
cycling-dashboard/
├── PROJECT_CONTEXT.md          ← this file
├── requirements.txt            ← Python deps
├── scrapers/
│   ├── scrape_races.py         ← STEP 1: races + startlists  (done)
│   ├── scrape_gpx.py           ← STEP 2: GPX routes          (done)
│   ├── scrape_odds.py          ← STEP 4: Bet365 odds         (done)
│   └── enter_odds.py           ← STEP 4: manual odds entry   (done)
├── frontend/
│   └── index.html              ← STEP 3: the whole UI        (done)
└── data/                       ← scraper output (currently SAMPLE data)
    ├── races.json
    ├── gpx_index.json
    ├── odds_index.json
    ├── startlists/{slug}.json
    ├── gpx/{slug}/*.gpx
    └── odds/{slug}.json
```

> **Important:** the `data/` folder currently holds **realistic SAMPLE data**
> generated for development/testing — NOT real scraped data. The scrapers can't
> run in Claude's sandbox (network allowlist blocks the cycling sites), so the
> sample data lets the frontend be built and tested. On the user's machine /
> GitHub Actions, running the scrapers replaces this with real data.

---

## 5. Data formats (contracts between scrapers and frontend)

### `races.json`
```json
{
  "updated_at": "ISO timestamp",
  "year": 2026,
  "total_races": 8,
  "races": [
    {
      "pcs_url": "race/tour-de-france/2026",
      "slug": "tour-de-france-2026",
      "pcs_slug": "tour-de-france",
      "cyclingstage_slug": "tour-de-france",
      "name": "Tour de France",
      "year": 2026,
      "nationality": "FR",
      "startdate": "2026-07-04",
      "enddate": "2026-07-26",
      "category": "Men Elite",
      "uci_tour": "UCI World Tour",
      "is_one_day_race": false,
      "edition": 113,
      "stages": [ { "stage_url": "...", "date": "...", "departure": "...", "arrival": "...", "distance": 185.0 } ]
    }
  ]
}
```

### `startlists/{slug}.json`
```json
{
  "race": "Tour de France", "race_slug": "tour-de-france-2026",
  "updated_at": "ISO", "total_riders": 10,
  "riders": [
    { "name": "POGAČAR Tadej", "nationality": "SI", "number": 1,
      "team": "UAE Team Emirates", "rider_url": "rider/...", "team_url": "team/..." }
  ]
}
```

### `gpx_index.json`
```json
{
  "updated_at": "ISO", "year": 2026,
  "races": {
    "tour-de-suisse-2026": {
      "name": "Tour de Suisse", "gpx_available": true, "total_files": 3,
      "files": [
        { "stage": 1, "filename": "stage-1-route.gpx",
          "url": "https://cdn.cyclingstage.com/...",
          "local_path": "gpx/tour-de-suisse-2026/stage-1-route.gpx" }
      ]
    },
    "vuelta-a-espana-2026": {
      "name": "Vuelta a España", "gpx_available": false,
      "reason": "route_not_yet_published", "total_files": 0, "files": []
    }
  }
}
```
`reason` is `route_not_yet_published` or `no_cyclingstage_mapping`.

### `odds/{slug}.json`
```json
{
  "race": "Tour de Suisse", "race_slug": "tour-de-suisse-2026",
  "source": "bet365",            // or "manual"
  "market": "outright_winner",
  "updated_at": "ISO", "rider_count": 8,
  "riders": [
    { "rider": "Tadej Pogacar", "odds_decimal": 1.67, "odds_raw": "4/6" }
  ]
}
```

### `odds_index.json`
```json
{
  "updated_at": "ISO", "source": "bet365",
  "races": {
    "tour-de-suisse-2026": { "name": "...", "odds_available": true,
      "source": "bet365", "rider_count": 8 }
  }
}
```

---

## 6. Step-by-step progress

| Step | What | Status |
|---|---|---|
| 1 | Race calendar + startlist scraper (`scrape_races.py`) | ✅ Done, syntax-verified. Not run live (sandbox blocks PCS). |
| 2 | GPX scraper (`scrape_gpx.py`) | ✅ Done, syntax-verified. Not run live. |
| 3 | Frontend: map + elevation profile (`index.html`) | ✅ Done, **tested in headless Chromium**. |
| 4 | Bet365 odds scraper + manual entry + frontend odds panel | ✅ Done, parsing logic + frontend **tested**. |
| 5 | GitHub Actions daily pipeline + GitHub Pages deploy | ⏳ **Next** (was going to retest everything first). |

### What's been tested vs not
- **Tested in a real (headless) browser:** the frontend — race list, race
  selection, stage tabs, elevation profile rendering, hover sync, distance/
  elevation/gradient readout, stats (distance/gain/max), "no route" state,
  odds panel (bet365 + manual sources), "no odds" state, data path fallback.
- **Tested in isolation with mock data:** odds fractional→decimal conversion,
  paste parsing, Bet365 hub HTML parsing (list + table layouts).
- **NOT tested live:** the actual scrapers hitting PCS / CyclingStage / Bet365,
  because Claude's sandbox network blocks those domains. They're syntactically
  valid and logically structured but need a real run on the user's machine.

### Known limitations / risks
- **Bet365 odds is the fragile part.** Datacenter IPs (GitHub Actions) are
  likely blocked. The manual-entry tool (`enter_odds.py`) exists so the user is
  never stuck. May need to run odds scraping locally and commit results.
- **PCS race slugs** for 2026 may need updating if races are renamed
  (e.g. Gent-Wevelgem → In Flanders Fields, Critérium du Dauphiné → Tour
  Auvergne-Rhône-Alpes). The slug→hub and slug→cyclingstage maps live in the
  scraper files and are the first place to fix if a race doesn't resolve.
- **CyclingStage GPX** may not exist for races whose route isn't published yet;
  the frontend shows a graceful "route not available" message.

---

## 7. How to run it locally (for the beginner user)

The site **cannot** be opened by double-clicking `index.html` or via Claude's
file preview — browsers block loading the data files that way (you'll see
"Could not load data. Unexpected token '<'"). It must be served over HTTP:

```bash
# 1. Install Python deps (one time)
cd cycling-dashboard
pip install -r requirements.txt

# 2. (Optional) run the scrapers to get REAL data
#    These need internet access to the cycling sites.
python scrapers/scrape_races.py
python scrapers/scrape_gpx.py
python scrapers/scrape_odds.py          # add --live to also try live Bet365

# 3. If odds scraping is blocked, enter them by hand:
python scrapers/enter_odds.py tour-de-france-2026 --paste
#    then paste lines like "Tadej Pogacar 4/6", Ctrl-D to finish.

# 4. Serve the site locally
python -m http.server
#    then open  http://localhost:8000/frontend/  in a browser.
```

On GitHub Pages (Step 5), none of this matters — the public URL just works.

---

## 8. Tech stack / dependencies

- **Python 3** for scrapers.
  - `procyclingstats` (race data), `cloudscraper` (Cloudflare bypass for PCS),
    `requests` + `beautifulsoup4` + `lxml` (CyclingStage + Bet365 hub scraping).
  - `playwright` is **optional**, only for the `--live` Bet365 attempt.
- **Frontend:** plain HTML/CSS/JS in one file. Libraries via CDN:
  - **Leaflet 1.9.4** (interactive map) from cdnjs.
  - **Google Fonts** (Archivo / Archivo Black / JetBrains Mono).
  - Elevation profile is **custom `<canvas>`** code (no library).
- **Hosting:** GitHub Pages (frontend) + GitHub Actions (daily scraper cron).

### Design language (frontend)
Editorial / vintage-cycling-almanac aesthetic: cream paper background
(`--paper #f4f1ea`), rust-red accent (`--rust #c8442a`), ink near-black,
moss green + gold secondary accents. Bold "PELOTON." wordmark in Archivo Black,
monospace (JetBrains Mono) for data readouts, hard offset box-shadows. Avoid
generic/AI-looking design. CSS variables defined in `:root`.

---

## 9. Immediate next step (Step 5)

Set up the **GitHub Actions pipeline + GitHub Pages deployment**:

1. Create a GitHub repo, push this project.
2. A workflow (`.github/workflows/scrape.yml`) that:
   - runs daily on a cron schedule,
   - installs Python deps,
   - runs the three scrapers,
   - commits the updated `data/` back to the repo.
3. Enable GitHub Pages to serve `frontend/` (or move `index.html` to root and
   serve from there — decide path so `index.html`'s data-path fallback works;
   it already tries `../data`, `./data`, `data`, `/data`).
4. Decide how to handle odds: likely run `scrape_odds.py` locally (datacenter
   IPs get blocked by Bet365) and commit, or use `enter_odds.py`.

**The user asked to retest everything end-to-end before doing Step 5.** So the
very next action is to help them run the whole thing locally (scrapers → data →
local server → verify the site), then build the deployment.

---

## 10. Working style notes for the assistant

- The user is a **beginner** — explain clearly, avoid jargon dumps, give exact
  commands they can copy.
- The user likes to **research alternatives before committing** (we did this for
  both GPX and odds sources). Offer to web-search when a better option might
  exist.
- We've been proceeding **one step at a time**, confirming before moving on.
- Files are delivered to the user via the outputs folder; the project lives in
  `cycling-dashboard/`. Keep the folder structure intact.
- Claude's sandbox **cannot reach** procyclingstats.com, cyclingstage.com, or
  bet365.com (network allowlist) and **cannot load** the Leaflet CDN — so live
  scraping and map rendering can only be verified on the user's machine. Build
  and test with sample data; verify logic in isolation.
