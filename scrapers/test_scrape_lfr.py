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


def _nc_row(rid, name, flag, typ):
    """One LFR calendar-12 listing row (mirrors the real markup)."""
    return (f'<tr class="displayRaceLine"><td>Thu 25 June 2026</td>'
            f'<td><a href="/maps/races?calendar%5B0%5D=12">National Championships</a></td>'
            f'<td><div class="displayRaceLine__logo"><strong>{name}</strong></div></td>'
            f'<td><a href="/maps/races?nations%5B0%5D=2"><img class="flag" '
            f'src="/ext/theme/images/flags/{flag}.png"/></a></td><td>1</td>'
            f'<td><a href="/maps/races?type%5B0%5D=1">{typ}</a></td>'
            f'<td><a href="/maps/races?subclass%5B0%5D=18">CN</a></td>'
            f'<td><a href="/maps/races/view/2026/{rid}"><h4 class="icon"></h4></a></td></tr>')


def test_parse_nc_listing_filters_me_and_target_nations():
    html = "<table>" + "".join([
        _nc_row(112, "French Road National Championship - ITT (Men Elite)", "France", "ME"),
        _nc_row(113, "French Road National Championship (Men Elite)", "France", "ME"),
        _nc_row(592, "French Road National Championship - ITT (Women Elite)", "France", "WE"),
        _nc_row(900, "USA Road National Championship (Men Elite)", "United-States", "ME"),
    ]) + "</table>"
    got = {(c["nat"], c["discipline"]): c["race_id"] for c in lfr.parse_nc_listing(html, 2026)}
    # Men's France ITT + road kept; women's dropped; non-target nation dropped.
    assert got == {("FR", "itt"): 112, ("FR", "road"): 113}


def test_is_nc_race_and_discipline():
    assert lfr.is_nc_race({"slug": "nc-france-itt-2026"}) is True
    assert lfr.is_nc_race({"slug": "tour-de-france-2026", "uci_tour": "2.UWT"}) is False
    assert lfr.is_nc_race({"slug": "x-2026", "uci_tour": "NC"}) is True   # PCS class
    assert lfr.is_nc_race({"slug": "x-2026", "uci_tour": "CN"}) is True   # LFR class
    # Override-slug NC (no 'nc-' prefix) still recognised via the class.
    assert lfr.is_nc_race({"slug": "danish-championships-2026", "uci_tour": "NC"}) is True
    assert lfr.nc_discipline({"slug": "nc-france-itt-2026"}) == "itt"
    assert lfr.nc_discipline({"slug": "nc-france-2026"}) == "road"
    assert lfr.nc_discipline({"slug": "danish-championships-2026"}) == "road"


def test_find_race_page_resolves_nc_by_nation_and_discipline():
    nc_pool = {("FR", "itt"): {"race_id": 112, "name": "French NC ITT",
                               "view_url": "u/112"}}
    itt = lfr.find_race_page(
        {"slug": "nc-france-itt-2026", "nationality": "FR", "name": "FR NC ITT"},
        2026, [], nc_pool)
    assert itt and itt["race_id"] == 112
    # Road race for the same nation isn't in the pool → no match.
    road = lfr.find_race_page(
        {"slug": "nc-france-2026", "nationality": "FR", "name": "FR NC Road"},
        2026, [], nc_pool)
    assert road is None


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
