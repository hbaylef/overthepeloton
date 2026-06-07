# Peloton — UCI World Tour Race Tracker · Project Context

> Upload this document to a new conversation to continue the project with full
> context. It records what we're building, every decision made, the current
> state of the code, what's deployed, what's tested, and what's left to do.
> **Section 9 holds the feature roadmap (R1–R7) for where the project is going.**

---

## 0. ⚠️ CURRENT STATUS — START HERE (updated 2026-06-07, end of session)

Running in **Claude Code** locally at `C:\Users\PC\Desktop\cycling-dashboard`.
Site is live; each verified increment is committed + pushed (GitHub Pages).

### 🟢 LATEST SESSION (2026-06-07) — R5 weather frontend SHIPPED

- **Integrated origin/main first** (overnight daily scrape `43d21e4`). That scrape
  populated **`start_time` across `races.json`** — 105 stages `pcs`, 13 `default`,
  57/52 `pending` — plus `data/start_times_cache.json`. R5 step 1 paying off; the
  weather frontend now has real start times to read against.
- **R5 weather overlay — BUILT, VERIFIED in browser, SHIPPED** (`frontend/index.html`).
  Open-Meteo direct-from-JS (forecast vs archive by date; recent-past dates route
  to the forecast endpoint to dodge ERA5's ~5-day lag). Pass-time model
  (`PELOTON_KMH` table → `passHourIndex`), 24 route samples. **Wind**: big arrows
  offset perpendicular to the line (`sampleRoute` olat/olon), black→orange→red by
  speed, km/h label. **Rain**: continuous translucent zone (overlapping `L.circle`),
  shown only where `prob > 10% && mm > 0.05` (forecast) or `mm` (archive), blue
  scale by amount, `% · mm` label above each zone. Top-right toggle control +
  legend; toggles persist across stage switches; `wxToken` guards async; no API
  call unless a toggle is on. Tuned live with the user.
- **Race-list FILTER BAR shipped** (same file): search, **World Tour** (merges
  `1.UWT`+`2.UWT`) vs **The rest**, stage/one-day, hide-finished; keeps original
  index for `selectRace`; live count.
- **GPX discovery fix shipped** (`scrape_gpx.py` + `gpx_index.json` + 8 Auvergne
  GPX). Tour Auvergne-Rhône-Alpes (slug `criterium-du-dauphine-2026`) now has all
  8 stages. Fix: crawl sub-links from the main race page (`entry_points[:3]`) +
  `construct_cdn_gpx_urls()` fallback (predictable CDN paths from stage count;
  `download_gpx` still content-validates). General — any future race in this
  situation now works automatically.
- **R4 climb naming fix shipped** (`20b7869`, `derive_climbs.py`). The name matcher
  only matched the PCS `route/climbs` pool by **altitude**, but some races (e.g.
  the **Tour de France**) publish that pool with names + lengths and `top=0`, so
  every climb stayed "Climb". Added a **length-matching fallback** (altitude still
  wins when present; `LEN_MATCH_TOL_KM=2.0`). Re-derived: **TdF 0→48 named**,
  **Tour Auvergne-Rhône-Alpes 0→32 climbs** (its GPX only just landed; names are
  unnamed locally — fill in on the next Actions run). Tests 17/17.
- ⚠️ **Local SSL caveat unchanged:** TLS-intercepting proxy here breaks Python
  cert verification, so live scraping runs in Actions / browser only. The one-off
  Auvergne download used `verify=False`; **the scraper itself was left untouched** —
  don't "fix" scrape_gpx.py to disable verification.
- ⏳ **Pending Actions run:** trigger the "Daily scrape" workflow to (a) name
  Auvergne's 32 climbs and (b) apply the climb length-match to other uncached
  races (e.g. Vuelta). GPX detection works everywhere; PCS naming needs Actions.
- **La Flamme Rouge GPX fallback BUILT then PAUSED** (`scrape_lfr.py` +
  `test_scrape_lfr.py` 9/9; `scrape_gpx.py` preserves LFR entries). WT+ProSeries
  only. ⛔ Blocked by LFR's **Cloudflare managed challenge** (pre-login; no HTTP
  scraper passes it; login secrets don't help). **User is contacting the LFR admin.**
  See §9 "La Flamme Rouge" for diagnosis + resume options.
- **GitHub username RENAMED `hbaylef` → `paludes`** (2026-06-07). Live site is now
  `https://paludes.github.io/overthepeloton/frontend/` (old URL dead); repo
  `github.com/paludes/overthepeloton`; local remote re-pointed. Only doc URLs
  referenced the name — no app code. **Future commits in this repo are anonymous**
  (`git config --local user.email paludes@users.noreply.github.com`); global config
  untouched. Past 32 commits still carry the old gmail — user is **fine leaving
  history as-is** (no rewrite).
- **Frontend trimmed for this public version** (commits `d9b0367`, `4cb1fe6`):
  **win-probability REMOVED from the UI** — Specialty Rankings now shows only career
  PCS specialty points (no Win% column / stage-GC selector; default sort GC desc),
  "pred" badge + per-race predictions fetch + dead win% plumbing/CSS removed.
  **Live-odds panel moved BELOW the Startlist.** "Hide finished races" filter now
  defaults **ticked**. Specialty Rankings section now defaults **collapsed**.
  NOTE: `score_riders.py` + `data/predictions/*` + `predictions_index.json` remain
  in the repo (dormant — predictions_index still fetched once at boot, unused);
  user may retire them later or keep for a future predictions feature.

**R5 open / tunable (not blockers):** avg-speed table + start-time default hour are
guesses; wind-arrow density (24) / offset (2%) and rain thresholds are tuned but
adjustable; **wind FROM-direction not yet in the tooltip** (offered as a self-check
aid — declined for now). See `R5_WEATHER.md` §8–9.

### 🎯 DIRECTION (set by the user 2026-06-07) — what we focus on next

- **R6 odds is PARKED** — not abandoned, deferred until the user finds a viable
  data solution. Access is blocked both ways (Bet365 blocks Actions IPs; the local
  TLS proxy breaks Python scraping). Do NOT build odds until the user revisits it.
- **Focus area 1 — UI/UX fine-tuning.** Polish the existing site (the editorial
  aesthetic, the map/profile/weather/tables interactions). Open-ended; user drives
  specific tweaks. We just did a live tuning pass on the R5 weather overlay.
- **Focus area 2 — close the GPX coverage gap.** ~14 races without routes (past
  races archived differently + upcoming 2026 routes not yet published). The
  Auvergne fix (`construct_cdn_gpx_urls()` + crawl the main race page) recovered
  one. The **La Flamme Rouge fallback** (`scrape_lfr.py`, WT+ProSeries) is built
  but ⛔ **PAUSED** — LFR is behind a Cloudflare managed challenge no HTTP scraper
  can pass; **user is contacting the LFR admin** for blessed access. See §9 "La
  Flamme Rouge" for the full diagnosis + resume options.
- **R7 non-WT** stays a later-stage goal (the filter bar already buckets "the rest").

---

**Done & live:**
- **R1** — startlists for all races + per-rider PCS **career** specialty points.
- **R2** — stage grading + win-probability (percentile + **softmax**,
  `score_riders.py`); per-stage win% + GC, Specialty Rankings table, team grid.
- **R3** — gradient-coloured elevation profile + hover label + drag-to-zoom.
- **R4 cobbles** — pavé sectors as **brown profile segments** from curated
  `data/cobbles/{slug}.json`.
- **R4 climbs scraper (LIVE on origin/main)** — `scrapers/scrape_climbs.py`
  (commit `62186c1`) + the daily Actions step; **the 2026-06-06 08:05 UTC scrape
  ran and committed real data** (`data/climbs/{slug}.json` + `climbs_index.json`
  + `climbs_cache.json`). **One-day races: 12 races, 164 real climbs** (Liège,
  Lombardia, Flanders, etc. — name/length/steepness/top/km_before_finish).
  Covered by `scrapers/test_scrape_climbs.py` (no-network, 7/7).

**⚠️ UNCOMMITTED in the working tree (built + tested this session, NOT pushed —
verify, then commit next session):**
- `frontend/index.html` — **climbs rendering** (numbered ▲ markers with
  length·gradient labels, a clickable **Climbs list** below the profile, ▲/list
  click = **foot→summit zoom**, hover readout) + **map highlights**
  (`drawHighlightsOnMap`: pavé = brown, climbs = steepness-coloured stretches +
  numbered summit markers, with casing) + a **fix** for Start/Finish markers
  stacking on stage switch. JS syntax-checked; **NOT yet eyeballed in a browser**
  (Leaflet CDN is blocked in Claude's sandbox — verify on the live-ish local
  server against the REAL climbs data now present locally).
- `scrapers/score_riders.py` — **R4 cobbles scoring tie-in**: new `cobbles`
  weight vector (`one_day_races 1.0 · sprint 0.4 · tt 0.3 · hills 0.4`, weights
  **signed off by the user**) + `one_day_stage_type()` that promotes any race
  with a curated `data/cobbles/{slug}.json` to `cobbles` at scoring time (no
  `races.json` mutation), + `base["stage_type"]` added to one-day output.
- `scrapers/test_score_riders.py` — **new** no-network tests (7/7).
- Smoke-tested end-to-end (`main()` to a temp dir): 36 predictions write
  cleanly; Paris-Roubaix → `cobbles`, top5 Van Aert/Pedersen/Van der Poel/
  Degenkolb/Laporte (vs sprinters under the old `sprint` mis-classification).

**To finish the cobbles tie-in (next session):** re-run `python
scrapers/score_riders.py` on the fresh data, then commit + push **frontend +
score_riders.py + test_score_riders.py + regenerated `data/predictions/*`**.
This **changes live predictions** — that's expected and approved.

**✅ R4 climbs — STAGE RACES now have climbs, DERIVED FROM GPX + NAMED FROM PCS
(LIVE on origin/main `d8c0b57`, 2026-06-06).** The earlier "custom
`/info/profiles` parser" plan is
**dead — that page has no climb data**. Verified against real saved pages
(`scrapers/fixture/`, gitignored):
- PCS `…/stage-N/info/profiles` is **images only** — a stage-profile JPG + N
  unnamed "Climb" JPGs. No table, no names, no length/steepness/km. `RaceClimbs`
  rejecting it (`<h2>` is "Profiles" not "Climbs") was a red herring; there's
  nothing to parse.
- **cyclingstage** `…-{year}-gpx/` is the same story: one table (stage #,
  start–finish, km, type, GPX link) + per-stage profile JPGs with the climbs
  **painted into the image**. No climb text anywhere. Extracting them = OCR
  (rejected: fragile, not pipeline-viable).
- **Solution — `scrapers/derive_climbs.py`**: detect climbs from the GPX we
  already download (hysteresis foot→summit walk; 6371 km haversine + 200 m
  elevation smoothing to match the frontend; thresholds ≥1 km, ≥60 m gain,
  ≥3 % avg). Writes `data/climbs/{slug}.json` `stages{}` in the **same shape the
  frontend already renders** (`name/km_before_finish/length_km/steepness/top_m`)
  → **no frontend change**. One-day races keep their named PCS climbs.
  GPX detection is **no-network** (runs + tests locally and in Actions).
- **NAMES (`d8c0b57`):** PCS publishes the race's climbs (with altitude) on the
  race-level `route/climbs` page — the SAME `RaceClimbs` call one-day races use.
  `derive_climbs.py` fetches that pool once per race and attaches a name to each
  detected climb by **matching on altitude** (`top_m`, ±40 m tol; greedy, no pool
  reuse, length breaks ties). Unmatched climbs stay "Climb". Pool cached in
  `data/climbs_names_cache.json`. **PCS is only reachable from Actions** (this
  machine's TLS proxy), so names populate when the workflow runs — GPX detection
  works everywhere, naming needs an Actions run.
- **Ran locally: 13 stage races, 385 climbs** (named: 0 locally — PCS blocked;
  names fill in on Actions). Tests `scrapers/test_derive_climbs.py` 15/15
  (synthetic GPX + mock name pools). Wired into the daily workflow after
  `scrape_climbs.py`. Browser pass: GPX climbs verified rendering on the local
  server (showed "Climb"); re-check names after the workflow run.
- ⏳ **AFTER the next Actions run:** verify names on the live site; if many
  climbs stay "Climb", widen `TOP_MATCH_TOL_M` in `derive_climbs.py`.

**Scoring input caveat (unchanged):** still PCS **career** points; swap to **PCM
WorldDB** is **PARKED** pending a user `.sqlite` (see `project-data-source-swap`).

**Pick up next session — open items (in order):**
1. ✅ DONE — cobbles tie-in shipped (`5fd1e19`); GPX-derived stage climbs +
   PCS-altitude naming shipped (`74dcb29`, `d8c0b57`). All live on origin/main.
2. **Verify climb NAMES after an Actions run** — a "Daily scrape" workflow run was
   triggered 2026-06-06; once green, `git pull` and check the live site shows real
   names (Chommle, Oberarig…) on stage races. If many stay "Climb", widen
   `TOP_MATCH_TOL_M` in `derive_climbs.py` and re-run the workflow.
3. Then **R5** weather (Open-Meteo) / **R6** odds / **R7** non-WT.

**Workflow:** edit → verify on a local server (`python -m http.server 8000`,
open `/frontend/`) → commit → push. `score_riders.py` is run **manually** (not in
cron). The daily scrape lands data commits on `origin/main` → `git fetch` +
fast-forward/rebase before pushing (done already this session: local is at
`55f62f8`).

---

## 1. What we're building

A **public website** that, for upcoming UCI World Tour cycling races:

1. Fetches the **list of races** (full year, past + future) + **startlists**.
2. Fetches the **GPX route file** for each race / stage.
3. Shows an **interactive map + interactive elevation profile** from the GPX.
4. Shows **betting odds** (race winner) for each race (when populated).

**Status: LIVE AND DEPLOYED.**
- Public URL: **`https://paludes.github.io/overthepeloton/frontend/`**
- Repo: **`https://github.com/paludes/overthepeloton`**
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
│  Site path:    https://paludes.github.io/overthepeloton/  │
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
│   ├── scrape_gpx.py            ← STEP 2: GPX routes (crawl-based; preserves LFR entries)
│   ├── scrape_lfr.py            ← GPX FALLBACK: La Flamme Rouge (WT+ProSeries, local-only)
│   ├── test_scrape_lfr.py       ← no-network tests for the LFR fallback (9/9)
│   ├── scrape_odds.py           ← STEP 4: Bet365 odds
│   ├── enter_odds.py            ← STEP 4: manual odds entry
│   ├── scrape_riders.py         ← R1: embeds specialties.career into startlists
│   ├── classify_stages.py       ← R2 Phase 2: backfill stage_type into races.json
│   ├── score_riders.py          ← R2 Phase 3: predictions (R4 cobbles tie-in shipped)
│   ├── scrape_climbs.py         ← R4: one-day climbs via RaceClimbs (PCS, named)
│   ├── derive_climbs.py         ← R4: stage-race climbs DERIVED from GPX (no network)
│   ├── test_scrape_climbs.py    ← R4: no-network tests for the climbs scraper (7/7)
│   ├── test_derive_climbs.py    ← R4: no-network tests for GPX climb detection (10/10)
│   └── test_score_riders.py     ← R4: no-network tests for scoring + cobbles (7/7)
├── frontend/
│   └── index.html               ← STEP 3: whole UI (R4 climbs + map highlights UNCOMMITTED)
├── R1_R2_DESIGN.md              ← R1+R2 build spec (Tier 1) + R4/R5 research (Tier 2)
└── data/                        ← REAL scraped data (live)
    ├── races.json               ← 37 races
    ├── gpx_index.json
    ├── odds_index.json          ← sample odds for 3 races
    ├── riders_cache.json        ← R1: career specialty points (7-day cache)
    ├── climbs_index.json        ← R4: which races have climbs (12 one-day; stage=0)
    ├── climbs_cache.json        ← R4: per-URL climbs cache (7-day, retries empties)
    ├── cobbles/{slug}.json      ← R4: curated pavé sectors (paris-roubaix)
    ├── climbs/{slug}.json       ← R4: scraped climbs (one-day populated; stage races empty)
    ├── predictions/{slug}.json  ← R2: win-prob (re-run score_riders to apply cobbles tie-in)
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
- ✅ **Phase 2 done & live (2026-06-05, commit `519a7db`):** `classify_stage`
  function — pure logic, no scraping. `scrape_races.py` now writes a derived
  `stage_type` + `stage_type_source` annotation **inside** `races.json` (per
  stage for stage races, at race level for one-day races) at the end of every
  scrape. Output values: `sprint`, `sprint_break`, `hills_puncheur`,
  `climber`, `time_trial`; `cobbles` deferred to R4. ITTs detected by
  stage-name regex (overrides the icon — 10 of 12 ITTs were icon `p1`). TTT is
  a documented gap. Standalone `scrapers/classify_stages.py` backfills without
  scraping. **Verified on the deployed site:** 175/175 entries annotated, 0
  missing, 163 `profile_icon` / 12 `stage_name_itt`.
- ✅ **Phase 3 done & live:** Steps 2–4 — type → specialty weight vector →
  per-rider score → pseudo win-probability. `scrapers/score_riders.py` writes
  `data/predictions/*` + `predictions_index.json`. Score→prob via temperature
  **softmax** (`SOFTMAX_TEMPERATURE=0.15`) so the favourite stands out (the
  first linear cut flattened everyone to ~1.5%). Stage races carry **per-stage
  win%** (each stage scored on its own `stage_type`) **plus** overall GC
  (`0.6·gc + 0.4·stage-mean`); one-day races a single list. Career-only blend
  until R1's `recent` block ships. Frontend: sortable **Specialty Rankings**
  table, collapsible **Startlist by Team** grid, section toggles, flagcdn flags,
  jersey glyphs, rider→PCS links, and a `getRaceRoster` data-access layer (see
  `R1_R2_DESIGN.md`).
  ⏸ `score_riders.py` is intentionally NOT in the daily Actions workflow — the
  rider/specialty data source is being swapped, so predictions stay a manual
  re-run for now.
  ⚠️ **Win-probability is REMOVED from the current public frontend (2026-06-07).**
  The model + `data/predictions/*` still exist (dormant), but the Specialty
  Rankings table now shows career PCS points only (no Win% column/dropdown), and
  the sidebar "pred" badge is gone. See §0 "Frontend trimmed".
- Until R1's `recent` block ships, Step 3's blend degrades to `career`-only
  (`blended = career_norm`). Structure preserved so `recent` can drop in later.
- See `R1_R2_DESIGN.md` for the full 4-step model + weight vectors + Phase 1
  details.
- **Derivation method (still open):** (a) own algorithm = stage type × rider
  specialty points (planned starting point); (b) scrape PCS's own
  predictions/startlist-quality; (c) both, compared. **Planned path:** ship (a)
  → calibrate against (b) → (c).

### R3 — Elevation profile shows gradient changes  ✅ **DONE & live (2026-06-05)**
- Profile is coloured **segment-by-segment by steepness** (stepped roadbook
  palette: blue-grey descent → moss → gold → rust → maroon 12%+), with a legend.
  Grade is smoothed over a ~250 m window (`GRADE_WINDOW_KM`), precomputed once
  per stage; the static profile is cached to an offscreen canvas so hover only
  redraws a light overlay (smooth).
- **On-graph hover label** pinned near the top showing `dist · grade% · elev`.
- **Drag-to-zoom**: select a range to zoom (refits both axes), the **map fits
  that segment**, a top-right **badge** shows the selection's distance + average
  gradient; reset via a button or double-click.

### R4 — Highlight key segments (climbs + cobbles)  🔨 **mostly done (2026-06-06)**
- ✅ **Cobbles on the profile:** curated `data/cobbles/{slug}.json` (sectors with
  km_start/km_end + ★ rating) render as **brown segments** on the elevation
  profile (`#4a2c12`), with a `cobbles` legend swatch and sector name/★ in the
  hover readout. Seeded `paris-roubaix-2026.json` (26-sector starter — verify vs
  the official roadbook; real men's PR has ~29–30). Also: races with men+women
  GPX now default to the main (men's) route.
- ✅ **Climbs scraper (LIVE):** `scrapers/scrape_climbs.py` fetches
  `procyclingstats` `RaceClimbs` and writes `data/climbs/{slug}.json` +
  `climbs_index.json` (+ a 7-day `climbs_cache.json` that retries empty routes).
  Stores raw `km_before_finish`; the frontend places each climb at
  `x = total_km − km_before_finish` (anchored to the GPX finish). Runs in the
  daily Actions workflow. **One-day races populated (12 races, 164 climbs);
  stage races empty — see below.**
- ✅ **Climbs on the profile + map (UNCOMMITTED, see §0):** numbered ▲ markers
  (length·gradient) + clickable Climbs list + foot→summit zoom; map highlights
  (`drawHighlightsOnMap`) for pavé + climbs. Built + syntax-checked, **needs a
  browser pass**.
- ✅ **R2 cobbles scoring tie-in (UNCOMMITTED, see §0):** `cobbles` weight vector;
  a curated cobbles file promotes the race to `cobbles` at scoring time. Needs a
  `score_riders.py` re-run + push to reach live predictions.
- ✅ **Stage-race climbs — SOLVED & LIVE (`derive_climbs.py`, `74dcb29` +
  `d8c0b57`, 2026-06-06).** Both PCS `/info/profiles` and cyclingstage publish
  per-stage climbs **as images only** (no parseable text), so we **detect** climbs
  from the GPX elevation (same data shape, no frontend change, no network) and
  **name** them from PCS's race-level `route/climbs` pool by altitude match (names
  populate via Actions; this machine can't reach PCS). 13 stage races, 385 climbs.
  See §0 for the full diagnosis.

### R5 — Weather on the map (wind / rain)
- Overlay wind (direction + strength) and rain conditions along the route.
- **Needs a weather API — TBD.** (Open-Meteo is a likely free candidate but not
  yet researched/chosen.) Forecasts only meaningful close to race day.

### R6 — Odds: actually scrape and show them  ⏸ **PARKED (2026-06-07)**
- **Deferred by the user** until a viable data solution is found. Access is
  blocked both ways: Bet365 blocks GitHub Actions IPs, and this machine's
  TLS-intercepting proxy breaks Python cert verification, so neither path reaches
  live odds cleanly today. Do not build odds until the user revisits it.
- The odds code exists (`scrape_odds.py`, `enter_odds.py`, frontend panel) but
  has **not been run successfully against live Bet365 data yet**.
- Goal (when un-parked): get real odds flowing and displayed. May require a
  non-proxied machine, a more scrapable source, or the manual paste tool as the
  practical fallback. Still the "hardest part" of the project.

### R7 — Extend to non-World-Tour races (later stage)
- Broaden coverage beyond UCI World Tour: ProSeries, Continental, women's
  racing, etc. Explicitly a **later-stage** goal once the above are solid.

### La Flamme Rouge supplemental GPX source — 🔨 TOOL BUILT (2026-06-07), calibrating
Fallback for the GPX cyclingstage misses, **scoped to WorldTour + ProSeries** (user
decision). `scrapers/scrape_lfr.py` + `test_scrape_lfr.py` (9/9). **No login needed**
for LFR's public maps section (the old "requires login / sid" worry was overstated —
confirmed against the open-source `jalnichols/p-c` LFR scraper). Mechanics:
- Race listing: `/maps/races?count=0&page={p}&calendar[0]={cal}&year[0]={yr}&name={q}`
  (cal 1=UWT, 2=Europe, 3=Americas, 4=Asia). Race page: `/maps/races/view/{id}/{name}`
  lists the stage tracks. GPX: `/maps/viewtrack/gpx/{track_id}`.
- **Fallback discipline:** only fills races still `gpx_available:false`; MERGES into
  `gpx_index.json` tagging `"source":"la_flamme_rouge"`; never rebuilds it.
- **scrape_gpx.py now PRESERVES** LFR entries on its daily rebuild (LFR runs locally
  only, not in Actions — otherwise the cron would wipe them).
- **Run locally only** (LFR blocks bots/Actions IPs; polite 3–7 s random delays).
  This machine's TLS proxy → use `--insecure` (or `LFR_INSECURE=1`). Start with
  `--dry-run` to see resolved races/track ids; pin a race via `LFR_RACE_OVERRIDES`
  if name auto-match misses. ToS likely prohibits scraping; user accepted the risk.
- ⛔ **BLOCKED by Cloudflare (diagnosed 2026-06-07).** LFR sits behind a Cloudflare
  **managed challenge** (`Cf-Mitigated: challenge`, "Just a moment…", `_cf_chl_opt`
  on the homepage itself — i.e. pre-login). HTTP scrapers **cannot** pass it:
  plain `requests` → 403; `cloudscraper` (1.2.71) only beats the retired `jschl`
  challenge, not managed/Turnstile; and on this machine its TLS-fingerprint context
  also collides with the corporate TLS proxy. **Login secrets do NOT help** — the
  challenge is in front of login, and LFR's public GPX needs no login anyway.
- ➡️ **Resume options when we return to LFR:** (1) **admin cooperation** — user is
  contacting the LFR admin for a data export / blessed access (PREFERRED; pending
  reply); (2) **`cf_clearance` cookie** — paste a browser-obtained Cloudflare
  clearance cookie + matching UA into the scraper's fetch layer (IP/UA-bound,
  expires in ~30 min–hours, semi-manual refresh); (3) **Playwright** headed real
  browser (heavier, may still be flagged); (4) **browser-assisted import** (user
  downloads `/maps/viewtrack/gpx/{id}` via their browser, a script ingests).
- **STATUS: PAUSED pending the LFR admin's reply.** The scraper scaffolding
  (`scrape_lfr.py`: target selection, parsing, GPX validation, index merge +
  `scrape_gpx.py` preserve-logic) is built and unit-tested (9/9) — only the *fetch
  layer* needs swapping for whichever resume option we pick. 11 WT+ProSeries races
  still lack GPX (TDU, Flèche W., Romandie, Suisse, San Sebastián, Renewi, Britain,
  Québec, Montréal, Lombardia, Paris-Tours).

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
