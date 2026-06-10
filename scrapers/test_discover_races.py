#!/usr/bin/env python3
"""
No-network tests for scrape_races.py's PCS season-calendar discovery (Phase A).

Covers the races.php parser (synthetic HTML mirroring the real row structure),
the CALENDAR reconciliation rules (superset, CALENDAR wins, slug aliases), and
the discovery-aware fallback entry. When the gitignored live fixture
scrapers/fixture/pcs_calendar_2026.json is present (harvested 2026-06-10), the
parser expectations are also checked against real listing data.

Run:  python scrapers/test_discover_races.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_races as sr

FIXTURE = Path(__file__).resolve().parent / "fixture" / "pcs_calendar_2026.json"

# Synthetic listing page, mirroring the real races.php table row structure
# (verified live 2026-06-10): date cell + hidden duplicate date cell, flag span
# + race link (href may end in /gc, /result or nothing), winner link, class.
CAL_HTML = """
<table class="basic">
 <thead><tr><th>Date</th><th>Date</th><th>Race</th><th>Winner</th><th>Class</th></tr></thead>
 <tbody>
  <tr><td class="cu500">20.01 - 25.01</td><td class="hide cs500">20.01</td>
      <td><span class="flag au"></span> <a href="race/tour-down-under/2026/gc">Santos Tour Down Under</a></td>
      <td class="cu500"><a href="rider/jay-vine">VINE Jay</a></td><td>2.UWT</td></tr>
  <tr><td class="cu500">28.02</td><td class="hide cs500">28.02</td>
      <td><span class="flag be"></span> <a href="race/omloop-het-nieuwsblad/2026/result">Omloop Nieuwsblad ME</a></td>
      <td class="cu500"></td><td>1.UWT</td></tr>
  <tr><td class="cu500">18.10</td><td class="hide cs500">18.10</td>
      <td><span class="flag jp"></span> <a href="race/japan-cup/2026">Utsunomiya Japan Cup Road Race</a></td>
      <td class="cu500"></td><td>1.Pro</td></tr>
  <tr><td class="cu500">20.01 - 25.01</td><td class="hide cs500">20.01</td>
      <td><span class="flag au"></span> <a href="race/tour-down-under/2026/gc">duplicate row</a></td>
      <td class="cu500"></td><td>2.UWT</td></tr>
  <tr><td class="cu500">01.05</td><td class="hide cs500">01.05</td>
      <td><a href="race/old-race/2025/result">wrong year — skip</a></td>
      <td class="cu500"></td><td>1.UWT</td></tr>
 </tbody>
</table>"""


def test_parse_calendar_html_rows():
    rows = sr.parse_calendar_html(CAL_HTML, 2026)
    assert [r["pcs_slug"] for r in rows] == [
        "tour-down-under", "omloop-het-nieuwsblad", "japan-cup"], rows
    tdu = rows[0]
    assert tdu["startdate"] == "2026-01-20" and tdu["enddate"] == "2026-01-25"
    assert tdu["nationality"] == "AU" and tdu["uci_class"] == "2.UWT"
    assert tdu["name"] == "Santos Tour Down Under"
    omloop = rows[1]   # one-day: startdate == enddate
    assert omloop["startdate"] == omloop["enddate"] == "2026-02-28"
    assert rows[2]["uci_class"] == "1.Pro" and rows[2]["nationality"] == "JP"


def test_build_effective_calendar_superset_and_new_races():
    discovered = [
        # Matches CALENDAR by pcs_slug → merges, no new key
        {"pcs_slug": "tour-de-france", "name": "Tour de France",
         "nationality": "FR", "startdate": "2026-07-04",
         "enddate": "2026-07-26", "uci_class": "2.UWT"},
        # New ProSeries stage race → added, keyed by pcs_slug
        {"pcs_slug": "alula-tour", "name": "AlUla Tour", "nationality": "SA",
         "startdate": "2026-01-27", "enddate": "2026-01-31", "uci_class": "2.Pro"},
        # New one-day race (1. prefix) with a listing-name tail to strip
        {"pcs_slug": "copenhagen-sprint", "name": "Copenhagen Sprint ME",
         "nationality": "DK", "startdate": "2026-06-14",
         "enddate": "2026-06-14", "uci_class": "1.UWT"},
    ]
    entries = sr.build_effective_calendar(discovered)
    # Superset: every CALENDAR entry kept + exactly the 2 new ones.
    assert len(entries) == len(sr.CALENDAR) + 2
    assert set(sr.CALENDAR) <= set(entries)
    # CALENDAR match: hand-tuned fields kept, season metadata filled in.
    tdf = entries["tour-de-france"]
    assert tdf["from_calendar"] and tdf["startdate"] == "2026-07-04"
    assert tdf["name"] == "Tour de France"
    # A CALENDAR race PCS no longer lists (not discovered) survives untouched.
    assert entries["o-gran-camino"]["pcs_slug"] == "gran-camino"
    # New races: keyed by pcs_slug, one-day from the class prefix, tail stripped.
    alula = entries["alula-tour"]
    assert not alula["from_calendar"] and not alula["is_one_day"]
    assert alula["month"] == 1 and alula["uci_class"] == "2.Pro"
    cph = entries["copenhagen-sprint"]
    assert cph["is_one_day"] and cph["name"] == "Copenhagen Sprint"


def test_build_effective_calendar_alias_reconciles_renamed_slug():
    # PCS renamed the Dauphiné's canonical slug; the alias must reconcile it to
    # the CALENDAR entry (pcs_slug criterium-du-dauphine) — no duplicate race.
    discovered = [{"pcs_slug": "tour-auvergne-rhone-alpes",
                   "name": "Tour Auvergne - Rhône-Alpes", "nationality": "FR",
                   "startdate": "2026-06-07", "enddate": "2026-06-14",
                   "uci_class": "2.UWT"}]
    entries = sr.build_effective_calendar(discovered)
    assert len(entries) == len(sr.CALENDAR)
    e = entries["tour-auvergne-rhone-alpes"]   # the CALENDAR (cs) key
    assert e["pcs_slug"] == "criterium-du-dauphine"      # CALENDAR wins
    assert e["startdate"] == "2026-06-07"                 # discovery fills dates
    # Same mechanism for the Valenciana rename.
    discovered = [{"pcs_slug": "vuelta-a-la-comunidad-valenciana",
                   "name": "Volta Comunitat Valenciana", "nationality": "ES",
                   "startdate": "2026-02-04", "enddate": "2026-02-08",
                   "uci_class": "2.Pro"}]
    entries = sr.build_effective_calendar(discovered)
    assert len(entries) == len(sr.CALENDAR)
    assert entries["tour-of-valencia"]["pcs_slug"] == "setmana-ciclista-valenciana"


def test_build_effective_calendar_matches_calendar_key_as_fallback():
    # A discovered slug that equals a CALENDAR KEY (cs_slug) but not any
    # pcs_slug or alias still reconciles — generic protection against renames
    # where our cs key already uses the new name.
    discovered = [{"pcs_slug": "tour-of-valencia", "name": "Volta Valenciana",
                   "nationality": "ES", "startdate": "2026-02-04",
                   "enddate": "2026-02-08", "uci_class": "2.Pro"}]
    entries = sr.build_effective_calendar(discovered)
    assert len(entries) == len(sr.CALENDAR)
    assert entries["tour-of-valencia"]["startdate"] == "2026-02-04"
    assert entries["tour-of-valencia"]["pcs_slug"] == "setmana-ciclista-valenciana"


def test_fallback_entry_uses_discovered_dates_and_class():
    e = sr.build_fallback_entry("alula-tour", "alula-tour", "AlUla Tour", "SA",
                                False, 1, 2026, startdate="2026-01-27",
                                enddate="2026-01-31", uci_class="2.Pro")
    assert e["slug"] == "alula-tour-2026"
    assert e["startdate"] == "2026-01-27" and e["enddate"] == "2026-01-31"
    assert e["uci_tour"] == "2.Pro" and e["_pcs_data_missing"] is True
    # Without discovery data: the old approximate behaviour.
    e = sr.build_fallback_entry("x", "x", "X", "FR", True, 7, 2026)
    assert e["startdate"] == e["enddate"] == "2026-07-15"
    assert e["uci_tour"] is None


def test_parse_calendar_html_against_live_fixture():
    # Harvested live listing (gitignored); skip silently when absent (CI).
    if not FIXTURE.exists():
        print("        (live fixture absent — skipped)")
        return
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def as_html(rows):
        trs = []
        for date, href, name, klass, nat in rows:
            trs.append(
                f'<tr><td class="cu500">{date}</td><td class="hide cs500"></td>'
                f'<td><span class="flag {nat.lower()}"></span> '
                f'<a href="{href}">{name}</a></td><td class="cu500"></td>'
                f'<td>{klass}</td></tr>')
        return "<table><tbody>" + "".join(trs) + "</tbody></table>"

    wt = sr.parse_calendar_html(as_html(fx["worldtour"]), 2026)
    pro = sr.parse_calendar_html(as_html(fx["proseries"]), 2026)
    assert len(wt) == 36 and len(pro) == 61, (len(wt), len(pro))
    entries = sr.build_effective_calendar(wt + pro)
    # 37 hand-tuned + 61 discovered-only (8 WT + 53 ProSeries) = 98.
    assert len(entries) == 98, len(entries)
    new = {cs for cs, e in entries.items() if not e["from_calendar"]}
    assert "alula-tour" in new and "tour-de-pologne" in new
    # The two renamed slugs reconciled — no duplicates.
    assert "tour-auvergne-rhone-alpes" not in new
    assert "vuelta-a-la-comunidad-valenciana" not in new


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
