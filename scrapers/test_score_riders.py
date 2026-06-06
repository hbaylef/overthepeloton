#!/usr/bin/env python3
"""
No-network logic tests for score_riders.py — focused on the maths (percentile,
softmax, weighted score) and the R4 cobbles tie-in (a curated cobbles file
promotes a one-day race to the `cobbles` weight vector).

Run:  python scrapers/test_score_riders.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_riders as sr

SPECS = sr.SPECIALTIES


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_cobbles_weight_vector_exists_and_well_formed():
    w = sr.TYPE_WEIGHTS["cobbles"]
    assert set(w.keys()) == set(SPECS), w
    # flat pavé → no climbing / GC; classics pedigree dominant
    assert w["climber"] == 0.0 and w["gc"] == 0.0
    assert w["one_day_races"] >= max(w["sprint"], w["tt"], w["hills"])


def test_percentile_ranks_ties_and_extremes():
    assert sr.percentile_ranks([]) == []
    assert sr.percentile_ranks([42]) == [0.5]
    assert sr.percentile_ranks([5, 5, 5]) == [0.5, 0.5, 0.5]   # all equal → neutral
    r = sr.percentile_ranks([10, 20, 30])
    assert r[0] == 0.0 and r[2] == 1.0 and approx(r[1], 0.5)
    # ties share the midrank
    r2 = sr.percentile_ranks([1, 2, 2, 3])
    assert approx(r2[1], r2[2])


def test_softmax_sums_100_and_sharpens():
    probs = sr.softmax_probabilities([0.0, 0.5, 1.0])
    assert approx(sum(probs), 100.0, 1e-6)
    assert probs[2] > probs[1] > probs[0]
    # degenerate (all equal) → uniform
    u = sr.softmax_probabilities([3, 3, 3])
    assert all(approx(p, 100.0 / 3) for p in u)
    assert sr.softmax_probabilities([]) == []


def test_weighted_score():
    blended = {s: 0.0 for s in SPECS}
    blended["sprint"] = 1.0
    blended["one_day_races"] = 0.5
    w = sr.TYPE_WEIGHTS["cobbles"]
    expected = w["sprint"] * 1.0 + w["one_day_races"] * 0.5
    assert approx(sr.weighted_score(blended, w), expected)


def test_one_day_type_promotes_to_cobbles_when_file_present(tmp_cobbles):
    sr.COBBLES_DIR = tmp_cobbles
    (tmp_cobbles / "paris-roubaix-2026.json").write_text("{}", encoding="utf-8")
    pr = {"slug": "paris-roubaix-2026", "stage_type": "sprint"}
    other = {"slug": "milano-sanremo-2026", "stage_type": "sprint"}
    assert sr.one_day_stage_type(pr) == "cobbles"      # promoted
    assert sr.one_day_stage_type(other) == "sprint"    # untouched


def test_score_one_day_uses_promoted_type(tmp_cobbles):
    sr.COBBLES_DIR = tmp_cobbles
    (tmp_cobbles / "paris-roubaix-2026.json").write_text("{}", encoding="utf-8")
    riders = [{"specialties": {"career": {s: 0 for s in SPECS}}} for _ in range(3)]
    riders[0]["specialties"]["career"]["sprint"] = 100        # a pure sprinter
    riders[1]["specialties"]["career"]["one_day_races"] = 100 # a classics rider
    blended = sr.build_blended(riders)

    scores, stype = sr.score_one_day({"slug": "paris-roubaix-2026", "stage_type": "sprint"},
                                     riders, blended)
    assert stype == "cobbles"
    # under cobbles, the classics rider (one_day_races) outscores the sprinter
    assert scores[1] > scores[0]


def test_score_one_day_unknown_type_returns_none(tmp_cobbles):
    sr.COBBLES_DIR = tmp_cobbles  # empty → no promotion
    riders = [{"specialties": {"career": {s: 0 for s in SPECS}}} for _ in range(2)]
    blended = sr.build_blended(riders)
    scores, stype = sr.score_one_day({"slug": "x-2026", "stage_type": "team_time_trial"},
                                     riders, blended)
    assert scores is None and stype == "team_time_trial"


def run():
    failed = 0
    real_cobbles = sr.COBBLES_DIR
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        try:
            if "tmp_cobbles" in fn.__code__.co_varnames:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failed += 1; print(f"  ERROR {name}: {type(e).__name__}: {e}")
        finally:
            sr.COBBLES_DIR = real_cobbles   # restore after any monkeypatch
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()
