#!/usr/bin/env python3
"""
R2 Phase 3 — stage grading → win-probability ranking (Steps 2-4).

Pure logic, no scraping. Reads:
  - data/races.json            (each stage / one-day race carries `stage_type`)
  - data/startlists/{slug}.json (each rider carries `specialties.career`)

Writes:
  - data/predictions/{slug}.json  — per race, riders ordered by pseudo win-prob
  - data/predictions_index.json   — which races have a prediction

The model (see R1_R2_DESIGN.md "R2 — Stage grading"):

  Step 2  type → specialty weight vector            (TYPE_WEIGHTS)
  Step 3  per-rider score:
            career_norm[spec] = percentile of career[spec] within the startlist
            blended[spec]     = career_norm[spec]   (recent block deferred → no-op)
            score             = Σ weight[type][spec] * blended[spec]
          one-day races stop here.
  Step 4  stage races aggregate whole-race strength:
            final = GC_AGG_WEIGHT * gc_norm
                  + STAGE_AGG_WEIGHT * percentile(mean per-stage score)

  Final scores → pseudo win-prob (sum 100) via a temperature softmax
  (SOFTMAX_TEMPERATURE) so the favourite stands out instead of the field
  reading near-uniform.

Output: one-day races get a single ranked rider list. Stage races get, per
rider, an overall `gc_win` plus a `stage_win[]` array (one win% per stage,
each stage scored against its own stage_type) — so the frontend can show a
per-stage ranking or the overall GC. See `stages_meta` for stage labels/types.

Output is EXPERIMENTAL — the weights are uncalibrated starting guesses.

Usage:
  python scrapers/score_riders.py
"""

import json
import logging
import math
from bisect import bisect_left, bisect_right
from datetime import datetime
from pathlib import Path
from statistics import mean

import db  # local module: Turso/SQLite store (build-order step 2)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Cobbles stay on disk: curated, public, hand-edited (not scraped raw data).
COBBLES_DIR = DATA_DIR / "cobbles"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# The six PCS specialties, as keyed in startlists/{slug}.json -> specialties.career.
SPECIALTIES = ["one_day_races", "gc", "tt", "sprint", "climber", "hills"]

# ---------------------------------------------------------------------------
# Step 2 — stage_type → specialty weight vector.
#
# Named constant (uncalibrated starting guesses — output is "experimental").
# Keys use the DATA specialty names (one_day_races, not the doc's "one_day").
# `sprint_break` has no row in the original design table (it predates the
# 5-way stage_type split) — this is a fresh starting guess: a sprinter who
# survives / breakaway profile sitting between Sprint and Hilly/puncheur.
# `cobbles` (R4): pavé classics. PCS has no cobbles specialty, so we proxy with
# classics pedigree (one_day_races, dominant) + raw power (sprint) + engine (tt)
# + durability (hills). climber/gc ~0 — Roubaix is pan-flat. A race is promoted
# to this type at scoring time when it has a curated data/cobbles/{slug}.json.
# ---------------------------------------------------------------------------
TYPE_WEIGHTS = {
    "sprint":         {"one_day_races": 0.1, "gc": 0.0, "tt": 0.0, "sprint": 1.0, "climber": 0.0, "hills": 0.2},
    "sprint_break":   {"one_day_races": 0.3, "gc": 0.0, "tt": 0.0, "sprint": 0.6, "climber": 0.1, "hills": 0.5},
    "hills_puncheur": {"one_day_races": 0.4, "gc": 0.1, "tt": 0.0, "sprint": 0.2, "climber": 0.2, "hills": 1.0},
    "climber":        {"one_day_races": 0.2, "gc": 0.5, "tt": 0.0, "sprint": 0.0, "climber": 1.0, "hills": 0.3},
    "time_trial":     {"one_day_races": 0.0, "gc": 0.3, "tt": 1.0, "sprint": 0.0, "climber": 0.0, "hills": 0.0},
    "cobbles":        {"one_day_races": 1.0, "gc": 0.0, "tt": 0.3, "sprint": 0.4, "climber": 0.0, "hills": 0.4},
}

# Step 3 blend: 50/50 career/recent — but `recent` is deferred, so the blend
# degrades to career-only (blended = career_norm). Kept as a named constant so
# adding `recent` later is a one-line change, not a rewrite.
CAREER_WEIGHT = 0.5
RECENT_WEIGHT = 0.5

# Step 4 aggregation for stage races: whole-race GC strength vs single-stage fit.
GC_AGG_WEIGHT = 0.6
STAGE_AGG_WEIGHT = 0.4

# Score → probability via softmax with temperature. The ranking step is sound,
# but a LINEAR score/Σscore conversion of an evenly-spread score can't make a
# favourite stand out (top ends up only ~2× the median across a big field, so
# every rider reads ~1.5%). Softmax amplifies the contrast: lower T sharpens
# (favourite pulls away), higher T flattens back toward uniform. Scores are
# min-max normalised to [0, 1] within the race first, so T means the same thing
# regardless of race type / weight magnitudes. Tunable — output stays
# "experimental".
SOFTMAX_TEMPERATURE = 0.15

MODEL_LABEL = "experimental"


def percentile_ranks(values):
    """
    Map each value to its percentile rank in [0, 1] within `values`, using
    midranks for ties. Robust to a single dominant rider (unlike min-max, one
    outlier won't flatten everyone else toward 0).

    All-equal input (e.g. a specialty where everyone has 0 points) -> 0.5 for
    all, a neutral signal.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    ordered = sorted(values)
    ranks = []
    for v in values:
        lo = bisect_left(ordered, v)
        hi = bisect_right(ordered, v)
        midrank = (lo + hi - 1) / 2.0  # average 0-based index of the tie group
        ranks.append(midrank / (n - 1))
    return ranks


def softmax_probabilities(scores, temperature=SOFTMAX_TEMPERATURE):
    """
    Convert raw scores into percentages summing to 100 via a temperature
    softmax. Scores are first min-max normalised to [0, 1] within the race so
    `temperature` has a consistent meaning across race types. Lower temperature
    sharpens toward the favourite; higher flattens toward uniform.

    Degenerate input (empty, or every score equal) -> uniform split.
    """
    n = len(scores)
    if n == 0:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo <= 0:
        return [100.0 / n] * n
    norm = [(s - lo) / (hi - lo) for s in scores]
    exps = [math.exp(v / temperature) for v in norm]
    total = sum(exps)
    return [e / total * 100.0 for e in exps]


def weighted_score(blended, weights):
    """Σ weight[spec] * blended[spec] over the six specialties."""
    return sum(weights[spec] * blended[spec] for spec in SPECIALTIES)


def build_blended(scored_riders):
    """
    For the riders that have career data, compute the per-specialty percentile
    (career_norm) and the career-only blend. Returns a list of dicts aligned
    with `scored_riders`, each mapping specialty -> blended value in [0, 1].
    """
    # Percentile each specialty independently across the scored startlist.
    norms = {}
    for spec in SPECIALTIES:
        col = [float(r["specialties"]["career"].get(spec) or 0) for r in scored_riders]
        norms[spec] = percentile_ranks(col)

    blended = []
    for i in range(len(scored_riders)):
        # recent deferred → blended = career_norm. Constants kept for clarity.
        blended.append({
            spec: CAREER_WEIGHT * norms[spec][i] + RECENT_WEIGHT * norms[spec][i]
            for spec in SPECIALTIES
        })
    return blended


def one_day_stage_type(race):
    """
    Effective stage_type for a one-day race. A curated cobbles set
    (data/cobbles/{slug}.json) promotes the race to the `cobbles` type (R4
    tie-in) — so e.g. Paris-Roubaix is scored on pavé weights rather than its
    profile-icon classification. Otherwise the race's own stage_type is used.
    """
    if (COBBLES_DIR / f"{race['slug']}.json").exists():
        return "cobbles"
    return race.get("stage_type")


def score_one_day(race, scored_riders, blended):
    """Step 3 only: single race-level stage_type → score per rider.

    Returns (scores, stage_type_used); scores is None when the type has no
    weight vector.
    """
    stype = one_day_stage_type(race)
    weights = TYPE_WEIGHTS.get(stype)
    if weights is None:
        return None, stype
    return [weighted_score(b, weights) for b in blended], stype


def score_stage_race(race, blended):
    """
    Score every stage of a stage race independently against its own stage_type,
    plus an aggregated overall-GC score (Step 4).

    Returns (stages_meta, per_stage_probs, gc_probs), or None if no usable
    stages:
      - stages_meta:     list of {stage, stage_name, stage_type}
      - per_stage_probs: list (one per stage) of win% lists across riders
                         (each stage's list sums to ~100)
      - gc_probs:        win% list across riders for the overall GC ranking
    """
    stages = [s for s in race.get("stages", []) if s.get("stage_type") in TYPE_WEIGHTS]
    if not stages:
        return None

    n = len(blended)
    # Raw weighted score per rider, per stage.
    stage_scores = [
        [weighted_score(blended[i], TYPE_WEIGHTS[s["stage_type"]]) for i in range(n)]
        for s in stages
    ]
    # Each stage's probabilities: softmax across riders for that stage alone, so
    # a mountain stage favours climbers, a sprint stage favours sprinters.
    per_stage_probs = [softmax_probabilities(scores) for scores in stage_scores]

    # Overall GC (Step 4): 0.6*gc_percentile + 0.4*percentile(mean per-stage
    # score). The stage-mean is percentile-normalised onto [0,1] so the
    # GC_AGG / STAGE_AGG split is an honest mix.
    stage_means = [mean(stage_scores[s][i] for s in range(len(stages))) for i in range(n)]
    stage_mean_norm = percentile_ranks(stage_means)
    gc_norm = [blended[i]["gc"] for i in range(n)]  # already a percentile in [0,1]
    gc_final = [GC_AGG_WEIGHT * gc_norm[i] + STAGE_AGG_WEIGHT * stage_mean_norm[i]
                for i in range(n)]
    gc_probs = softmax_probabilities(gc_final)

    stages_meta = [
        {"stage": idx + 1, "stage_name": s.get("stage_name"), "stage_type": s["stage_type"]}
        for idx, s in enumerate(stages)
    ]
    return stages_meta, per_stage_probs, gc_probs


def _method_block():
    return {
        "normalisation": "percentile",
        "probability": "softmax",
        "softmax_temperature": SOFTMAX_TEMPERATURE,
        "blend": "career_only",
        "career_recent_weights": [CAREER_WEIGHT, RECENT_WEIGHT],
        "gc_stage_weights": [GC_AGG_WEIGHT, STAGE_AGG_WEIGHT],
    }


def predict_race(client, race):
    """
    Produce the prediction payload for one race, or None if it can't be scored
    (no startlist, or no scoreable stage type).

    One-day races: a single rider list with `win_probability` (unchanged shape).
    Stage races:   per-stage win% (aligned `stage_win` arrays) PLUS an overall
                   GC win% (`gc_win` / `gc_rank`), with `stages_meta` describing
                   each stage.
    """
    slug = race["slug"]
    startlist = db.get_document(client, db.KIND_STARTLIST, slug)
    if not startlist:
        return None

    riders = startlist.get("riders", [])

    # Riders with no PCS chart (career == null) are listed but not scored, and
    # are excluded from the percentile baseline — "no data" ≠ "zero points".
    def has_career(r):
        sp = r.get("specialties") or {}
        return isinstance(sp.get("career"), dict)

    scored_riders = [r for r in riders if has_career(r)]
    if not scored_riders:
        return None

    blended = build_blended(scored_riders)
    unscored_riders = [r for r in riders if not has_career(r)]

    base = {
        "race": race.get("name"),
        "race_slug": slug,
        "is_one_day_race": bool(race.get("is_one_day_race")),
        "model": MODEL_LABEL,
        "method": _method_block(),
        "updated_at": datetime.now().isoformat(),
        "scored_rider_count": len(scored_riders),
        "unscored_rider_count": len(unscored_riders),
    }

    if race.get("is_one_day_race"):
        scores, stype = score_one_day(race, scored_riders, blended)
        if scores is None:
            return None
        base["stage_type"] = stype          # effective type (may be promoted to cobbles)
        probs = softmax_probabilities(scores)
        scored_out = [{
            "name": r.get("name"),
            "team": r.get("team"),
            "rider_url": r.get("rider_url"),
            "score": round(scores[i], 4),
            "win_probability": round(probs[i], 2),
            "specialties_available": True,
        } for i, r in enumerate(scored_riders)]
        scored_out.sort(key=lambda x: x["win_probability"], reverse=True)
        for rank, row in enumerate(scored_out, 1):
            row["rank"] = rank
        unscored_out = [{
            "name": r.get("name"),
            "team": r.get("team"),
            "rider_url": r.get("rider_url"),
            "score": None,
            "win_probability": 0.0,
            "specialties_available": False,
            "rank": None,
        } for r in unscored_riders]
        base["riders"] = scored_out + unscored_out
        return base

    # Stage race: per-stage win% + overall GC.
    result = score_stage_race(race, blended)
    if result is None:
        return None
    stages_meta, per_stage_probs, gc_probs = result
    n_stages = len(stages_meta)

    # Order scored riders by GC win% (desc) for a stable, readable file.
    order = sorted(range(len(scored_riders)), key=lambda i: -gc_probs[i])
    scored_out = []
    for rank, i in enumerate(order, 1):
        r = scored_riders[i]
        scored_out.append({
            "name": r.get("name"),
            "team": r.get("team"),
            "rider_url": r.get("rider_url"),
            "specialties_available": True,
            "gc_win": round(gc_probs[i], 2),
            "gc_rank": rank,
            "stage_win": [round(per_stage_probs[s][i], 2) for s in range(n_stages)],
        })
    unscored_out = [{
        "name": r.get("name"),
        "team": r.get("team"),
        "rider_url": r.get("rider_url"),
        "specialties_available": False,
        "gc_win": 0.0,
        "gc_rank": None,
        "stage_win": None,
    } for r in unscored_riders]

    base["stages_meta"] = stages_meta
    base["riders"] = scored_out + unscored_out
    return base


def main():
    client = db.open_db()
    log.info(f"Predictions store: {'remote Turso' if db.is_remote() else 'local SQLite file'}")

    races = list(db.get_all_documents(client, db.KIND_RACE).values())
    written = 0
    skipped = 0
    for race in races:
        pred = predict_race(client, race)
        if pred is None:
            skipped += 1
            continue
        db.put_document(client, db.KIND_PREDICTIONS, race["slug"], pred)
        written += 1

    client.close()
    log.info(f"Predictions written: {written}  ·  skipped (no startlist/stages): {skipped}")
    print("\n" + "=" * 56)
    print(f"  R2 Phase 3 — predictions ({MODEL_LABEL})")
    print(f"  Written: {written}   Skipped: {skipped}")
    print("=" * 56)


if __name__ == "__main__":
    main()
