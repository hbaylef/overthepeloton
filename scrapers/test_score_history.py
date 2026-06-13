#!/usr/bin/env python3
"""
No-network maths tests for score_history.py — validates the rating formula
(percentile, rolling-window recency buckets, per-category min-N, min-max) and
the break 2-factor model, on hand-built results docs.

Run:  python scrapers/test_score_history.py     (or via pytest)
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_history as sh

REF = date(2026, 6, 1)
RECENT = "2026-05-15"   # ~0.5 months before REF → bucket 0 (last 12 months)


def approx(a, b, tol=1e-4):
    return a is not None and b is not None and abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #
def stage(stype, rows, won_how=None, quality=100, date=RECENT):
    """rows = list of (url, name, rank, status, breakaway_kms)."""
    return {
        "stage_type": stype, "won_how": won_how, "startlist_quality": quality,
        "date": date, "finishers": 100,
        "results": [{"rider_url": u, "rider_name": n, "rank": rk,
                     "status": st, "breakaway_kms": bk}
                    for (u, n, rk, st, bk) in rows],
    }


def doc(stages):
    return {"is_one_day_race": False, "race_slug": "x", "stages": stages}


def ratings_by_url(payload):
    return {r["rider_url"]: r for r in payload["riders"]}


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_break_won_classifier():
    assert sh.break_won("12 km solo")
    assert sh.break_won("Sprint of small group")
    assert sh.break_won("Sprint of 5 riders")
    assert sh.break_won("Sprint à deux")
    assert not sh.break_won("Sprint of large group")
    assert not sh.break_won("Time trial")
    assert not sh.break_won(None)


def test_recency_bucket():
    assert sh.recency_bucket("2026-05-01", REF) == 0     # ~1 month → bucket 0
    assert sh.recency_bucket("2025-03-01", REF) == 1     # ~15 months → bucket 1
    assert sh.recency_bucket("2024-04-01", REF) == 2     # ~26 months → bucket 2
    assert sh.recency_bucket("2022-12-01", REF) is None  # ~42 months → dropped
    assert sh.recency_bucket(None, REF) is None          # undated → dropped


def test_weighted_mean_buckets():
    # (1.0*0.10 + 0.5*0.25 + 0.25*0.40) / (1.0+0.5+0.25) = 0.325/1.75
    assert approx(sh.weighted_mean({0: 0.10, 1: 0.25, 2: 0.40}), 0.325 / 1.75)
    # single bucket → divides by its own weight only
    assert approx(sh.weighted_mean({0: 0.2}), 0.2)
    assert approx(sh.weighted_mean({2: 0.2}), 0.2)
    # no usable bucket → None
    assert sh.weighted_mean({}) is None
    assert sh.weighted_mean({3: 0.5}) is None            # bucket 3 has no weight


def test_trimmed_weighted_mean():
    # 10 equal-weight values → drop best 1 + worst 1 (floor(10*0.1)=1) → mean of
    # the middle 8 (values 2..9) = 5.5
    assert approx(sh.trimmed_weighted_mean([(v, 1) for v in range(1, 11)]), 5.5)
    # small sample (n=3) → floor(0.3)=0 → keep all → weighted mean
    assert approx(sh.trimmed_weighted_mean([(0.1, 1.0), (0.2, 0.5), (0.3, 0.25)]),
                  (1.0 * 0.1 + 0.5 * 0.2 + 0.25 * 0.3) / 1.75)
    assert sh.trimmed_weighted_mean([]) is None


def test_minmax_rescale_endpoints_and_degenerate():
    inv = sh.minmax_rescale({"a": 0.1, "b": 0.3}, invert=True)
    assert approx(inv["a"], 100.0) and approx(inv["b"], 0.0)
    nrm = sh.minmax_rescale({"a": 0.1, "b": 0.3}, invert=False)
    assert approx(nrm["a"], 0.0) and approx(nrm["b"], 100.0)
    deg = sh.minmax_rescale({"a": 5.0, "b": 5.0}, invert=True)
    assert approx(deg["a"], 100.0) and approx(deg["b"], 100.0)


# --------------------------------------------------------------------------- #
# Specialty scoring (mountain)
# --------------------------------------------------------------------------- #
def test_specialty_ordering_and_min_n():
    # mountain threshold is 10 → 10 recent mountain stages. A always 1st, B
    # always 80th; C appears once (1 result < 10 → unrated, nothing else → drop).
    stages = []
    for i in range(10):
        rows = [("rider/a", "A", 1, "DF", 0), ("rider/b", "B", 80, "DF", 0)]
        if i == 0:
            rows.append(("rider/c", "C", 5, "DF", 0))
        stages.append(stage("mountain", rows))
    out = ratings_by_url(sh.compute_ratings({"m": doc(stages)}, ref_date=REF))
    # score = aggregated percentile × 100 = "top X%", lower = better.
    assert approx(out["rider/a"]["scores"]["mountain"], 1.0)     # rank1/100 → top 1%
    assert approx(out["rider/b"]["scores"]["mountain"], 80.0)    # rank80/100 → top 80%
    assert "rider/c" not in out                                  # <10 → dropped


# --------------------------------------------------------------------------- #
# Break 2-factor model
# --------------------------------------------------------------------------- #
def test_break_two_factor_and_one_factor_rule():
    docs = {"b": doc([
        # break win: X & Y up front, Z in peloton
        stage("sprint", [("rider/x", "X", 1, "DF", 12),
                         ("rider/y", "Y", 3, "DF", 12),
                         ("rider/z", "Z", 50, "DF", 0)], won_how="12 km solo"),
        # bunch finish: X & W were in the break but caught; Y & Z not
        stage("sprint", [("rider/x", "X", 40, "DF", 30),
                         ("rider/w", "W", 45, "DF", 20),
                         ("rider/y", "Y", 60, "DF", 0),
                         ("rider/z", "Z", 10, "DF", 0)], won_how="Sprint of large group"),
        stage("sprint", [("rider/x", "X", 2, "DF", 50),
                         ("rider/y", "Y", 4, "DF", 50)], won_how="Sprint of 5 riders"),
        stage("sprint", [("rider/x", "X", 1, "DF", 8),
                         ("rider/y", "Y", 2, "DF", 8)], won_how="8 km solo"),
        stage("sprint", [("rider/w", "W", 30, "DF", 25)], won_how="Sprint of large group"),
        stage("sprint", [("rider/w", "W", 20, "DF", 15)], won_how="Sprint of large group"),
    ])}
    out = ratings_by_url(sh.compute_ratings(docs, ref_date=REF))

    # X: top F1 (always in break) AND best placing when break wins → 100
    assert approx(out["rider/x"]["scores"]["break"], 100.0)
    # Y: eligible both factors but lower on both → 0
    assert approx(out["rider/y"]["scores"]["break"], 0.0)
    # W: in breaks (F1 eligible, tied top) but NEVER in a winning break (F2
    #    absent → 0). One-factor rule: 0.5*100 + 0.5*0 = 50
    assert approx(out["rider/w"]["scores"]["break"], 50.0)
    # Z: never in a break, too few sprint results → dropped entirely
    assert "rider/z" not in out


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
