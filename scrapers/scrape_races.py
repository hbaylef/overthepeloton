#!/usr/bin/env python3
"""
Scrape upcoming UCI World Tour races and startlists from procyclingstats.com.

Outputs:
  - data/races.json: list of upcoming WT races with metadata
  - data/startlists/{race_slug}.json: startlist per race (when available)

Usage:
  python scrapers/scrape_races.py

Note: This script must be run from an environment that can access
procyclingstats.com (e.g. your local machine or GitHub Actions).
It will NOT work in sandboxed environments with network restrictions.
"""

import json
import os
import re
import time
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Optional

import cloudscraper
from bs4 import BeautifulSoup
from procyclingstats import Race, RaceStartlist

import db  # local module: Turso/SQLite store (build-order step 2)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
YEAR = datetime.now().year
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Legacy on-disk paths. Races AND startlists now live in Turso (race_data
# kind="race"/"startlist"); these paths are only read once, to SEED Turso on
# the first run. No longer written.
RACES_FILE = DATA_DIR / "races.json"
STARTLISTS_DIR = DATA_DIR / "startlists"

# race_data "kind" values (docs keyed by slug, e.g. tour-de-france-2026).
DB_RACE_KIND = db.KIND_RACE
DB_STARTLIST_KIND = db.KIND_STARTLIST
DELAY_BETWEEN_REQUESTS = 2  # seconds — be polite to PCS
# A race that ended more than this many days ago is "frozen": we reuse its
# cached entry + startlist and make no network calls for it. The grace window
# keeps us scraping for a couple of days after the finish so the final stage's
# results / abandons (scraped by scrape_results.py) are captured before freezing.
FREEZE_GRACE_DAYS = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Master 2026 race calendar.
# Key   = cyclingstage.com slug (used for GPX scraping)
# Value = (pcs_slug, display_name, nationality_code, is_one_day_race, month)
#
# When PCS has the race, we use its rich data (dates, stages, edition).
# When PCS fails, we keep the race with this fallback info so the user can
# still browse it. The `month` field gives a rough sort key.
#
# Covers ~37 races: all UCI World Tour + extra ones cyclingstage.com covers
# (Tour of Britain, Tour of the Alps, Brabantse Pijl, etc.).
# ---------------------------------------------------------------------------
CALENDAR = {
    # January
    "tour-down-under":           ("tour-down-under",                       "Tour Down Under",                "AU", False, 1),
    # February
    "tour-of-valencia":          ("vuelta-a-la-comunidad-valenciana",      "Volta a la Comunitat Valenciana","ES", False, 2),
    "ruta-del-sol":              ("ruta-del-sol",                          "Vuelta a Andalucía",             "ES", False, 2),
    "volta-ao-algarve":          ("volta-ao-algarve",                      "Volta ao Algarve",               "PT", False, 2),
    "uae-tour":                  ("uae-tour",                              "UAE Tour",                       "AE", False, 2),
    # March
    "omloop-het-nieuwsblad":     ("omloop-het-nieuwsblad",                 "Omloop Het Nieuwsblad",          "BE", True,  3),
    "kuurne-brussels-kuurne":    ("kuurne-brussel-kuurne",                 "Kuurne-Brussels-Kuurne",         "BE", True,  3),
    "strade-bianche":            ("strade-bianche",                        "Strade Bianche",                 "IT", True,  3),
    "o-gran-camino":             ("gran-camino",                           "O Gran Camiño",                  "ES", False, 3),
    "paris-nice":                ("paris-nice",                            "Paris-Nice",                     "FR", False, 3),
    "tirreno-adriatico":         ("tirreno-adriatico",                     "Tirreno-Adriatico",              "IT", False, 3),
    "milan-san-remo":            ("milano-sanremo",                        "Milano-Sanremo",                 "IT", True,  3),
    "volta-a-catalunya":         ("volta-a-catalunya",                     "Volta a Catalunya",              "ES", False, 3),
    "e3-saxo-classic":           ("e3-harelbeke",                          "E3 Saxo Classic",                "BE", True,  3),
    "in-flanders-fields":        ("gent-wevelgem",                         "In Flanders Fields (Gent-Wevelgem)","BE", True, 3),
    "dwars-door-vlaanderen":     ("dwars-door-vlaanderen",                 "Dwars door Vlaanderen",          "BE", True,  3),
    # April
    "tour-of-flanders":          ("ronde-van-vlaanderen",                  "Tour of Flanders",               "BE", True,  4),
    "paris-roubaix":             ("paris-roubaix",                         "Paris-Roubaix",                  "FR", True,  4),
    "tour-of-the-basque-country":("itzulia-basque-country",                "Itzulia Basque Country",         "ES", False, 4),
    "brabantse-pijl":            ("brabantse-pijl",                        "Brabantse Pijl",                 "BE", True,  4),
    "amstel-gold-race":          ("amstel-gold-race",                      "Amstel Gold Race",               "NL", True,  4),
    "la-fleche-wallonne":        ("la-fleche-wallonne",                    "La Flèche Wallonne",             "BE", True,  4),
    "liege-bastogne-liege":      ("liege-bastogne-liege",                  "Liège-Bastogne-Liège",           "BE", True,  4),
    "tour-of-the-alps":          ("tour-of-the-alps",                      "Tour of the Alps",               "IT", False, 4),
    "tour-de-romandie":          ("tour-de-romandie",                      "Tour de Romandie",               "CH", False, 4),
    # May
    "giro":                      ("giro-d-italia",                         "Giro d'Italia",                  "IT", False, 5),
    # June
    "tour-auvergne-rhone-alpes": ("criterium-du-dauphine",                 "Tour Auvergne-Rhône-Alpes",      "FR", False, 6),
    "tour-de-suisse":            ("tour-de-suisse",                        "Tour de Suisse",                 "CH", False, 6),
    # July
    "tour-de-france":            ("tour-de-france",                        "Tour de France",                 "FR", False, 7),
    # August
    "clasica-de-san-sebastian":  ("san-sebastian",                         "Clásica de San Sebastián",       "ES", True,  8),
    "vuelta":                    ("vuelta-a-espana",                       "Vuelta a España",                "ES", False, 8),
    "renewi-tour":               ("renewi-tour",                           "Renewi Tour",                    "BE", False, 8),
    # September
    "gp-quebec":                 ("gp-quebec",                             "Grand Prix de Québec",           "CA", True,  9),
    "gp-montreal":               ("gp-montreal",                           "Grand Prix de Montréal",         "CA", True,  9),
    "tour-of-britain":           ("tour-of-britain",                       "Tour of Britain",                "GB", False, 9),
    # October
    "tour-of-lombardy":          ("il-lombardia",                          "Il Lombardia",                   "IT", True,  10),
    "paris-tours":               ("paris-tours",                           "Paris-Tours",                    "FR", True,  10),
}


# ---------------------------------------------------------------------------
# PCS season-calendar discovery (Phase A).
# CALENDAR above stays the hand-tuned base (display names, cyclingstage_slug,
# ONE_DAY_OVERRIDE keys); discovery extends it to the FULL WorldTour + ProSeries
# season so no race is missed. Discovered races run through the same enrichment
# as CALENDAR ones. If discovery fails (PCS down), we fall back to CALENDAR
# alone — never less than today's 37 races.
# ---------------------------------------------------------------------------

# Circuit codes on PCS's races.php filter, VERIFIED live (2026-06-10) from the
# filter form's <select name="circuit">: 1 = "UCI WorldTour",
# 26 = "UCI ProSeries". (Others, for reference: 24 = Women's WorldTour,
# 13 = Europe Tour.)
PCS_CIRCUITS = {"UCI WorldTour": 1, "UCI ProSeries": 26}
PCS_CALENDAR_URL = ("https://www.procyclingstats.com/races.php"
                    "?year={year}&circuit={circuit}&class=&filter=Filter")

# PCS occasionally renames a race's canonical slug between seasons (the old
# slug usually still resolves). Map a races.php-discovered slug back to the
# CALENDAR pcs_slug it corresponds to, so a rename reconciles to the hand-tuned
# entry instead of duplicating the race.
PCS_SLUG_ALIASES = {
    "tour-auvergne-rhone-alpes": "criterium-du-dauphine",        # renamed 2026
    # (Removed valencia alias: the CALENDAR now points at the MEN'S race
    #  "vuelta-a-la-comunidad-valenciana" directly, so discovery reconciles by
    #  pcs_slug. The old alias folded the men's race into the WOMEN'S entry.)
}

# PCS listing names carry a gender/sponsor tail the race page itself drops
# ("Omloop Nieuwsblad ME", "Surf Coast Classic - Men"). Only used for fallback
# display names; enrichment overwrites with the race page's own name.
_LISTING_NAME_TAIL_RE = re.compile(r"\s*(?:\bME\b|-\s*Men)\s*$")

_CAL_ROW_DATE_RE = re.compile(r"(\d{2})\.(\d{2})")


def _cal_iso_date(token: str, year: int) -> Optional[str]:
    """'20.01' → '2026-01-20'."""
    m = _CAL_ROW_DATE_RE.fullmatch(token.strip())
    return f"{year}-{m.group(2)}-{m.group(1)}" if m else None


def parse_calendar_html(html: str, year: int) -> List[dict]:
    """Parse one races.php season-calendar page into race rows.

    Each table row holds: a date ('01.02') or range ('20.01 - 25.01'), a flag
    span, a race/{slug}/{year}[/gc|/result] link, and the UCI class in the last
    cell. Returns [{pcs_slug, name, nationality, startdate, enddate, uci_class}],
    de-duplicated by slug.
    """
    out, seen = [], set()
    soup = BeautifulSoup(html, "html.parser")
    link_re = re.compile(rf"^race/([^/]+)/{year}(?:/|$)")
    for tr in soup.select("table tr"):
        a = tr.find("a", href=link_re)
        if a is None:
            continue
        slug = link_re.match(a["href"]).group(1)
        if slug in seen:
            continue
        seen.add(slug)
        tds = tr.find_all("td")
        dates = _CAL_ROW_DATE_RE.findall(tds[0].get_text()) if tds else []
        startdate = f"{year}-{dates[0][1]}-{dates[0][0]}" if dates else None
        enddate = f"{year}-{dates[-1][1]}-{dates[-1][0]}" if dates else None
        flag = tr.find("span", class_="flag")
        nat = None
        if flag:
            codes = [c for c in flag.get("class", []) if c != "flag"]
            nat = codes[0].upper() if codes else None
        out.append({
            "pcs_slug": slug,
            "name": a.get_text(strip=True),
            "nationality": nat,
            "startdate": startdate,
            "enddate": enddate,
            "uci_class": tds[-1].get_text(strip=True) if tds else None,
        })
    return out


def discover_calendar(year: int) -> Optional[List[dict]]:
    """Fetch the full WT + ProSeries season calendar from PCS.

    Returns the combined race rows, or None when NO circuit page could be
    fetched (caller then proceeds with the hardcoded CALENDAR alone). A partial
    result (one circuit down) is still returned — the CALENDAR superset rule
    protects the missing races.
    """
    rows, fetched = [], 0
    for circuit_name, code in PCS_CIRCUITS.items():
        url = PCS_CALENDAR_URL.format(year=year, circuit=code)
        try:
            log.info(f"Discovering {circuit_name} {year} calendar: {url}")
            r = _get_scraper().get(url, timeout=30)
            if r.status_code != 200:
                log.warning(f"  calendar: HTTP {r.status_code}")
                continue
            page_rows = parse_calendar_html(r.text, year)
            log.info(f"  {len(page_rows)} {circuit_name} races listed")
            rows.extend(page_rows)
            fetched += 1
        except Exception as e:
            log.warning(f"  calendar fetch failed: {e}")
        time.sleep(DELAY_BETWEEN_REQUESTS)
    return rows if fetched else None


def _entry(pcs_slug: str, name: str, nationality: Optional[str],
           is_one_day: bool, month: int, from_calendar: bool,
           startdate: Optional[str] = None, enddate: Optional[str] = None,
           uci_class: Optional[str] = None) -> dict:
    return {
        "pcs_slug": pcs_slug, "name": name, "nationality": nationality,
        "is_one_day": is_one_day, "month": month,
        "from_calendar": from_calendar,
        "startdate": startdate, "enddate": enddate, "uci_class": uci_class,
    }


def calendar_entries() -> dict:
    """The hand-tuned CALENDAR as {cs_slug: entry-dict}."""
    return {cs: _entry(*vals, from_calendar=True)
            for cs, vals in CALENDAR.items()}


def build_effective_calendar(discovered: List[dict]) -> dict:
    """Merge PCS-discovered races into the hand-tuned CALENDAR.

    Superset, never a regression: every CALENDAR entry is kept (even when PCS
    no longer lists it), and CALENDAR wins for the hand-tuned fields
    (cyclingstage_slug = the dict key, display name, ONE_DAY_OVERRIDE keys).
    A discovered race reconciles to a CALENDAR entry by pcs_slug, by
    PCS_SLUG_ALIASES, or by matching the CALENDAR key itself (covers renames
    where our cs key already uses the new PCS name). Anything left is a new
    race keyed by its pcs_slug — so its internal slug is '{pcs_slug}-{year}'
    and its cyclingstage_slug defaults to the pcs_slug (content-validated
    downstream, wrong guesses fail safely).
    """
    entries = calendar_entries()
    by_pcs = {e["pcs_slug"]: cs for cs, e in entries.items()}
    for d in discovered:
        slug = d["pcs_slug"]
        cs = by_pcs.get(PCS_SLUG_ALIASES.get(slug, slug))
        if cs is None and slug in entries:
            cs = slug
        if cs is not None:
            # Hand-tuned fields win; discovery just fills the season metadata.
            entries[cs].update(startdate=d["startdate"], enddate=d["enddate"],
                               uci_class=d["uci_class"])
            continue
        name = _LISTING_NAME_TAIL_RE.sub("", d["name"] or slug.replace("-", " "))
        month = int(d["startdate"][5:7]) if d["startdate"] else 12
        is_one_day = bool(d["uci_class"] and d["uci_class"].startswith("1."))
        entries[slug] = _entry(slug, name, d["nationality"], is_one_day, month,
                               from_calendar=False, startdate=d["startdate"],
                               enddate=d["enddate"], uci_class=d["uci_class"])
    return entries


# Manual override for one-day race profile icons. PCS returns "p0" for both
# legitimately flat races AND races it hasn't classified yet (placeholder).
# This dict only fires when the scraped value is "p0" — the moment PCS
# publishes a real non-p0 icon, the override is bypassed automatically.
# Keyed by cs_slug to match CALENDAR.
ONE_DAY_OVERRIDE = {
    "clasica-de-san-sebastian": "p3",  # hilly classic
    "gp-quebec":                "p3",  # uphill finishes
    "gp-montreal":              "p3",  # uphill finishes
    "tour-of-lombardy":         "p5",  # mountain classic
    # paris-tours intentionally OUT — genuinely flat-ish; trust PCS when it publishes.
}

# Reusable Cloudflare-aware HTTP session for the one-off profile-icon scrape.
# (The procyclingstats library has its own internal session; this sidecar is
# used only for endpoints the library doesn't model — see /result below.)
_scraper = None


def _get_scraper():
    global _scraper
    if _scraper is None:
        _scraper = cloudscraper.create_scraper()
    return _scraper


def make_slug(race_url: str) -> str:
    """Extract a clean slug from a PCS race URL for filenames.
    'race/tour-de-france/2026' -> 'tour-de-france-2026'
    """
    parts = race_url.replace("race/", "").split("/")
    return "-".join(parts)


def pcs_slug(race_url: str) -> str:
    """Extract just the race name part of a PCS URL.
    'race/tour-de-france/2026' -> 'tour-de-france'
    """
    return race_url.replace("race/", "").split("/")[0]


def scrape_race_info(race_url: str) -> Optional[dict]:
    """
    Scrape basic race info (name, dates, stages, category, etc.)
    Returns None if the page doesn't exist or parsing fails.
    """
    try:
        log.info(f"Scraping race info: {race_url}")
        race = Race(race_url)
        data = race.parse()

        slug = pcs_slug(race_url)
        # cyclingstage_slug is filled in by main() from CALENDAR; default to PCS slug.
        result = {
            "pcs_url": race_url,
            "slug": make_slug(race_url),
            "pcs_slug": slug,
            "cyclingstage_slug": slug,  # overridden by caller
            "name": data.get("name"),
            "year": data.get("year"),
            "nationality": data.get("nationality"),
            "startdate": data.get("startdate"),
            "enddate": data.get("enddate"),
            "category": data.get("category"),
            "uci_tour": data.get("uci_tour"),
            "is_one_day_race": data.get("is_one_day_race"),
            "edition": data.get("edition"),
            "stages": data.get("stages", []),
        }

        return result

    except Exception as e:
        log.warning(f"Failed to scrape {race_url}: {e}")
        return None


# Only MEN'S elite races belong in this dashboard. PCS race pages expose a
# `category` ("Men Elite" / "Women Elite"). Discovery already queries men's
# circuits only (WorldTour=1, ProSeries=26; Women's WorldTour=24 is never asked),
# so this is the belt-and-braces guard that drops any women's race that still
# slips in via a mis-mapped CALENDAR slug or alias.
def is_mens_race(info: dict) -> bool:
    """False only when the race's PCS category is explicitly a women's one.
    Unknown/missing category → True (men-only discovery; don't drop legit races)."""
    cat = (info or {}).get("category") or ""
    return "women" not in cat.lower()


def drop_substitute_riders(riders: list) -> list:
    """
    PCS lists a team's reserve/substitute rider as a SECOND entry that shares a
    bib number with a confirmed starter (almost always the team's #X4 slot, the
    second one being the substitute who often doesn't actually start). The PCS
    library exposes no reserve flag, so we dedupe: within each team, keep the
    FIRST rider for a given bib number and drop any later duplicate(s).
    """
    seen = set()
    out = []
    for r in riders:
        num = r.get("number")
        key = (r.get("team"), num)
        if num is not None and key in seen:
            continue                 # duplicate bib within the team → substitute
        if num is not None:
            seen.add(key)
        out.append(r)
    return out


# Result fields owned by scrape_results.py (abandons + stage medals). scrape_races
# rebuilds startlists from scratch each day, so without this it would wipe them
# between the morning calendar scrape and scrape_results re-deriving them at the
# end of the pipeline — making medals/DNF tags flicker if scrape_results fails.
RESULT_FIELDS = ("status", "abandoned_stage", "medals")


def carry_over_results(client, riders: list, slug: str) -> None:
    """Copy any existing abandon/medal fields from the previous startlist (in
    Turso) onto the freshly-scraped riders (matched by rider_url), in place.
    These are authoritatively refreshed later by scrape_results.py; this just
    preserves them across scrape_races' rebuild so they never momentarily
    disappear."""
    prev = db.get_document(client, DB_STARTLIST_KIND, slug)
    if not prev:
        return
    by_url = {r.get("rider_url"): r for r in prev.get("riders", []) if r.get("rider_url")}
    for r in riders:
        old = by_url.get(r.get("rider_url"))
        if not old:
            continue
        for k in RESULT_FIELDS:
            if k in old:
                r[k] = old[k]


def scrape_startlist(race_url: str) -> Optional[list]:
    """
    Scrape the startlist for a race.
    Returns list of riders or None if not available yet.
    """
    startlist_url = f"{race_url}/startlist"
    try:
        log.info(f"Scraping startlist: {startlist_url}")
        sl = RaceStartlist(startlist_url)
        riders = sl.startlist()

        # Clean up and structure the rider data
        cleaned = []
        for rider in riders:
            cleaned.append({
                "name": rider.get("rider_name"),
                "nationality": rider.get("nationality"),
                "number": rider.get("rider_number"),
                "team": rider.get("team_name"),
                "rider_url": rider.get("rider_url"),
                "team_url": rider.get("team_url"),
            })

        return drop_substitute_riders(cleaned)

    except Exception as e:
        log.warning(f"No startlist available for {race_url}: {e}")
        return None


def refresh_startlists_only(client) -> int:
    """DAILY lightweight path (--startlists-only): re-scrape ONLY the startlists of
    races already in the store — no PCS calendar discovery, no climbs. Skips races
    that are over, carries over abandon/medal fields, and RE-APPLIES the cached
    rider specialties + birthplaces/coords (zero per-rider PCS / Nominatim calls),
    so the rebuilt startlists stay complete between the WEEKLY full runs. New riders
    get null enrichment blocks until the next weekly full run scrapes + caches them.
    Returns the number of startlists rewritten."""
    import scrape_riders
    import geocode_birthplaces as geo

    rider_cache = scrape_riders.load_cache(client).get("riders", {})
    geo_cache = geo.load_cache(client)
    races = db.get_all_documents(client, DB_RACE_KIND)
    today = datetime.now().date()
    log.info(f"--startlists-only: {len(races)} stored races (skipping finished ones)")

    n = 0
    for slug, race in races.items():
        race_url = race.get("pcs_url")
        if not race_url or is_finished(race, today):
            continue
        riders = scrape_startlist(race_url)
        time.sleep(DELAY_BETWEEN_REQUESTS)
        if not riders:
            continue
        carry_over_results(client, riders, slug)          # keep abandons/medals
        for r in riders:                                  # re-apply cached enrichment
            ent = rider_cache.get(r.get("rider_url")) or {}
            r["specialties"] = {"career": ent.get("career")}
            r["birthdate"] = ent.get("birthdate")
            r["place_of_birth"] = ent.get("place_of_birth")
            place = r.get("place_of_birth")
            gent = geo_cache.get(geo.cache_key(place, r.get("nationality"))) if place else None
            r["birthplace_lat"] = (gent or {}).get("lat")
            r["birthplace_lon"] = (gent or {}).get("lon")
        db.put_document(client, DB_STARTLIST_KIND, slug, {
            "race": race.get("name"),
            "race_slug": slug,
            "updated_at": datetime.now().isoformat(),
            "total_riders": len(riders),
            "riders": riders,
        })
        n += 1
        log.info(f"  → {slug}: {len(riders)} riders")
    return n


def scrape_one_day_profile_icon(pcs_slug: str, year: int) -> Optional[str]:
    """
    Fetch the profile icon (p0..p5) for a one-day race by scraping its
    PCS /result page. The procyclingstats library returns [] from
    Race.stages() for one-day races, so we have to go direct.

    The /result page exists for both past and upcoming races, but for
    upcoming races PCS often serves "p0" as a placeholder — the caller
    applies ONE_DAY_OVERRIDE for known mismatches.

    Returns None on network error / parse failure.
    """
    url = f"https://www.procyclingstats.com/race/{pcs_slug}/{year}/result"
    try:
        r = _get_scraper().get(url, timeout=30)
        if r.status_code != 200:
            log.warning(f"  profile icon: HTTP {r.status_code} for {url}")
            return None
        m = re.search(r'class="icon profile (p[0-5])', r.text)
        if not m:
            log.warning(f"  profile icon: not found in HTML at {url}")
            return None
        return m.group(1)
    except Exception as e:
        log.warning(f"  profile icon: failed to fetch {url}: {e}")
        return None


def build_fallback_entry(cs_slug: str, pcs_slug: str, name: str,
                          nationality: str, is_one_day: bool, month: int,
                          year: int, startdate: Optional[str] = None,
                          enddate: Optional[str] = None,
                          uci_class: Optional[str] = None) -> dict:
    """
    Build a minimal race entry when PCS scraping fails for this race.
    Uses the season-calendar dates/class when discovery provided them; else an
    approximate date (15th of given month) so it sorts roughly right.
    """
    approx_date = f"{year}-{month:02d}-15"
    return {
        "pcs_url": f"race/{pcs_slug}/{year}",
        "slug": f"{cs_slug}-{year}",
        "pcs_slug": pcs_slug,
        "cyclingstage_slug": cs_slug,
        "name": name,
        "year": year,
        "nationality": nationality,
        "startdate": startdate or approx_date,
        "enddate": enddate or startdate or approx_date,
        "category": "Men Elite",
        "uci_tour": uci_class,
        "is_one_day_race": is_one_day,
        "edition": None,
        "stages": [],
        "_pcs_data_missing": True,
    }


# ---------------------------------------------------------------------------
# R2 Phase 2 — derived stage-type classification (pure logic, no scraping).
#
# Maps a PCS profile_icon (p0..p5) to a stage_type, with an ITT override by
# name. Annotations are written back INTO races.json (per-stage for stage
# races, race-level for one-day races) so R2 scoring + the frontend can read
# them directly. Re-run safe: a full scrape re-derives these every day.
# ---------------------------------------------------------------------------

# profile_icon → stage_type. `cobbles` is intentionally absent: it's deferred
# to R4's curated cobble set, which overlays the type at scoring time.
PROFILE_ICON_TO_STAGE_TYPE = {
    "p0": "sprint",          # flat (PCS also uses p0 as an unclassified placeholder)
    "p1": "sprint",          # flat
    "p2": "sprint_break",    # hilly, flat finish
    "p3": "hills_puncheur",  # hilly, uphill finish
    "p4": "climber",         # mountain
    "p5": "climber",         # mountain
}

# Individual time trials. The icon can't identify an ITT — most are encoded
# p1, same as a flat sprint stage — so we detect them by name and override the
# type. Matches PCS's "(ITT)" and "Prologue" forms plus a spelled-out
# "Time trial" for robustness.
#
# Deliberately does NOT match team time trials "(TTT)": a TTT is a team effort,
# not an individual TT, so it must not inherit the time_trial weight vector.
# No TTT exists in the 2026 calendar; classifying TTTs is a KNOWN GAP to fix
# later (would need its own type/weights).
ITT_NAME_RE = re.compile(r"\(ITT\)|\bPrologue\b|\bTime[\s-]?trial\b", re.IGNORECASE)

# Used when profile_icon is missing/null/unrecognized (design Step 1:
# "treat as sprint/break and flag it" — the flag is stage_type_source below).
FALLBACK_STAGE_TYPE = "sprint_break"


def classify_stage(profile_icon: Optional[str], name: Optional[str] = None):
    """
    Derive a stage_type from a PCS profile_icon, with an ITT override by name.

    Pure logic — no scraping. Returns ``(stage_type, source)`` where source is
    one of:
      - "stage_name_itt"   — name matched the ITT regex (icon ignored)
      - "profile_icon"     — mapped straight from a valid p0..p5 icon
      - "fallback_default" — icon missing/unrecognized; guessed sprint_break

    `name` is the stage_name (stage races) or race name (one-day races); pass
    None when no name is available. ITT detection is checked first because the
    icon is unreliable for time trials.
    """
    if name and ITT_NAME_RE.search(name):
        return "time_trial", "stage_name_itt"
    stage_type = PROFILE_ICON_TO_STAGE_TYPE.get(profile_icon)
    if stage_type is not None:
        return stage_type, "profile_icon"
    return FALLBACK_STAGE_TYPE, "fallback_default"


def annotate_stage_types(races: list) -> list:
    """
    Write derived `stage_type` + `stage_type_source` onto each race in place:
      - stage races: per-stage, inside each stages[] entry (keyed on stage_name)
      - one-day races: at the race level (keyed on the race name)

    Mutates and returns the same list. Overwrites any prior annotation so a
    re-run never drifts.
    """
    for race in races:
        if race.get("is_one_day_race"):
            stage_type, source = classify_stage(race.get("profile_icon"), race.get("name"))
            race["stage_type"] = stage_type
            race["stage_type_source"] = source
        else:
            for stage in race.get("stages", []):
                stage_type, source = classify_stage(stage.get("profile_icon"), stage.get("stage_name"))
                stage["stage_type"] = stage_type
                stage["stage_type_source"] = source
    return races


def seed_races_from_json_if_empty(client) -> int:
    """One-time bootstrap. If Turso has no race docs yet but a legacy
    data/races.json exists, import it. This makes the freeze work on the very
    first Turso run (so finished races aren't needlessly re-scraped) and
    preserves the existing curated data. Idempotent — does nothing once the
    race table is populated."""
    if db.list_slugs(client, DB_RACE_KIND):
        return 0
    if not RACES_FILE.exists():
        return 0
    try:
        legacy = json.loads(RACES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"Seed: could not read {RACES_FILE.name}: {e}")
        return 0
    n = 0
    for race in legacy.get("races", []):
        slug = race.get("slug")
        if slug:
            db.put_document(client, DB_RACE_KIND, slug, race)
            n += 1
    if n:
        log.info(f"Seeded {n} races from legacy {RACES_FILE.name} into Turso")
    return n


def seed_startlists_from_json_if_empty(client) -> int:
    """One-time bootstrap for startlists, mirroring the race seed. If Turso has
    no startlist docs yet, import every data/startlists/*.json. Crucial so
    FINISHED (frozen) races — which scrape_races never re-scrapes — keep their
    startlists in the store. Idempotent."""
    if db.list_slugs(client, DB_STARTLIST_KIND):
        return 0
    if not STARTLISTS_DIR.exists():
        return 0
    n = 0
    for f in sorted(STARTLISTS_DIR.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Seed: could not read {f.name}: {e}")
            continue
        slug = doc.get("race_slug") or f.stem
        db.put_document(client, DB_STARTLIST_KIND, slug, doc)
        n += 1
    if n:
        log.info(f"Seeded {n} startlists from legacy files into Turso")
    return n


def load_existing_races_by_cs(client) -> dict:
    """Index the previously-stored races (from Turso) by cyclingstage_slug (the
    CALENDAR key), so a frozen race can reuse last run's entry without
    re-scraping."""
    out = {}
    for r in db.get_all_documents(client, DB_RACE_KIND).values():
        cs = r.get("cyclingstage_slug")
        if cs:
            out[cs] = r
    return out


def is_finished(entry: dict, today: date) -> bool:
    """True if the race ended more than FREEZE_GRACE_DAYS ago (safe to freeze)."""
    end = entry.get("enddate")
    if not end or len(end) < 10:
        return False
    try:
        end_d = datetime.strptime(end[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return end_d < today - timedelta(days=FREEZE_GRACE_DAYS)


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Scrape the race calendar + startlists into Turso.")
    ap.add_argument("--startlists-only", action="store_true",
                    help="DAILY lightweight mode: re-scrape ONLY the startlists of "
                         "races already in the store (no calendar discovery, no "
                         "climbs), re-applying cached rider data. Run the full "
                         "no-flag scrape weekly.")
    args = ap.parse_args()

    # Create output directories (startlists are still written to disk this step)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STARTLISTS_DIR.mkdir(parents=True, exist_ok=True)

    # Open the store (remote Turso in Actions; local SQLite file in dev).
    client = db.open_db()
    where = "remote Turso" if db.is_remote() else "local SQLite file"
    log.info(f"Race store: {where}")

    if args.startlists_only:
        n = refresh_startlists_only(client)
        client.close()
        log.info(f"--startlists-only done: {n} startlist(s) refreshed.")
        return

    # First-run bootstrap: import the legacy JSON so freeze works today and
    # frozen races keep their startlists.
    seed_races_from_json_if_empty(client)
    seed_startlists_from_json_if_empty(client)

    # Reuse last run's data for races that are already over (skip the network).
    existing = load_existing_races_by_cs(client)
    today = datetime.now().date()   # Actions runs in UTC; date granularity is enough
    frozen = 0

    # Phase A — discover the full WT + ProSeries season from PCS and merge it
    # into the hand-tuned CALENDAR (superset; CALENDAR wins hand-tuned fields).
    discovered = discover_calendar(YEAR)
    if discovered is None:
        log.warning("PCS calendar discovery unavailable — "
                    "proceeding with the hardcoded CALENDAR only")
        entries = calendar_entries()
    else:
        entries = build_effective_calendar(discovered)
        log.info(f"Effective calendar: {len(entries)} races "
                 f"({len(CALENDAR)} hand-tuned + "
                 f"{len(entries) - len(CALENDAR)} discovered)")

    all_races = []
    startlist_count = 0
    pcs_ok = 0
    pcs_fail = 0

    for cs_slug, entry in entries.items():
        pcs_slug = entry["pcs_slug"]
        name = entry["name"]
        is_one_day = entry["is_one_day"]
        race_url = f"race/{pcs_slug}/{YEAR}"

        # 0) Skip races that are over: reuse last run's entry + existing startlist
        #    and make zero network calls. Final-day results are still captured
        #    because the freeze waits FREEZE_GRACE_DAYS past the enddate.
        cached = existing.get(cs_slug)
        # A cached women's race must NOT be frozen-reused: skip the freeze so we
        # fall through and (re)scrape this slug's men's race instead.
        if cached and is_finished(cached, today) and is_mens_race(cached):
            log.info(f"  → Frozen (race over): {name} — reusing cached data")
            all_races.append(cached)
            frozen += 1
            if db.has_document(client, DB_STARTLIST_KIND, cached.get("slug", "")):
                startlist_count += 1
            continue

        # 1) Try PCS for rich race info
        info = scrape_race_info(race_url)
        time.sleep(DELAY_BETWEEN_REQUESTS)

        if info is None:
            # PCS didn't have this race — use fallback with calendar basics
            log.info(f"  → No PCS data, using fallback for: {name}")
            info = build_fallback_entry(cs_slug, pcs_slug, name,
                                         entry["nationality"], is_one_day,
                                         entry["month"], YEAR,
                                         startdate=entry["startdate"],
                                         enddate=entry["enddate"],
                                         uci_class=entry["uci_class"])
            pcs_fail += 1
        else:
            # Override the cyclingstage_slug from our master mapping.
            info["cyclingstage_slug"] = cs_slug
            if entry["from_calendar"]:
                # Force the display name from CALENDAR so PCS's verbose titles
                # (e.g. "Donostia San Sebastian Klasikoa") don't replace our
                # curated ones (e.g. "Clásica de San Sebastián").
                info["name"] = name
            elif not info.get("name"):
                info["name"] = name   # listing name beats an empty parse
            pcs_ok += 1

        # Men's-only guard: drop any women's race (e.g. a mis-mapped slug/alias)
        # before doing any further work or storing a startlist for it.
        if not is_mens_race(info):
            log.info(f"  → Skipping non-men's race: {info.get('name') or name}")
            continue

        # For one-day races, fetch the race-level profile icon (the library
        # gives us nothing — stages() returns []). Apply ONE_DAY_OVERRIDE
        # only when PCS returns "p0" (placeholder/ambiguous).
        if is_one_day:
            scraped_icon = scrape_one_day_profile_icon(pcs_slug, YEAR)
            time.sleep(DELAY_BETWEEN_REQUESTS)
            override = ONE_DAY_OVERRIDE.get(cs_slug)
            if scraped_icon == "p0" and override:
                info["profile_icon"] = override
                info["profile_icon_source"] = "manual_override"
                log.info(f"  → profile icon: {scraped_icon} (p0) → {override} (override)")
            elif scraped_icon:
                info["profile_icon"] = scraped_icon
                info["profile_icon_source"] = "pcs"
                log.info(f"  → profile icon: {scraped_icon} (pcs)")
            else:
                info["profile_icon"] = None
                info["profile_icon_source"] = None
                log.info(f"  → profile icon: unavailable")

        all_races.append(info)

        # 2) Try startlist (works only for some races, mainly closer to race day)
        riders = scrape_startlist(race_url)
        time.sleep(DELAY_BETWEEN_REQUESTS)

        if riders and len(riders) > 0:
            slug = info["slug"]
            carry_over_results(client, riders, slug)  # keep abandon/medal fields across the rebuild
            db.put_document(client, DB_STARTLIST_KIND, slug, {
                "race": info["name"],
                "race_slug": slug,
                "updated_at": datetime.now().isoformat(),
                "total_riders": len(riders),
                "riders": riders,
            })
            startlist_count += 1
            log.info(f"  → Saved startlist: {slug} ({len(riders)} riders)")

    # Sort by start date (chronological for the year)
    all_races.sort(key=lambda r: r.get("startdate") or "9999-12-31")

    # R2 Phase 2 — derive stage_type from profile_icon / stage names (in place).
    annotate_stage_types(all_races)

    # Persist each race as its own JSON-blob row in Turso (change-aware: a row is
    # written only when its content actually changed, so the store barely churns).
    written = 0
    for race in all_races:
        slug = race.get("slug")
        if slug and db.put_document(client, DB_RACE_KIND, slug, race):
            written += 1
    stored = len(db.list_slugs(client, DB_RACE_KIND))
    sl_slugs = set(db.list_slugs(client, DB_STARTLIST_KIND))
    client.close()

    log.info(f"Done! {len(all_races)} races processed; {written} rows changed; "
             f"{stored} race docs now in the store.")
    log.info(f"  PCS data ok: {pcs_ok}  ·  fallback used: {pcs_fail}  ·  frozen: {frozen}")
    log.info(f"  Startlists saved: {startlist_count}")

    print("\n" + "=" * 64)
    print(f"  SCRAPE SUMMARY")
    print(f"  Total races:  {len(all_races)}")
    print(f"  PCS enriched: {pcs_ok}")
    print(f"  Fallback:     {pcs_fail}  (races PCS didn't have data for)")
    print(f"  Frozen:       {frozen}  (races over — reused cached data, no scrape)")
    print(f"  Startlists:   {startlist_count}")
    print("=" * 64)
    for r in all_races:
        has_sl = "📋" if r["slug"] in sl_slugs else "  "
        flag = "⚠️" if r.get("_pcs_data_missing") else "  "
        print(f"  {has_sl}{flag} {r['startdate']}  {r['name']}")


if __name__ == "__main__":
    main()
