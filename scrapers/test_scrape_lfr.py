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


def test_parse_race_listing_extracts_view_links():
    # Real LFR markup: name in a sibling cell's <strong>, map link is
    # /maps/races/view/{year}/{id} with only an icon inside.
    html = """
      <table>
        <tr class="displayRaceLine">
          <td><div class="displayRaceLine__logo"><strong>Tour de Suisse</strong></div></td>
          <td><a href="/maps/races/view/2026/777"><h4 class="icon"></h4></a></td>
        </tr>
        <tr class="displayRaceLine">
          <td><div class="displayRaceLine__logo"><strong>Il Lombardia</strong></div></td>
          <td><a href="/maps/races/view/2026/901"><h4 class="icon"></h4></a></td>
        </tr>
        <tr class="displayRaceLine">
          <td><div class="displayRaceLine__logo"><strong>dup row</strong></div></td>
          <td><a href="/maps/races/view/2026/777"><h4 class="icon"></h4></a></td>
        </tr>
        <tr><td><a href="/something/else">noise</a></td></tr>
      </table>"""
    rows = lfr.parse_race_listing(html)
    assert [r["race_id"] for r in rows] == [777, 901], rows
    assert rows[0]["name"] == "Tour de Suisse"
    assert rows[0]["view_url"].endswith("/maps/races/view/2026/777")


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
