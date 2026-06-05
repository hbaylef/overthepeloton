# Peloton — UCI World Tour Race Tracker · Project Context

> Upload this document to a new conversation to continue the project with full
> context. It records what we're building, every decision made, the current
> state of the code, what's deployed, what's tested, and what's left to do.
> **Section 9 holds the feature roadmap (R1–R7) for where the project is going.**

---

## 0. ⚠️ READ THIS FIRST (for the assistant)

**The user wants to switch to Claude Code for future sessions.** They said:
> "I can't do it in this session but will do it later, keep in this mind"

In a future conversation, if Claude Code isn't already in use, **proactively
remind them** that this was their plan. The setup is: install Node.js, then
`npm install -g @anthropic-ai/claude-code`, then `claude` inside the repo.

Once on Claude Code, the workflow gets much faster — direct file edits, ability
to run scrapers locally for testing, no more copy-paste-from-chat dance.

---

## 1. What we're building

A **public website** that, for upcoming UCI World Tour cycling races:

1. Fetches the **list of races** (full year, past + future) + **startlists**.
2. Fetches the **GPX route file** for each race / stage.
3. Shows an **interactive map + interactive elevation profile** from the GPX.
4. Shows **betting odds** (race winner) for each race (when populated).

**Status: LIVE AND DEPLOYED.**
- Public URL: **`https://hbaylef.github.io/overthepeloton/frontend/`**
- Repo: **`https://github.com/hbaylef/overthepeloton`**
- Auto-refreshes daily at 06:00 UTC via GitHub Actions.

**Audience:** the user is a self-described **beginner** to coding, but has
gotten comfortable with terminal, Git, GitHub through this project. Explanations
should still be clear and beginner-friendly, but assume they know how to
`git add/commit/push`, run Python scripts, etc.

**Constraints chosen by the user:**
- Public web page, accessible to anyone — ✅ done.
- Data refreshed **at least daily** — ✅ done (GitHub Actions cron).
- **Free** hosting — ✅ done (GitHub Pages + Actions).
- Shared publicly — ✅ done.
- No strong tech preference — Python + plain HTML/JS chosen.

---

## 2. Architecture (deployed)

```
┌─────────────────────────────────────────────────────────┐
│  DATA PIPELINE  (Python, runs daily on GitHub Actions)    │
│                                                           │
│  scrape_races.py   → races.json + startlists/*.json       │
│  scrape_gpx.py     → gpx/*/**.gpx + gpx_index.json        │
│  scrape_odds.py    → odds/*.json + odds_index.json        │
│  enter_odds.py     → manual odds fallback (local-only)    │
│                                                           │
│  Workflow file:  .github/workflows/scrape.yml             │
│  Schedule:       '0 6 * * *' UTC (daily ~8 AM Paris)      │
│  Commits fresh data back to main branch.                  │
│                                                           │
│  NOTE: scrape_odds.py runs LOCALLY only (Bet365 blocks    │
│  GitHub IPs). User commits odds output manually.          │
└─────────────────────────────────────────────────────────┘
                          │  (static JSON + GPX files)
                          ▼
┌─────────────────────────────────────────────────────────┐
│  FRONTEND  (static site on GitHub Pages)                  │
│                                                           │
│  frontend/index.html — single file, no build step.        │
│  Pages source: "Deploy from a branch", main /(root).      │
│  Site path:    https://hbaylef.github.io/overthepeloton/  │
│                                                            frontend/
│  Renders: race list sidebar · Leaflet map · canvas        │
│  elevation profile (synced) · race-winner odds panel.     │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Data sources (decided)

| Need | Source | How | Notes |
|---|---|---|---|
| Race calendar + startlists | **procyclingstats.com** | `procyclingstats` library + `cloudscraper` for Cloudflare | Works reliably from GitHub Actions and locally. |
| GPX routes | **cyclingstage.com** | Crawl-based discovery (visits multiple URL patterns + follows internal links) | Free, no login, ~62% race coverage. |
| Betting odds | **bet365.com** | Hub-page scraper + Playwright fallback + manual paste tool | Likely blocked from GitHub Actions; user runs locally. |

### Source decisions & history
- **GPX:** user originally suggested La Flamme Rouge → we switched to
  cyclingstage (no login, public). User raised LFR again later (wanted more
  coverage), but **deferred** to a future session — see Section 9.
- **Odds:** no free cycling odds API exists (The Odds API doesn't cover
  cycling; Sportradar/Sportbex are enterprise-priced). Bet365 hub pages
  chosen as easier than the live engine, manual entry as fallback.

### Key URL patterns
- PCS race: `race/{slug}/{year}` and `race/{slug}/{year}/startlist`
- CyclingStage stage-race GPX index: `cyclingstage.com/{slug}-{year}-gpx/`
- CyclingStage one-day route page: `cyclingstage.com/{slug}-{year}/route-{code}-{year}/`
- CyclingStage GPX file: `cdn.cyclingstage.com/images/{race}/{year}/stage-N-route.gpx`
- Bet365 hub: `bet365.com/hub/en-gb/cycling/cycling-competitions/{slug}`

---

## 4. Current file structure

```
overthepeloton/
├── .github/workflows/scrape.yml ← daily cron + manual trigger
├── .gitignore                   ← __pycache__, *.pyc, etc.
├── PROJECT_CONTEXT.md           ← this file
├── README_TEST.md               ← user-facing local-test instructions
├── requirements.txt             ← Python deps
├── scrapers/
│   ├── scrape_races.py          ← STEP 1: races + startlists
│   ├── scrape_gpx.py            ← STEP 2: GPX routes (crawl-based)
│   ├── scrape_odds.py           ← STEP 4: Bet365 odds
│   ├── enter_odds.py            ← STEP 4: manual odds entry
│   ├── scrape_riders.py         ← R1: embeds specialties.career into startlists
│   └── classify_stages.py       ← R2 Phase 2: backfill stage_type into races.json
├── frontend/
│   └── index.html               ← STEP 3: the whole UI (cache-busted fetches)
├── R1_R2_DESIGN.md              ← R1+R2 build spec (Tier 1) + R4/R5 research (Tier 2)
└── data/                        ← REAL scraped data (live)
    ├── races.json               ← 37 races
    ├── gpx_index.json
    ├── odds_index.json          ← sample odds for 3 races
    ├── riders_cache.json        ← R1: career specialty points (7-day cache)
    ├── startlists/{slug}.json   ← 36 startlists (riders carry specialties.career)
    ├── gpx/{slug}/*.gpx         ← real GPX for ~23 races (TdF, Giro, Vuelta, classics)
    └── odds/{slug}.json
```

---

## 5. Data formats (contracts between scrapers and frontend)

### `races.json`
```json
{
  "updated_at": "ISO timestamp",
  "year": 2026,
  "total_races": 37,
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
      "stages": [ { "stage_url": "...", "date": "...", "stage_name": "Stage 1 (ITT) | ...", "profile_icon": "p1",
                    "stage_type": "time_trial", "stage_type_source": "stage_name_itt" } ],
      "_pcs_data_missing": false  // historical fallback flag; no longer set after R1 slug fixes
    },
    {
      "slug": "il-lombardia-2026",
      "is_one_day_race": true,
      "stages": [],
      "profile_icon": "p5",                    // R2 Phase 1: race-level icon for one-day races
      "profile_icon_source": "manual_override", // "pcs" or "manual_override"
      "stage_type": "climber",                 // R2 Phase 2: derived from profile_icon
      "stage_type_source": "profile_icon"      // "profile_icon" | "stage_name_itt" | "fallback_default"
    }
  ]
}
```

`_pcs_data_missing: true` is a historical flag (no entries set it after R1's
slug fixes). On stage races, each `stages[]` entry carries `profile_icon`
from PCS. On one-day races (where `Race.stages()` returns `[]`), the
race-level `profile_icon` + `profile_icon_source` come from R2 Phase 1's
`/result` scrape; see `R1_R2_DESIGN.md` Step 1 status. R2 Phase 2 then derives
`stage_type` + `stage_type_source` from those icons (per-stage on stage races,
race-level on one-day races) — written by `annotate_stage_types()` at the end
of every scrape.

### `startlists/{slug}.json`
```json
{ "race": "...", "race_slug": "...", "updated_at": "...", "total_riders": 118,
  "riders": [ { "name": "POGAČAR Tadej", "nationality": "SI", "number": 1,
                "team": "...", "rider_url": "rider/...", "team_url": "team/...",
                "specialties": { "career": { "one_day_races": 9983, "gc": 7594,
                                              "tt": 3287, "sprint": 297,
                                              "climber": 9989, "hills": 4368 } } } ] }
```

Per-rider `specialties.career` is added by R1's `scrape_riders.py`. Value is
`null` for riders with no PCS chart. The `recent` half is deferred — see
`R1_R2_DESIGN.md` "Spike outcome".

### `gpx_index.json`
```json
{
  "updated_at": "ISO", "year": 2026,
  "races": {
    "tour-de-france-2026": {
      "name": "Tour de France", "gpx_available": true, "total_files": 21,
      "files": [ { "stage": 1, "filename": "stage-1-route.gpx",
                   "url": "https://cdn.cyclingstage.com/...",
                   "local_path": "gpx/tour-de-france-2026/stage-1-route.gpx" } ]
    },
    "tour-de-suisse-2026": {
      "name": "Tour de Suisse", "gpx_available": false,
      "reason": "route_not_yet_published", "total_files": 0, "files": []
    }
  }
}
```

### `odds/{slug}.json`
```json
{ "race": "Tour de Suisse", "race_slug": "tour-de-suisse-2026",
  "source": "bet365",  // or "manual"
  "market": "outright_winner",
  "updated_at": "ISO", "rider_count": 8,
  "riders": [ { "rider": "Tadej Pogacar", "odds_decimal": 1.67, "odds_raw": "4/6" } ] }
```

### `odds_index.json`
```json
{ "updated_at": "ISO", "source": "bet365",
  "races": { "tour-de-suisse-2026": { "name": "...", "odds_available": true,
              "source": "bet365", "rider_count": 8 } } }
```

---

## 6. Step-by-step progress

| Step | What | Status |
|---|---|---|
| 1 | Race calendar + startlist scraper (`scrape_races.py`) | ✅ Done. After R1 slug fixes (2026-06-03): 37/37 races have PCS data; 36 startlists (Tour of Britain's still publishes closer to race day). |
| 2 | GPX scraper (`scrape_gpx.py`) — crawl-based discovery | ✅ Done, ran live, 23/37 races got GPX (62%). |
| 3 | Frontend: map + elevation profile (`index.html`) | ✅ Done, deployed on GitHub Pages, cache-busting added. |
| 4 | Bet365 odds scraper + manual entry + frontend odds panel | ✅ Code done & tested with mocks. Real-data run pending; user will run locally. |
| 5 | GitHub Actions daily pipeline + GitHub Pages deploy | ✅ DONE. Pages enabled, workflow file committed, manual run succeeded (12m 31s, all green). |

### What's working live (verified by user)
- ~37 races appear in the sidebar.
- Race selection shows correct stage tabs.
- Map renders route with start/finish markers.
- Elevation profile renders, hover syncs marker on the map, distance/elevation/gradient readout updates.
- "Route not available" message for the 14 races without GPX.
- Odds panel shows for the 3 sample-data races; "no odds" message for others.
- Manual GitHub Actions run completed successfully end-to-end.

### Known limitations / things deferred
- **14 races have no GPX yet.** Mix of (a) past races where cyclingstage may
  have archived files differently, (b) upcoming races whose 2026 routes aren't
  published yet (Tour de Suisse, Lombardia, Renewi, San Sebastián, Québec,
  Montréal, Tour of Britain, Paris-Tours, etc.). Will fill in automatically as
  cyclingstage uploads them.
- **Bet365 odds scraping has not been run live yet.** User plans to run locally
  occasionally and commit results.
- **La Flamme Rouge alternative GPX source: deferred** (see Section 9).
- **Date encoding on Windows terminal:** cmd.exe shows mojibake for accents
  (e.g. "Vuelta a EspaÃ±a"). The JSON files on disk are correct UTF-8.

---

## 7. How to run / develop locally

```bash
# (One-time) install deps
pip install -r requirements.txt

# Run scrapers — needs internet to PCS + cyclingstage + (for odds) Bet365
python scrapers/scrape_races.py     # ~3-5 min, 37 races
python scrapers/scrape_gpx.py       # ~10-15 min, crawls cyclingstage

# Manual odds (when needed)
python scrapers/enter_odds.py tour-de-france-2026 --paste

# Local preview
python -m http.server
# open http://localhost:8000/frontend/  (incognito recommended to avoid cache)

# Push updates
git add data/ scrapers/ frontend/
git commit -m "..."
git push           # triggers GitHub Pages rebuild automatically
```

---

## 8. Tech stack / dependencies

- **Python 3** for scrapers.
  - `procyclingstats` (race data) + `cloudscraper` (Cloudflare bypass for PCS).
  - `requests` + `beautifulsoup4` + `lxml` (CyclingStage + Bet365 hub).
  - `playwright` is **optional**, only for the `--live` Bet365 attempt.
- **Frontend:** plain HTML/CSS/JS in one file. Libraries via CDN:
  - **Leaflet 1.9.4** (interactive map) from cdnjs.
  - **Google Fonts** (Archivo / Archivo Black / JetBrains Mono).
  - Elevation profile is **custom `<canvas>`** code (no library).
- **CI:** GitHub Actions (`actions/checkout@v4`, `actions/setup-python@v5`).
- **Hosting:** GitHub Pages (public repo, free tier, no usage limits reached).

### Design language (frontend)
Editorial / vintage-cycling-almanac aesthetic: cream paper background
(`--paper #f4f1ea`), rust-red accent (`--rust #c8442a`), ink near-black,
moss green + gold secondary accents. Bold "PELOTON." wordmark in Archivo Black,
monospace (JetBrains Mono) for data readouts, hard offset box-shadows. Avoid
generic/AI-looking design. CSS variables defined in `:root`.

### Frontend resilience features
- **Data-path fallback** — tries `../data`, `./data`, `data`, `/data` so the
  page works whether served from repo root or `/frontend/`.
- **Cache-busting** — JSON and GPX fetches include `?t=${Date.now()}` and
  `cache: 'no-store'` so newly-scraped data shows up immediately.
- **Graceful Leaflet failure** — if Leaflet CDN fails, elevation profile still
  works on its own.
- **Clear error UI** — if data can't load, shows specific guidance based on
  whether the URL is `file://` (use a local server) vs missing data.

---

## 9. Roadmap — planned features (future work, no priority order)

These are the user's stated goals for where the project is headed. Captured as
a backlog, not in priority order. Several have open design questions noted.

### R1 — Startlists for ALL races + rider specialty points  ✅ **DONE (2026-06-03)**
- ✅ All **36 of 37** races now have full startlists (was ~31/37). Tour of
  Britain's startlist publishes closer to its Sept race date — by design.
- ✅ Every rider entry in `data/startlists/{slug}.json` carries
  `"specialties": { "career": { one_day_races, gc, tt, sprint, climber, hills } | null }`.
- ✅ `null` block when PCS has no chart for the rider (1 such rider out of
  1,173 at first seed).
- ✅ Bookkeeping in sidecar `data/riders_cache.json` (7-day freshness gate
  survives `scrape_races.py`'s daily startlist overwrites).
- ⏸ The `recent` block (last 2 seasons split by specialty) is **DEFERRED** —
  the procyclingstats library doesn't cleanly expose per-season-by-specialty
  data. See `R1_R2_DESIGN.md` "Spike outcome" for the full finding + the
  future path (Option 1 — derive from `season_results` bucketed by stage
  profile).
- This rider-specialty data is the foundation for R2.

### R2 — Stage grading + win-probability ranking  ← **in progress**
- Classify each stage by type (sprint / hilly / mountain / cobbles / ITT —
  ideally matching PCS's own specialty categories).
- Rank each race's startlist riders by how well their specialty points fit the
  stage/race type → a homemade "win probability" per rider.
- ✅ **Spike done (2026-06-03):** stage races have 100% `profile_icon`
  coverage; one-day races have 0% via the library — PCS exposes the
  race-level icon only on the `/result` subpage.
- ✅ **Phase 1 done (2026-06-03):** `scrape_races.py` now scrapes
  `/result` for every one-day race and writes `profile_icon` +
  `profile_icon_source` onto each one-day race in `data/races.json`. A small
  `ONE_DAY_OVERRIDE` dict supplies a known-correct value when PCS returns
  `p0` (placeholder); override is bypassed automatically once PCS publishes
  a real icon. ITT detection lands in Phase 2 via stage-name regex.
- ✅ **Phase 2 done (2026-06-05):** `classify_stage` function — pure logic,
  no scraping. `scrape_races.py` now writes a derived `stage_type` +
  `stage_type_source` annotation **inside** `races.json` (per stage for
  stage races, at race level for one-day races) at the end of every scrape.
  Output values: `sprint`, `sprint_break`, `hills_puncheur`, `climber`,
  `time_trial`; `cobbles` deferred to R4. ITTs detected by stage-name regex
  (overrides the icon — 10 of 12 ITTs were icon `p1`). TTT is a documented
  gap. Standalone `scrapers/classify_stages.py` backfills without scraping.
- ⏭ **Phase 3 next:** Steps 2–4 — type → specialty weight vector → per-rider
  score → pseudo win-probability. Career-only blend until R1's `recent`
  block ships.
- Until R1's `recent` block ships, Step 3's blend degrades to `career`-only
  (`blended = career_norm`). Structure preserved so `recent` can drop in later.
- See `R1_R2_DESIGN.md` for the full 4-step model + weight vectors + Phase 1
  details.
- **Derivation method (still open):** (a) own algorithm = stage type × rider
  specialty points (planned starting point); (b) scrape PCS's own
  predictions/startlist-quality; (c) both, compared. **Planned path:** ship (a)
  → calibrate against (b) → (c).

### R3 — Elevation profile shows gradient changes
- Improve the canvas elevation profile to reflect **steepness** — e.g. colour
  segments by gradient so a 12% ramp looks visibly different from a false flat,
  like TV-broadcast / roadbook profiles.
- Data is already available (we compute per-point gradient on hover); needs to
  be rendered along the whole profile, not just at the cursor.

### R4 — Highlight key segments (climbs + cobbles)
- Mark the major categorised climbs and cobbled (pavé) sectors on the profile,
  and ideally on the map too — like official roadbooks.
- **Open question:** where does the segment data come from? PCS lists climbs
  with KM marks; cobbled sectors may need another source. TBD.

### R5 — Weather on the map (wind / rain)
- Overlay wind (direction + strength) and rain conditions along the route.
- **Needs a weather API — TBD.** (Open-Meteo is a likely free candidate but not
  yet researched/chosen.) Forecasts only meaningful close to race day.

### R6 — Odds: actually scrape and show them
- The odds code exists (`scrape_odds.py`, `enter_odds.py`, frontend panel) but
  has **not been run successfully against live Bet365 data yet**.
- Goal: get real odds flowing and displayed. May require running locally,
  finding a more scrapable source, or the manual paste tool as the practical
  fallback. Still the "hardest part" of the project.

### R7 — Extend to non-World-Tour races (later stage)
- Broaden coverage beyond UCI World Tour: ProSeries, Continental, women's
  racing, etc. Explicitly a **later-stage** goal once the above are solid.

### Deferred — La Flamme Rouge supplemental GPX source
User wanted to add LFR for races cyclingstage misses. We agreed on a **hybrid**
approach: keep cyclingstage as primary, add LFR as fallback for races without
GPX. Run LFR scraping **locally only** (it requires login, GH Actions IPs would
get blocked). Key constraints:
- LFR requires user login → store creds as GitHub Secrets if ever moved to Actions.
- LFR session IDs (`sid=...`) appear in URLs — those expire and must not be
  shared publicly.
- LFR's ToS likely prohibits scraping; user accepted the small ban risk.

**Why we deferred:** user wanted to lock in the working cyclingstage deployment
first, then add LFR as a non-breaking supplement. (Relevant to R4/R7 — LFR may
help with both extra races and segment data.)

### Smaller polish ideas (not committed to)
- Search/filter box on the race list.
- Show race results once a race is complete (PCS has this data).
- Rider photos / team kits.
- Mobile layout improvements.

---

## 10. Working style notes for the assistant

- **User has progressed from beginner.** They've now done: Git Bash, real Git
  workflows including `pull --rebase`, terminal-based scraper runs, GitHub
  Pages setup, GitHub Actions triggering. Still appreciate beginner-friendly
  explanations but don't over-explain basics like "what is a terminal".
- **They like to research alternatives before committing** (we did this for
  GPX sources and odds APIs). Offer to web-search when a better path exists.
- **One step at a time.** They confirm before moving on. Don't bundle multiple
  big changes into one push.
- **🔁 NEXT TIME: encourage them to install Claude Code** (see Section 0). The
  current copy-paste-from-chat workflow is slowing them down on file edits.
- **Sandbox caveats (if continuing without Claude Code):** Claude's sandbox
  cannot reach procyclingstats.com, cyclingstage.com, bet365.com, or
  la-flamme-rouge.eu — so live scraping must run on the user's machine or via
  GitHub Actions. Leaflet's CDN is also blocked, so map rendering can only be
  verified in their real browser. Build with mock data, validate logic in
  isolation, test integration on user's side.
- **Live deployment exists now.** Be careful — pushed changes go to a real
  public site. Test logic before suggesting `git push`.
