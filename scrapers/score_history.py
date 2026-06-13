#!/usr/bin/env python3
"""
Results-based rider rating model (the locked design — see the
project_results_scoring_model memory). Pure logic, no scraping.

Reads:  race_data kind="results" (built by scrape_history.py) from the store.
Writes: caches row "rider_ratings" — per rider, a 0-100 score per category.

Categories (5 specialties): mountain, hilly, sprint, TT, prologue
  per finished result:  percentile = rank / finishers   (lower = better)
  rolling-window recency by each result's DATE (per-result weight): last 12mo
      ×1.0, 12-24mo ×0.5, 24-36mo ×0.25, older dropped
      (one-day races get their date from data/oneday_dates.json)
  SYMMETRIC trimmed mean: drop each rider's best TRIM_FRAC + worst TRIM_FRAC of
      results, weighted mean of the middle
  per-category min results (MIN_RESULTS) else UNRATED
  score = aggregated percentile × 100 = "top X%" (lower = better). NO min-max
      rescale — only `break` keeps min-max (it blends a rate with a placing).

Category "break" (two factors, combined α/1-α; α=0.5):
  road stages only (sprint/hilly/mountain); "in break" = breakaway_kms > 0
  F1 propensity   = #in-break / #road-started   → min-max NON-inverted → Score1
  F2 placing      = percentile on (break won ∧ in break) → min-max INVERTED → Score2
  break = α·Score1 + (1-α)·Score2 ; an absent factor contributes 0
  eligibility: ≥MIN_BREAK in-break stages (F1), ≥MIN_BREAK break-win stages (F2)

The "break won" stage flag is DERIVED from won_how (breakaway_kms alone
under-detects) — see break_won().

Usage:
  python scrapers/score_history.py
"""

import json
import logging
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import db  # local module: Turso/SQLite store

# Rider names carry accents (č, é…) the Windows cp1252 console can't print.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_RIDER_RATINGS = "rider_ratings"

SPECIALTY_CATEGORIES = ["mountain", "hilly", "sprint", "TT", "prologue"]
ROAD_TYPES = {"sprint", "hilly", "mountain"}   # break factor-1 denominator

# Rolling-window recency (by each result's DATE): 12-month buckets over the last
# 3 years. Bucket 0 = last 12 months ×1.0, bucket 1 = 12-24mo ×0.5, bucket 2 =
# 24-36mo ×0.25; older than 36 months dropped.
WINDOW_MONTHS = 36
BUCKET_WEIGHTS = {0: 1.0, 1: 0.5, 2: 0.25}
# Minimum finished results in a specialty over the 3-year window to be rated.
# PER-CATEGORY: stage types differ hugely in frequency — many sprint/mountain
# stages per season, but only a handful of ITTs and very few prologues. A flat
# threshold (e.g. 10) would empty the rare categories (prologue) and drop genuine
# elites (Pogačar/Tarling have <10 ITTs in 3 years).
MIN_RESULTS = {"mountain": 10, "hilly": 5, "sprint": 10, "TT": 3, "prologue": 1}
MIN_BREAK = 3   # break factors: F1 (#in-break stages), F2 (#break-win-with-rider)
ALPHA = 0.5     # break = ALPHA*propensity + (1-ALPHA)*placing
# Symmetric trimmed mean: drop each rider's best TRIM_FRAC and worst TRIM_FRAC of
# results (per specialty) before averaging, removing both flukey peaks and
# off-days. Floor → small samples (<10 with 0.10) keep everything.
TRIM_FRAC = 0.10


# --------------------------------------------------------------------------- #
# won_how → "did the breakaway win?" (Factor 2 stage flag)
# --------------------------------------------------------------------------- #
# Bunch/peloton finishes (NOT a break win). Checked first.
_BUNCH_RE = re.compile(r"large group|bunch|peloton", re.IGNORECASE)
# Break wins: solo, small group, "Sprint of N riders", "à deux/trois/quatre".
_BREAK_RE = re.compile(r"\bsolo\b|small group|sprint of \d+|à\s+(deux|trois|quatre)",
                       re.IGNORECASE)


def break_won(won_how):
    """Derive whether the breakaway decided the stage from PCS's won_how string.
    Conservative: unknown/ambiguous ('Time trial', 'Other', empty) → False."""
    if not won_how:
        return False
    if _BUNCH_RE.search(won_how):
        return False
    return bool(_BREAK_RE.search(won_how))


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def recency_bucket(date_str, ref_date):
    """Which rolling-window bucket a result falls in by its date: 0 = last 12
    months, 1 = months 12-24, None = older than 24 months or undated."""
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    months = (ref_date - d).days / 30.4375
    if months < 12:        # includes any small future/negative (recent)
        return 0
    if months < 24:
        return 1
    if months < WINDOW_MONTHS:
        return 2
    return None


def weighted_mean(by_bucket):
    """Recency-weighted mean of {bucket: value} using BUCKET_WEIGHTS, divided by
    the SUM OF PRESENT weights. None if no usable bucket."""
    num = den = 0.0
    for b, v in by_bucket.items():
        w = BUCKET_WEIGHTS.get(b)
        if w is None:
            continue
        num += w * v
        den += w
    return num / den if den > 0 else None


def trimmed_weighted_mean(weighted_vals):
    """weighted_vals: list of (value, weight). Drop the best TRIM_FRAC and worst
    TRIM_FRAC of results (by value; floor → small samples keep all), then
    weighted mean of the middle. Recency lives in the per-result weights."""
    if not weighted_vals:
        return None
    ordered = sorted(weighted_vals, key=lambda x: x[0])   # ascending value
    k = int(len(ordered) * TRIM_FRAC)
    kept = ordered[k:len(ordered) - k] if k else ordered
    num = sum(w * v for v, w in kept)
    den = sum(w for v, w in kept)
    return num / den if den > 0 else None


def minmax_rescale(by_rider, invert):
    """Map {rider: value} onto 0-100 by min-max. invert=True → lowest value
    becomes 100 (for 'lower percentile is better'). Degenerate (all equal) →
    everyone 100."""
    if not by_rider:
        return {}
    vals = list(by_rider.values())
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return {r: 100.0 for r in by_rider}
    out = {}
    for r, v in by_rider.items():
        frac = (v - lo) / (hi - lo)
        out[r] = 100.0 * (1.0 - frac) if invert else 100.0 * frac
    return out


def _finishers(stage):
    """Stored finishers count, falling back to a DF tally."""
    n = stage.get("finishers")
    if n:
        return n
    return sum(1 for r in stage.get("results", []) if r.get("status") == "DF")


def _is_finish(r):
    """A scored finish: status DF with a usable integer rank."""
    rank = r.get("rank")
    return r.get("status") == "DF" and isinstance(rank, int) and rank > 0


# --------------------------------------------------------------------------- #
# Core computation (pure — takes the {slug: results-doc} dict)
# --------------------------------------------------------------------------- #
def compute_ratings(docs, ref_date=None, oneday_dates=None):
    """Build the rider-ratings payload from kind='results' docs. Pure: no I/O.

    ref_date: window anchor (default today). oneday_dates: {slug: 'YYYY-MM-DD'}
    supplying dates for one-day races (their stages store date=None)."""
    if not docs:
        return _payload(None, [])
    ref_date = ref_date or date.today()
    oneday_dates = oneday_dates or {}

    names = {}
    # specialty: val[cat][rider] -> [(percentile, bucket_weight)]; n_cat count
    val = defaultdict(lambda: defaultdict(list))
    n_cat = defaultdict(lambda: defaultdict(int))
    # break F1: per rider per bucket, road-started & in-break counts
    started_road = defaultdict(lambda: defaultdict(int))
    in_break = defaultdict(lambda: defaultdict(int))
    n_break = defaultdict(int)
    # break F2: [(percentile, bucket_weight)] on (break won ∧ in break) finishes
    f2 = defaultdict(list)
    n_break_win = defaultdict(int)

    for doc in docs.values():
        if doc.get("pcs_slug") in db.EXCLUDE_RESULT_PCS_SLUGS:
            continue  # women's / non-men's race — never score it
        oneday_date = oneday_dates.get(doc.get("race_slug"))
        for st in doc.get("stages", []):
            cat = st.get("stage_type")
            fin = _finishers(st)
            if not fin:
                continue
            bwon = break_won(st.get("won_how"))
            is_road = cat in ROAD_TYPES
            # date: stage date for stage races, else the one-day race date.
            bucket = recency_bucket(st.get("date") or oneday_date, ref_date)
            if bucket is None:
                continue  # outside the 3-year window (or undated)
            w = BUCKET_WEIGHTS[bucket]
            for r in st.get("results", []):
                ru = r.get("rider_url")
                if not ru:
                    continue
                if r.get("rider_name"):
                    names[ru] = r["rider_name"]
                status = r.get("status")
                in_brk = (r.get("breakaway_kms") or 0) > 0

                # percentile = rank / finishers (finished only); lower = better.
                pct = r["rank"] / fin if _is_finish(r) else None

                if pct is not None and cat in SPECIALTY_CATEGORIES:
                    val[cat][ru].append((pct, w))
                    n_cat[cat][ru] += 1

                if is_road and status != "DNS":
                    started_road[ru][bucket] += 1
                    if in_brk:
                        in_break[ru][bucket] += 1
                        n_break[ru] += 1

                if pct is not None and bwon and in_brk:
                    f2[ru].append((pct, w))
                    n_break_win[ru] += 1

    # Specialty scores
    cat_scores = {}
    for cat in SPECIALTY_CATEGORIES:
        raw = {}
        for ru, wv in val[cat].items():
            if n_cat[cat][ru] < MIN_RESULTS[cat]:
                continue
            s = trimmed_weighted_mean(wv)
            if s is not None:
                raw[ru] = s
        # No min-max rescale: the score IS the aggregated percentile (lower =
        # better), exposed below as "top X%". Only break keeps min-max (it has
        # to blend a participation rate with a placing).
        cat_scores[cat] = raw

    # Break factor 1 — propensity
    f1_raw = {}
    for ru, by_bucket in started_road.items():
        if n_break[ru] < MIN_BREAK:
            continue
        ratios = {b: in_break[ru].get(b, 0) / by_bucket[b]
                  for b in by_bucket if by_bucket[b] > 0}
        f = weighted_mean(ratios)
        if f is not None:
            f1_raw[ru] = f
    score1 = minmax_rescale(f1_raw, invert=False)

    # Break factor 2 — placing when the break wins
    f2_raw = {}
    for ru, wv in f2.items():
        if n_break_win[ru] < MIN_BREAK:
            continue
        s = trimmed_weighted_mean(wv)
        if s is not None:
            f2_raw[ru] = s
    score2 = minmax_rescale(f2_raw, invert=True)   # lower percentile = better

    # Combine (absent factor = 0)
    break_score = {}
    for ru in set(score1) | set(score2):
        break_score[ru] = ALPHA * score1.get(ru, 0.0) + (1 - ALPHA) * score2.get(ru, 0.0)

    # Assemble per-rider rows (drop riders with no rating at all). Specialty
    # score = aggregated percentile × 100 = "top X%" (lower = better).
    riders = []
    for ru in sorted(names):
        scores = {c: round(cat_scores[c][ru] * 100, 2) for c in SPECIALTY_CATEGORIES
                  if ru in cat_scores[c]}
        b = round(break_score[ru], 2) if ru in break_score else None
        if not scores and b is None:
            continue
        riders.append({
            "rider_url": ru,
            "name": names[ru],
            "scores": {**{c: scores.get(c) for c in SPECIALTY_CATEGORIES}, "break": b},
            "n_results": {**{c: n_cat[c].get(ru, 0) for c in SPECIALTY_CATEGORIES},
                          "break_in": n_break.get(ru, 0),
                          "break_win": n_break_win.get(ru, 0)},
        })

    return _payload(ref_date, riders)


def _payload(ref_date, riders):
    """Assemble the ratings payload with consistent metadata (used for both the
    normal and the empty-store path, so callers always get the same keys)."""
    return {
        "ref_date": ref_date.isoformat() if ref_date else None,
        "window_months": WINDOW_MONTHS,
        "bucket_weights": {"0-12mo": BUCKET_WEIGHTS[0], "12-24mo": BUCKET_WEIGHTS[1],
                           "24-36mo": BUCKET_WEIGHTS[2]},
        "alpha": ALPHA,
        "min_results": MIN_RESULTS,
        "min_break": MIN_BREAK,
        "rider_count": len(riders),
        "riders": riders,
        "generated_at": datetime.now().isoformat(),
    }


def _load_oneday_dates():
    path = DATA_DIR / "oneday_dates.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    log.warning("data/oneday_dates.json missing — one-day races will be undated "
                "and dropped. Run enrich_oneday_dates.py first.")
    return {}


def main():
    # connect() (not open_db): no CREATE TABLE, so a READ-ONLY Turso token works
    # — the tables already exist (scrape_history created them).
    client = db.connect()
    log.info(f"Ratings store: {'remote Turso' if db.is_remote() else 'local SQLite file'}")
    docs = db.get_all_documents(client, db.KIND_RESULTS)
    log.info(f"Loaded {len(docs)} results docs")
    payload = compute_ratings(docs, oneday_dates=_load_oneday_dates())

    # Local JSON — what the dashboard reads.
    out = DATA_DIR / "rider_ratings.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Wrote {out}")

    # Best-effort: also cache in Turso. Skipped if the token is read-only.
    try:
        db.put_cache(client, CACHE_RIDER_RATINGS, payload)
    except Exception as e:  # noqa: BLE001 — read-only token / transient write error
        log.warning(f"Turso cache write skipped ({e})")
    client.close()

    rated = payload["rider_count"]
    log.info(f"Rated {rated} riders (ref date {payload['ref_date']})")
    print("\n" + "=" * 56)
    print(f"  RIDER RATINGS  (ref {payload['ref_date']}, "
          f"window {payload['window_months']} months)")
    print(f"  Riders rated: {rated}")
    print_top(payload)
    print("=" * 56)


def print_top(payload, n=10):
    """Eyeball check: top N riders per category. Specialties = top X% (lower is
    better); break = 0-100 (higher is better)."""
    riders = payload["riders"]
    for cat in SPECIALTY_CATEGORIES + ["break"]:
        lower_better = cat != "break"
        ranked = sorted(
            (r for r in riders if r["scores"].get(cat) is not None),
            key=lambda r: r["scores"][cat], reverse=not lower_better)[:n]
        unit = "%" if lower_better else ""
        print(f"\n  — {cat} —")
        for r in ranked:
            print(f"    {r['scores'][cat]:6.2f}{unit}  {r['name']}")


if __name__ == "__main__":
    main()
