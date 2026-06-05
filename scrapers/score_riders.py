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

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RACES_FILE = DATA_DIR / "races.json"
STARTLISTS_DIR = DATA_DIR / "startlists"
PREDICTIONS_DIR = DATA_DIR / "predictions"
PREDICTIONS_INDEX = DATA_DIR / "predictions_index.json"

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
# `cobbles` is deferred to R4 (curated set overlays the type at scoring time).
# `sprint_break` has no row in the original design table (it predates the
# 5-way stage_type split) — this is a fresh starting guess: a sprinter who
# survives / breakaway profile sitting between Sprint and Hilly/puncheur.
# ---------------------------------------------------------------------------
TYPE_WEIGHTS = {
    "sprint":         {"one_day_races": 0.1, "gc": 0.0, "tt": 0.0, "sprint": 1.0, "climber": 0.0, "hills": 0.2},
    "sprint_break":   {"one_day_races": 0.3, "gc": 0.0, "tt": 0.0, "sprint": 0.6, "climber": 0.1, "hills": 0.5},
    "hills_puncheur": {"one_day_races": 0.4, "gc": 0.1, "tt": 0.0, "sprint": 0.2, "climber": 0.2, "hills": 1.0},
    "climber":        {"one_day_races": 0.2, "gc": 0.5, "tt": 0.0, "sprint": 0.0, "climber": 1.0, "hills": 0.3},
    "time_trial":     {"one_day_races": 0.0, "gc": 0.3, "tt": 1.0, "sprint": 0.0, "climber": 0.0, "hills": 0.0},
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


def score_one_day(race, scored_riders, blended):
    """Step 3 only: single race-level stage_type → score per rider."""
    weights = TYPE_WEIGHTS.get(race.get("stage_type"))
    if weights is None:
        return None
    return [weighted_score(b, weights) for b in blended]


def score_stage_race(race, scored_riders, blended):
    """
    Step 3 per stage + Step 4 aggregation. Returns a final score per rider, or
    None if the race has no usable stages.
    """
    stages = [s for s in race.get("stages", []) if s.get("stage_type") in TYPE_WEIGHTS]
    if not stages:
        return None

    n = len(scored_riders)
    # Per-stage score per rider, then the mean across stages.
    stage_means = []
    for i in range(n):
        per_stage = [weighted_score(blended[i], TYPE_WEIGHTS[s["stage_type"]]) for s in stages]
        stage_means.append(mean(per_stage))

    # Put the stage-mean on the same [0, 1] scale as gc_norm before blending,
    # so the GC_AGG / STAGE_AGG split is an honest mix and not dominated by the
    # larger-magnitude weighted sum.
    stage_mean_norm = percentile_ranks(stage_means)
    gc_norm = [b["gc"] for b in blended]  # already a percentile in [0, 1]

    return [GC_AGG_WEIGHT * gc_norm[i] + STAGE_AGG_WEIGHT * stage_mean_norm[i]
            for i in range(n)]


def predict_race(race):
    """
    Produce the prediction payload for one race, or None if it can't be scored
    (no startlist, or no scoreable stage type).
    """
    slug = race["slug"]
    sl_file = STARTLISTS_DIR / f"{slug}.json"
    if not sl_file.exists():
        return None

    startlist = json.loads(sl_file.read_text(encoding="utf-8"))
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

    if race.get("is_one_day_race"):
        scores = score_one_day(race, scored_riders, blended)
    else:
        scores = score_stage_race(race, scored_riders, blended)
    if scores is None:
        return None

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

    # Append unscored riders (no career data) at the end, prob 0.
    unscored_out = [{
        "name": r.get("name"),
        "team": r.get("team"),
        "rider_url": r.get("rider_url"),
        "score": None,
        "win_probability": 0.0,
        "specialties_available": False,
        "rank": None,
    } for r in riders if not has_career(r)]

    return {
        "race": race.get("name"),
        "race_slug": slug,
        "is_one_day_race": bool(race.get("is_one_day_race")),
        "model": MODEL_LABEL,
        "method": {
            "normalisation": "percentile",
            "probability": "softmax",
            "softmax_temperature": SOFTMAX_TEMPERATURE,
            "blend": "career_only",
            "career_recent_weights": [CAREER_WEIGHT, RECENT_WEIGHT],
            "gc_stage_weights": [GC_AGG_WEIGHT, STAGE_AGG_WEIGHT],
        },
        "updated_at": datetime.now().isoformat(),
        "scored_rider_count": len(scored_out),
        "unscored_rider_count": len(unscored_out),
        "riders": scored_out + unscored_out,
    }


def main():
    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
    data = json.loads(RACES_FILE.read_text(encoding="utf-8"))

    index = {}
    written = 0
    skipped = 0
    for race in data.get("races", []):
        pred = predict_race(race)
        slug = race["slug"]
        if pred is None:
            skipped += 1
            index[slug] = {"name": race.get("name"), "prediction_available": False}
            continue
        out_file = PREDICTIONS_DIR / f"{slug}.json"
        out_file.write_text(json.dumps(pred, indent=2, ensure_ascii=False), encoding="utf-8")
        written += 1
        index[slug] = {
            "name": race.get("name"),
            "prediction_available": True,
            "is_one_day_race": pred["is_one_day_race"],
            "scored_rider_count": pred["scored_rider_count"],
        }

    PREDICTIONS_INDEX.write_text(
        json.dumps({
            "updated_at": datetime.now().isoformat(),
            "year": data.get("year"),
            "model": MODEL_LABEL,
            "races": index,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    log.info(f"Predictions written: {written}  ·  skipped (no startlist/stages): {skipped}")
    print("\n" + "=" * 56)
    print(f"  R2 Phase 3 — predictions ({MODEL_LABEL})")
    print(f"  Written: {written}   Skipped: {skipped}")
    print("=" * 56)


if __name__ == "__main__":
    main()
