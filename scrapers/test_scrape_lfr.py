#!/usr/bin/env python3
"""
No-network logic tests for scrape_lfr.py (the LFR GPX fallback).

Covers the pure parsing/matching helpers against synthetic HTML — the live LFR
integration can only be validated by running the scraper locally, but the logic
that turns LFR pages into track ids + race matches is asserted here.
Run:  python scrapers/test_scrape_lfr.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_lfr as lfr


def test_normalize_name_strips_accents_and_year():
    assert lfr.normalize_name("Critérium du Dauphiné 2026") == "criterium du dauphine"
    assert lfr.normalize_name("Vuelta a España") == "vuelta a espana"


def test_name_match_score_high_for_same_race():
    assert lfr.name_match_score("Tour de Suisse", "Tour de Suisse 2026") == 1.0
    assert lfr.name_match_score("Il Lombardia", "Giro di Lombardia") > 0.3


def test_name_match_score_low_for_different_races():
    assert lfr.name_match_score("Paris-Tours", "Tour Down Under") < 0.3


def _cal_race(day, rid, name, meta):
    return (f'<td class="day"><div class="day__header">'
            f'<div class="day__header__day">{day}</div></div><div class="day__body">'
            f'<a href="/maps/races/view/2026/{rid}"><div class="race"><div class="race__info">'
            f'<div class="race__name">{name}</div>'
            f'<div class="race__meta"> {meta} <img></div></div></div></a></div></td>')


def test_parse_calendar_extracts_name_meta_and_earliest_date():
    # Real LFR markup: month grid of <td class="day"> cells; a multi-day race
    # repeats across days; spill-over days carry day--anotherMonth (skipped).
    html = ("<table><tr>"
            + _cal_race(4, 1, "Tour de France", "2.UWT - ME -")
            + _cal_race(5, 1, "Tour de France", "2.UWT - ME -")   # same race, later day
            + _cal_race(5, 445, "Tour of Britain Women", "2.WWT - WE -")
            + '<td class="day day--anotherMonth"><div class="day__header">'
              '<div class="day__header__day">31</div></div><div class="day__body">'
              '<a href="/maps/races/view/2026/99"><div class="race__name">Spillover</div></a>'
              '</div></td>'
            + "</tr></table>")
    out = {c["race_id"]: c for c in lfr.parse_calendar(html, 2026, 7)}
    assert set(out) == {1, 445}                          # anotherMonth (99) skipped
    assert out[1]["name"] == "Tour de France"
    assert out[1]["date"] == "2026-07-04"                # earliest day kept, not 5
    assert out[1]["uci_class"] == "2.UWT" and out[1]["gender"] == "ME"
    assert out[445]["gender"] == "WE"
    assert out[1]["view_url"].endswith("/maps/races/view/2026/1")


def test_match_in_calendar_prefers_exact_date_then_name():
    pool = [
        {"race_id": 1, "name": "Tour de France", "date": "2026-07-04"},
        {"race_id": 2, "name": "Tour de Pologne", "date": "2026-08-03"},
    ]
    # Unique same-date hit with a sane name wins outright.
    m = lfr.match_in_calendar({"name": "Tour de France", "startdate": "2026-07-04"}, pool)
    assert m and m["race_id"] == 1 and m["score"] == 1.0
    # No date hit → fall back to name score across the pool.
    m2 = lfr.match_in_calendar({"name": "Tour de Pologne", "startdate": "2099-01-01"}, pool)
    assert m2 and m2["race_id"] == 2
    # No date hit and weak name → None.
    assert lfr.match_in_calendar({"name": "Surf Carnival", "startdate": "2099-01-01"}, pool) is None


def test_match_in_calendar_rejects_same_date_wrong_name():
    # Two of OUR races share a start date but only one is on LFR. The other must
    # NOT inherit it just because the date lines up (the wrong-route guard).
    pool = [{"race_id": 37, "name": "Renewi Tour", "date": "2026-08-19"}]
    good = lfr.match_in_calendar({"name": "Renewi Tour", "startdate": "2026-08-19"}, pool)
    assert good and good["race_id"] == 37
    bad = lfr.match_in_calendar({"name": "Deutschland Tour", "startdate": "2026-08-19"}, pool)
    assert bad is None, bad


def test_best_race_match_picks_above_threshold():
    cands = [
        {"race_id": 1, "name": "Tour Down Under"},
        {"race_id": 2, "name": "Tour de Suisse"},
    ]
    m = lfr.best_race_match("Tour de Suisse", cands)
    assert m and m["race_id"] == 2 and m["score"] >= 0.34, m


def test_best_race_match_returns_none_when_weak():
    cands = [{"race_id": 1, "name": "Surf Lifesaving Championship"}]
    assert lfr.best_race_match("Tour de Suisse", cands) is None


def test_parse_stage_tracks_ordered_dedup():
    html = """
      <a href="/maps/viewtrack/1001?sid=ab">Stage 1</a>
      <a href="/maps/viewtrack/hd/1002">Stage 2 HD</a>
      <a href="/maps/viewtrack/1001">Stage 1 again</a>
      <a href="/maps/viewtrack/1003">Stage 3</a>"""
    assert lfr.parse_stage_tracks(html) == [1001, 1002, 1003]


def test_stage_filename_oneday_vs_stage():
    assert lfr.stage_filename(1, 1, True) == "route.gpx"
    assert lfr.stage_filename(1, 1, False) == "route.gpx"      # single track → route
    assert lfr.stage_filename(3, 8, False) == "stage-3-route.gpx"


def test_looks_like_gpx_accepts_xml_rejects_junk():
    assert lfr.looks_like_gpx("<?xml version='1.0'?><gpx>" + "x" * 200)
    assert lfr.looks_like_gpx("<GPX " + "y" * 200)             # case-insensitive
    assert not lfr.looks_like_gpx("")
    assert not lfr.looks_like_gpx(None)
    assert not lfr.looks_like_gpx("too short")                 # < 100 chars
    assert not lfr.looks_like_gpx("<html>" + "n" * 200)        # not GPX/XML


def test_targets_filters_to_missing_wt_proseries():
    races = [
        {"slug": "a-2026", "uci_tour": "1.UWT"},   # missing → include
        {"slug": "b-2026", "uci_tour": "2.UWT"},   # already has gpx → skip
        {"slug": "c-2026", "uci_tour": "2.1"},     # wrong class → skip
        {"slug": "d-2026", "uci_tour": "1.Pro"},   # missing → include
        {"slug": "e-2026", "uci_tour": "2.Pro"},   # already stored → skip
    ]
    # has_gpx predicate stands in for db.has_gpx (keeps targeting db-free).
    have = {"b-2026", "e-2026"}
    has_gpx = lambda slug: slug in have
    got = [r["slug"] for r in lfr.targets(races, has_gpx, only=None)]
    assert got == ["a-2026", "d-2026"], got
    # --only narrows
    got1 = [r["slug"] for r in lfr.targets(races, has_gpx, only="d-2026")]
    assert got1 == ["d-2026"], got1


def test_targets_filters_by_start_date():
    import datetime
    cutoff = datetime.date(2026, 6, 13)
    races = [
        {"slug": "past-2026", "uci_tour": "1.UWT", "startdate": "2026-04-19"},  # before → skip
        {"slug": "today-2026", "uci_tour": "1.Pro", "startdate": "2026-06-12"}, # before → skip
        {"slug": "cut-2026", "uci_tour": "1.Pro", "startdate": "2026-06-13"},   # on cutoff → keep
        {"slug": "future-2026", "uci_tour": "2.UWT", "startdate": "2026-08-01"},# after → keep
        {"slug": "nodate-2026", "uci_tour": "1.Pro"},                           # missing → skip
    ]
    has_gpx = lambda s: False
    got = [r["slug"] for r in lfr.targets(races, has_gpx, start_on_or_after=cutoff)]
    assert got == ["cut-2026", "future-2026"], got
    # --only overrides the date window (explicit pick of a past race still resolves)
    got1 = [r["slug"] for r in lfr.targets(races, has_gpx, only="past-2026",
                                           start_on_or_after=cutoff)]
    assert got1 == ["past-2026"], got1


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()
