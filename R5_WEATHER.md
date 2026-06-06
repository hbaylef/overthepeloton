# R5 — Weather on the map (wind + rain) · Design

> Status: **DESIGN / not built yet** (2026-06-06). Decisions below are locked with
> the user. Build order is in §8. See `PROJECT_CONTEXT.md` §9 R5 for context.

## 1. Goal (user requirements)

On the race map, show — **for the time the race is actually happening**:
1. **Wind direction + speed** along the route. *Critical: at race time*, and
   refined so each point reflects when the peloton passes it (not one global hour).
2. **Potential rain** (precipitation / probability) along the route.
3. A **toggleable map overlay**: turn Wind and/or Rain on and off.
4. Wind shown as **arrows + the speed value**.

## 2. Data source — Open-Meteo (researched + confirmed)

Free, **no API key** (non-commercial), **CORS-enabled** → callable **directly from
the frontend JS**. No weather is stored server-side (forecasts go stale instantly).

Two endpoints, same variables, picked by the race date:

| When the race is | Endpoint | Notes |
|---|---|---|
| ≤ 16 days ahead | `https://api.open-meteo.com/v1/forecast` | live forecast (max 16 d) |
| already run (past) | `https://archive-api.open-meteo.com/v1/archive` | ERA5 actuals (~5-day delay; ECMWF no delay 2017+) |
| > 16 days ahead | — | **no data yet** → show "weather available closer to race day" (decision 3) |

**Hourly variables:** `wind_speed_10m`, `wind_direction_10m`, `wind_gusts_10m`,
`precipitation`, `precipitation_probability`.
**Params:** `latitude`/`longitude` (comma-separated → multiple points in ONE call,
response is an array of location objects), `start_date`/`end_date` (`YYYY-MM-DD`),
`timezone=auto`, `wind_speed_unit=kmh`.
**Response per location:** `hourly.time[]` (ISO, e.g. `2026-06-09T13:00`) +
parallel value arrays (24/day). `wind_direction_10m` is the direction the wind
comes **from**, in degrees.

## 3. Coverage reality (important, not a bug)

Forecasts only exist 16 days out, so at any moment only a slice of races show
weather: **past 2026 races → archive actuals; next ~16 days → forecast; the rest
→ "closer to race day".** Past-race actuals give the feature immediate value.

## 4. Race timing — what we have / what we add

- ✅ **Date**: per-stage `date` (`MM-DD`) and one-day `startdate` (`YYYY-MM-DD`) —
  already in `races.json`.
- ➕ **Start time (NEW, v1 — decision 1):** the PCS library's `Race.stages()` does
  NOT include it; we must fetch each `Stage(stage_url).start_time()` (and the
  one-day race's own page). Because that's one request per stage, **cache it**:
  - New sidecar `data/start_times_cache.json`, keyed by `stage_url` (and one-day
    `pcs_url`) → `{ "start_time": "13:15", "_scraped_at": ... }`.
  - Long TTL (~30 d); refetch only when missing/empty (times publish closer to
    race day) — mirrors `riders_cache.json` / `climbs_cache.json`.
  - Written into `races.json` as `stages[].start_time` (and race-level
    `start_time` for one-day races). Likely a small new step
    `scrapers/scrape_start_times.py` (no new network rules; runs in Actions).
  - **Fallback** when PCS has no time yet: assume a typical window — treat start
    as **12:00 local** (tunable). Flag `start_time_source: "pcs" | "default"`.

## 5. Pass-time model (decision 2 — refined from the start)

Weather at each route point at the hour the **peloton reaches that point**:

```
pass_time(point) = start_time + (cumulative_km(point) / avg_speed[stage_type])
```

- `cumulative_km` from the same GPX distance the profile/map already compute.
- Round `pass_time` to the nearest hour → index into the Open-Meteo `hourly` arrays
  for that point's location.
- `timezone=auto` so the hourly timestamps are in race-local time, matching the
  PCS start time.

**Avg peloton speed by `stage_type`** (km/h; uncalibrated, tunable — we already
have `stage_type` per stage/one-day race):

| stage_type | avg km/h |
|---|---|
| `sprint` | 44 |
| `sprint_break` | 42 |
| `cobbles` | 41 |
| `hills_puncheur` | 40 |
| `climber` (mountain) | 36 |
| `time_trial` | 48 |
| (fallback) | 41 |

## 6. Architecture

**Frontend-direct fetch at view time** (recommended): when a race/stage is shown
and its date is in range, the frontend:
1. samples ~6–10 evenly-spaced points along the route (lat/lon + cumulative km),
2. computes each point's `pass_time` (§5),
3. calls Open-Meteo once (all points, comma-separated) for that date,
4. reads each point's values at its pass-hour,
5. renders the Wind / Rain layers.

Backend change is limited to **§4 start_time**. (Optional later: freeze past-race
weather via a scraper to guard against archive changes — not needed for v1.)

## 7. Frontend overlay spec

- **Toggle UI:** two controls — "Wind", "Rain" — styled to the vintage aesthetic,
  implemented as Leaflet layer groups (like the existing `highlightLayer`). A
  small legend (wind speed scale; rain intensity).
- **Wind layer (arrows + speed — decision 4):** at each sample point a rotated
  arrow `divIcon` — rotation = `wind_direction_10m` (+180° to point the way the
  wind blows *to*, TBD in build), arrow size/colour scaled by `wind_speed_10m`,
  with the **speed value (km/h) labelled** next to it; tooltip adds gusts + the
  pass-time used.
- **Rain layer:** per-point 💧/shaded marker scaled by `precipitation` &
  `precipitation_probability`, or a tint along route segments; tooltip shows mm + %.
- **States:** out-of-range race → a quiet "weather available closer to race day"
  note instead of the toggles; fetch error → graceful "weather unavailable".

## 8. Build order (turnkey)

1. **Scraper:** `start_time` enrichment + cache (§4); writes `races.json`. Validate
   via an Actions run (PCS unreachable locally).
2. **Frontend weather module:** fetch + pass-time (§5,§6) — unit-testable math
   (sampling, pass-hour) in isolation; verify the live fetch in the browser.
3. **Wind layer** (arrows + speed), then **Rain layer**, then **toggles + legend**.
4. Browser pass; tune avg-speed table + sample count + arrow scaling.

## 9. Open / tunable later

- Avg-speed table (§5) and start-time default hour (§4) are guesses — calibrate.
- Sample-point count (density vs API load) — start ~8.
- Arrow semantics (from/to) — confirm visually in build.
- TTT has no row (rare); falls back to 41 km/h.
