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
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import cloudscraper
from procyclingstats import Race, RaceStartlist

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
YEAR = datetime.now().year
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RACES_FILE = DATA_DIR / "races.json"
STARTLISTS_DIR = DATA_DIR / "startlists"
DELAY_BETWEEN_REQUESTS = 2  # seconds — be polite to PCS

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
    "tour-of-valencia":          ("setmana-ciclista-valenciana",           "Volta a la Comunitat Valenciana","ES", False, 2),
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

        return cleaned

    except Exception as e:
        log.warning(f"No startlist available for {race_url}: {e}")
        return None


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
                          year: int) -> dict:
    """
    Build a minimal race entry when PCS scraping fails for this race.
    Sets approximate date (15th of given month) so it sorts roughly right.
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
        "startdate": approx_date,
        "enddate": approx_date,
        "category": "Men Elite",
        "uci_tour": None,
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


def main():
    # Create output directories
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STARTLISTS_DIR.mkdir(parents=True, exist_ok=True)

    all_races = []
    startlist_count = 0
    pcs_ok = 0
    pcs_fail = 0

    for cs_slug, (pcs_slug, name, nationality, is_one_day, month) in CALENDAR.items():
        race_url = f"race/{pcs_slug}/{YEAR}"

        # 1) Try PCS for rich race info
        info = scrape_race_info(race_url)
        time.sleep(DELAY_BETWEEN_REQUESTS)

        if info is None:
            # PCS didn't have this race — use fallback with hardcoded basics
            log.info(f"  → No PCS data, using fallback for: {name}")
            info = build_fallback_entry(cs_slug, pcs_slug, name, nationality,
                                         is_one_day, month, YEAR)
            pcs_fail += 1
        else:
            # Override the cyclingstage_slug from our master mapping.
            # Also force the display name from CALENDAR so PCS's verbose titles
            # (e.g. "Donostia San Sebastian Klasikoa") don't replace our curated
            # ones (e.g. "Clásica de San Sebastián").
            info["cyclingstage_slug"] = cs_slug
            info["name"] = name
            pcs_ok += 1

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
            sl_file = STARTLISTS_DIR / f"{slug}.json"
            with open(sl_file, "w", encoding="utf-8") as f:
                json.dump({
                    "race": info["name"],
                    "race_slug": slug,
                    "updated_at": datetime.now().isoformat(),
                    "total_riders": len(riders),
                    "riders": riders,
                }, f, indent=2, ensure_ascii=False)
            startlist_count += 1
            log.info(f"  → Saved startlist: {slug} ({len(riders)} riders)")

    # Sort by start date (chronological for the year)
    all_races.sort(key=lambda r: r.get("startdate") or "9999-12-31")

    # R2 Phase 2 — derive stage_type from profile_icon / stage names (in place).
    annotate_stage_types(all_races)

    # Build final output — include ALL races, not just upcoming.
    # The frontend can show past + future together so users browse any race.
    output = {
        "updated_at": datetime.now().isoformat(),
        "year": YEAR,
        "total_races": len(all_races),
        "races": all_races,
    }

    with open(RACES_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    log.info(f"Done! {len(all_races)} races saved to {RACES_FILE}")
    log.info(f"  PCS data ok: {pcs_ok}  ·  fallback used: {pcs_fail}")
    log.info(f"  Startlists saved: {startlist_count}")

    print("\n" + "=" * 64)
    print(f"  SCRAPE SUMMARY")
    print(f"  Total races:  {len(all_races)}")
    print(f"  PCS enriched: {pcs_ok}")
    print(f"  Fallback:     {pcs_fail}  (races PCS didn't have data for)")
    print(f"  Startlists:   {startlist_count}")
    print("=" * 64)
    for r in all_races:
        has_sl = "📋" if (STARTLISTS_DIR / f"{r['slug']}.json").exists() else "  "
        flag = "⚠️" if r.get("_pcs_data_missing") else "  "
        print(f"  {has_sl}{flag} {r['startdate']}  {r['name']}")


if __name__ == "__main__":
    main()
