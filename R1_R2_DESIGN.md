# Peloton Tracker — R1 + R2 Design Spec

> **Handoff doc for Claude Code.** Tier 1 (R1 + R2) is the build spec — implement
> this. Tier 2 (R4 + R5) is captured research — feasibility is confirmed, but
> these are *not* ready to build yet; the section preserves findings so they
> don't need re-researching later.
>
> Scope: R1 (data foundation) and R2 (scoring). R3, R6, R7 are out of scope here.

---

## 0. Purpose

Extend the tracker so that every race carries a full startlist with rider
specialty data (R1), and so each race's riders can be ranked by how well their
specialties fit the stage/race terrain (R2 — a homemade "win probability").

R1 is the data foundation; R2 consumes it. R1 must land and be verified before
R2 is built. R2's cobble-type scoring additionally depends on the R4 curated
cobble dataset (Tier 2), so cobble-specific scoring comes slightly later than
the rest of R2.

---

# TIER 1 — BUILD NOW

## 1. R1 — Startlists for all races + rider specialty points

### Goal
- Every race gets its full rider list (close the current ~31/37 gap).
- Each rider carries **two** specialty blocks: career totals and recent (last
  2 seasons) points, across the six PCS specialties.

### Data source
- `procyclingstats` library (already a dependency).
  - **Career totals:** the `Rider` specialty dict — keys `one_day_races`, `gc`,
    `time_trial`, `sprint`, `climber`, `hills`.
  - **Recent points:** per-season results, summed over the last 2 calendar
    seasons, split by the same six specialties.

### "Last 2 years" definition
- Use **calendar seasons** (e.g. 2025 + 2026). Easier to scrape and reason
  about than a rolling 24-month window.
- Rolling-24-month is a **deferred alternative** — noted, not built.

### ⚠️ Spike to run FIRST (before committing R1 schema)
Pull a single rider and confirm you can cleanly obtain **both** halves:
- The career specialty dict (confirmed available).
- A per-season, per-specialty points breakdown for the last 2 seasons.

These may not come from the same library call. If the per-season split isn't
cleanly exposed by specialty, decide the fallback before building (e.g. derive
recent specialty points from season results filtered by race profile, or accept
a coarser recent signal). **Do not build the full scraper until this is
confirmed.**

### Spike outcome (2026-06-03) — `recent` block DEFERRED

Ran the spike against `rider/tadej-pogacar` via the procyclingstats library.

| Method | Per-season? | Per-specialty? |
|---|---|---|
| `points_per_speciality()` | ❌ career only | ✅ |
| `points_per_season_history()` | ✅ | ❌ totals only |
| `season_results()` (URL-scoped to a year) | ✅ | ❌ no specialty label |

The library does **not** cleanly expose per-season-by-specialty data.
`Rider('rider/foo/2026').points_per_speciality()` returns the same numbers as
the un-scoped call — the URL year does not filter specialty totals.

**Choice — Option 3 (career-only).** The `recent` block and the 50/50
`career`/`recent` blend are **DEFERRED**. Reason: data not cleanly available
from the library; the data foundation needed to land first. Schema wrapper
`specialties.career` is preserved so `recent` can be added later without a
schema migration.

**Future path if `recent` is revisited — Option 1: derive from
`season_results` bucketed by stage profile.**
For each rider, call `season_results()` twice (last 2 calendar seasons). For
each result row, look up the stage's `profile_icon` and bucket its `pcs_points`
into a specialty using the same Step 1 classification R2 already uses. Reuses
R2's type taxonomy for consistency. Cost: ~2× HTTP per rider on top of career
(~80 min seed); stage→profile lookup can be cached globally and reused across
riders.

**Other fallbacks considered and rejected for now:**
- **Option 2** — coarser `recent`: total recent points without per-specialty
  split. Cheap, but breaks the per-specialty model R2 depends on.

### What R1 actually shipped (2026-06-03)
- All **36 of 37** races now have full startlists (was ~31). Tour of Britain's
  startlist appears closer to its Sept race date — by design, not a bug.
- Every rider entry in `data/startlists/{slug}.json` carries
  `"specialties": { "career": { ... } | null }`.
- `null` block when PCS has no `.pps` chart (1 such rider out of 1,173 at
  first seed).
- Bookkeeping in sidecar `data/riders_cache.json` (7-day freshness gate
  that survives `scrape_races.py`'s daily startlist overwrites).
- PCS's `time_trial` key is renamed to `tt` on output per spec.
- The `recent` key is **omitted** from real output (not written as `null`)
  until Option 1 ships.

### Per-rider schema (added to each rider in `startlists/{slug}.json`)
```json
"specialties": {
  "career": { "one_day_races": 751, "gc": 1140, "tt": 312, "sprint": 30, "climber": 1213, "hills": 575 },
  "recent": { "one_day_races": 0,   "gc": 220,  "tt": 40,  "sprint": 0,  "climber": 380,  "hills": 110 }
}
```
- Store **raw points** for both blocks. Do not pre-normalise or pre-blend at
  scrape time — R2 does that at scoring time so the 50/50 split can be retuned
  without re-scraping.
- Riders with missing data: write `null` blocks rather than zeros, so R2 can
  distinguish "no data" from "genuinely zero points."

> **Status (2026-06-03):** Only the `career` half ships. Actual output is
> `"specialties": { "career": {...} | null }` — the `recent` key is omitted
> (not written as `null`) until Option 1 is built. Schema above is the
> eventual end-state.

### Known limitations (record in output / context)
- Specialty points are PCS points, i.e. an accumulation metric — even the
  "recent" block is points, not a pure form model.
- Gaps expected: some riders (especially lesser-known or very young) will have
  thin or missing data.

---

## 2. R2 — Stage grading + win-probability ranking

### The 4-step model

**Step 1 — Classify the stage/race into a type.**
Primary signal: PCS `profile_icon` (`p0`–`p5`), refined by flags.

| Signal | Type |
|---|---|
| p0 / p1 (flat) | sprint |
| p2 (hilly, flat finish) | sprint/break mix |
| p3 (hilly, uphill finish) | hills/puncheur |
| p4 / p5 (mountain) | climber |
| ITT flag | time_trial |
| cobble flag (from R4 curated set, Tier 2) | cobbles |

> Note: `profile_icon` may be absent for some/older races (library caveat).
> Fall back to a sensible default (e.g. treat as sprint/break) and flag it.

**Step 2 — Map type → specialty weight vector.**
Starting weights (uncalibrated — see open questions):

| Stage type | one_day | gc | tt | sprint | climber | hills |
|---|---|---|---|---|---|---|
| Sprint | 0.1 | 0 | 0 | 1.0 | 0 | 0.2 |
| Hilly/puncheur | 0.4 | 0.1 | 0 | 0.2 | 0.2 | 1.0 |
| Mountain | 0.2 | 0.5 | 0 | 0 | 1.0 | 0.3 |
| ITT | 0 | 0.3 | 1.0 | 0 | 0 | 0 |
| Cobbles | 1.0 | 0 | 0.1 | 0.3 | 0 | 0.4 |

Keep these as a **named constant** (config dict), not inline literals.

**Step 3 — Score each rider.**

> **Status (2026-06-03) — career-only blend.** Until the `recent` block ships
> (see R1 Spike outcome), `blended[spec] = career_norm[spec]` and the 50/50
> split is effectively a no-op. The structure below is preserved so adding
> `recent` later is a small change at scoring time, not a code rewrite.

First build the **blended specialty value** per rider per specialty. The
decision (your choice): **normalise each half within the startlist first, THEN
50/50 average** — so the 50/50 is an honest 50/50, not dominated by the larger
career totals.

```
# within the current startlist, per specialty:
career_norm[spec] = normalise(career[spec])   # e.g. 0..1 or percentile across startlist
recent_norm[spec] = normalise(recent[spec])

blended[spec] = 0.5 * career_norm[spec] + 0.5 * recent_norm[spec]
```

Then:
```
score = Σ over specialties ( weight[type][spec] * blended[spec] )
```
Normalise scores across the startlist into a **pseudo win-probability** (% that
sums to 100 across riders).

- The `0.5 / 0.5` split is a **named constant** — retunable without re-scraping.
- Normalisation method (min-max vs percentile) is an implementation choice;
  percentile is more robust to outliers (one dominant rider won't flatten the
  rest). Pick one, keep it consistent across both halves.

**Step 4 — Aggregate for stage races.**
A GC contender needs whole-race strength, not single-stage fit:
```
final_rank = 0.6 * gc_blended_norm + 0.4 * mean(per_stage_score)
```
Also a **named constant** blend. One-day races skip this step — the Step 3
score is the result.

### Output
- Per race: an ordered rider list with pseudo-probabilities.
- Label the output **"experimental"** in the UI — see open questions.

---

# TIER 2 — RESEARCHED, READY TO SPEC LATER

> Not for this build. Feasibility confirmed during the planning session;
> findings captured so they survive to the next session.

## R4 — Key segments: climbs + cobbles

**Climbs — automatable.**
- The `procyclingstats` library exposes a **`RaceClimbs`** class plus a
  "grouping climbs by stages" example — categorised climbs are scrapeable
  through the library you already use.
- Caveat: climb info is **not always present**, usually for older races.
  Expect gaps; handle nulls.
- These same climbs feed the elevation profile annotations (R4) and reinforce
  stage typing (R2).

**Cobbles — hand-curated, NOT a scrape.**
- No cobbled-sector class or method exists in the PCS library, and there is no
  free API for pavé sectors anywhere.
- PCS shows sectors visually on Roubaix pages but does not parse them into a
  structured field — pulling them = brittle race-specific HTML parsing. Avoid.
- Pavé is decisive in only ~3–4 races/year (Paris-Roubaix above all, plus some
  Tour/Belgian-classic sectors).
- **Plan:** maintain a small hand-built `cobbles/{slug}.json` once a year from
  the official sector list (km mark, length, star rating per sector). The km
  marks map straight onto the elevation profile x-axis. Low effort, reliable.
- This curated file is also what supplies R2's **cobble flag** (Step 1).

## R5 — Weather overlay (wind / rain)

- **Source: Open-Meteo — confirmed, no blocker.** Free, open-source, **no API
  key, no signup**. One HTTP GET per coordinate returns temperature, wind speed,
  wind direction, plus hourly forecasts; rain available too.
- Up to ~1 km resolution; ECMWF model selectable directly (best for FR/IT/BE
  races). Coordinate-based, so it maps directly onto existing GPX route points.
- **Implementation sketch:** sample a handful of points along the GPX, call
  Open-Meteo per point for the race-day hour, overlay wind arrows + rain on the
  map.
- **Constraint:** forecasts are only meaningful within ~16 days of race day —
  render the weather overlay only for imminent races.
- No GitHub Secrets needed (no key), so this can run client-side or in Actions.

---

## Appendix — Open design questions (recorded, not decided)

1. **Derivation method (from roadmap R2).** Three options: (a) own algorithm =
   stage type × specialty points; (b) scrape PCS's own predictions /
   startlist-quality; (c) both, compared. **Planned path:** ship (a) with the
   weights above → calibrate against (b) as a yardstick → arrive at (c).
2. **Weights are uncalibrated guesses.** The model *structure* is sound and
   explainable; the numbers need tuning against real outcomes before the
   probabilities are trustworthy. Hence the "experimental" label.
3. **Recency refinement deferred.** A finer per-season weighting (beyond the
   flat 50/50 career/recent split) is a later option, not built now.
4. **"Last 2 years" boundary.** Calendar-season (chosen) vs rolling-24-month
   (deferred).

## Appendix — Sequencing

```
R1 (verify spike → build scraper → schema)
   └─> R2 (Steps 1–4, non-cobble types)
          └─> R2 cobble scoring  ← depends on R4 curated cobble set
R4 climbs (library) / R4 cobbles (hand-curated)  — Tier 2, later
R5 weather (Open-Meteo)                          — Tier 2, later
```
