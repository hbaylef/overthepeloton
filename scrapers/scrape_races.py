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
import time
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

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
# UCI World Tour race slugs (PCS URL format).
# Verified against procyclingstats.com/races.php?year=2026&circuit=1
#
# NOTE: Some race slugs change when races get renamed. The script will
# log warnings for any slug that returns a 404. If a slug breaks,
# find the new one at procyclingstats.com and update this list.
# ---------------------------------------------------------------------------
WORLD_TOUR_RACES = [
    # January
    "race/tour-down-under/{year}",
    # February
    "race/uae-tour/{year}",
    "race/omloop-het-nieuwsblad/{year}",
    "race/kuurne-brussel-kuurne/{year}",
    # March
    "race/strade-bianche/{year}",
    "race/paris-nice/{year}",
    "race/tirreno-adriatico/{year}",
    "race/milano-sanremo/{year}",
    "race/volta-a-catalunya/{year}",
    "race/e3-saxo-classic/{year}",
    # Renamed in 2026: Gent-Wevelgem -> In Flanders Fields
    "race/gent-wevelgem/{year}",
    "race/dwars-door-vlaanderen/{year}",
    # April
    "race/ronde-van-vlaanderen/{year}",
    "race/paris-roubaix/{year}",
    "race/itzulia-basque-country/{year}",
    "race/amstel-gold-race/{year}",
    "race/la-fleche-wallonne/{year}",
    "race/liege-bastogne-liege/{year}",
    "race/tour-de-romandie/{year}",
    # May
    "race/eschborn-frankfurt/{year}",
    "race/giro-d-italia/{year}",
    # June
    "race/tour-de-suisse/{year}",
    # Renamed in 2026: Critérium du Dauphiné -> Tour Auvergne-Rhône-Alpes
    "race/criterium-du-dauphine/{year}",
    # July
    "race/tour-de-france/{year}",
    # August
    "race/clasica-ciclista-san-sebastian/{year}",
    "race/tour-of-poland/{year}",
    "race/renewi-tour/{year}",
    "race/vuelta-a-espana/{year}",
    # September
    "race/bretagne-classic/{year}",
    "race/gp-quebec/{year}",
    "race/gp-montreal/{year}",
    "race/tour-of-luxembourg/{year}",
    # October
    "race/il-lombardia/{year}",
    "race/paris-tours/{year}",
    "race/japan-cup/{year}",
    "race/tour-of-guangxi/{year}",
]

# Mapping of PCS race slugs to CyclingStage.com GPX page slugs.
# Used later by the GPX scraper to find the right download page.
PCS_TO_CYCLINGSTAGE = {
    "tour-down-under": "tour-down-under",
    "uae-tour": "uae-tour",
    "omloop-het-nieuwsblad": "omloop-het-nieuwsblad",
    "kuurne-brussel-kuurne": "kuurne-brussels-kuurne",
    "strade-bianche": "strade-bianche",
    "paris-nice": "paris-nice",
    "tirreno-adriatico": "tirreno-adriatico",
    "milano-sanremo": "milan-san-remo",
    "volta-a-catalunya": "volta-a-catalunya",
    "e3-saxo-classic": "e3-saxo-classic",
    "gent-wevelgem": "in-flanders-fields",
    "dwars-door-vlaanderen": "dwars-door-vlaanderen",
    "ronde-van-vlaanderen": "tour-of-flanders",
    "paris-roubaix": "paris-roubaix",
    "itzulia-basque-country": "tour-of-the-basque-country",
    "amstel-gold-race": "amstel-gold-race",
    "la-fleche-wallonne": "la-fleche-wallonne",
    "liege-bastogne-liege": "liege-bastogne-liege",
    "tour-de-romandie": "tour-de-romandie",
    "eschborn-frankfurt": None,  # not on CyclingStage
    "giro-d-italia": "giro",
    "tour-de-suisse": "tour-de-suisse",
    "criterium-du-dauphine": "tour-auvergne-rhone-alpes",
    "tour-de-france": "tour-de-france",
    "clasica-ciclista-san-sebastian": "clasica-de-san-sebastian",
    "tour-of-poland": None,
    "renewi-tour": "renewi-tour",
    "vuelta-a-espana": "vuelta",
    "bretagne-classic": None,
    "gp-quebec": "gp-quebec",
    "gp-montreal": "gp-montreal",
    "tour-of-luxembourg": None,
    "il-lombardia": "tour-of-lombardy",
    "paris-tours": "paris-tours",
    "japan-cup": None,
    "tour-of-guangxi": None,
}


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
        cs_slug = PCS_TO_CYCLINGSTAGE.get(slug)

        result = {
            "pcs_url": race_url,
            "slug": make_slug(race_url),
            "pcs_slug": slug,
            "cyclingstage_slug": cs_slug,
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


def is_upcoming(race_info: dict) -> bool:
    """Check if a race is upcoming (hasn't finished yet)."""
    enddate_str = race_info.get("enddate") or race_info.get("startdate")
    if not enddate_str:
        return True  # can't tell → include it
    try:
        enddate = datetime.strptime(enddate_str, "%Y-%m-%d").date()
        return enddate >= date.today()
    except (ValueError, TypeError):
        return True


def main():
    # Create output directories
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STARTLISTS_DIR.mkdir(parents=True, exist_ok=True)

    all_races = []
    startlist_count = 0

    for race_template in WORLD_TOUR_RACES:
        race_url = race_template.format(year=YEAR)

        # 1) Scrape race info
        info = scrape_race_info(race_url)
        time.sleep(DELAY_BETWEEN_REQUESTS)

        if info is None:
            continue

        all_races.append(info)

        # 2) Fetch startlist only for upcoming races
        if is_upcoming(info):
            riders = scrape_startlist(race_url)
            time.sleep(DELAY_BETWEEN_REQUESTS)

            if riders and len(riders) > 0:
                slug = info["slug"]

                # Save individual startlist file
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

    # Filter to upcoming races for the main output
    upcoming = [r for r in all_races if is_upcoming(r)]

    # Sort by start date
    upcoming.sort(key=lambda r: r.get("startdate") or "9999-12-31")

    # Build final output
    output = {
        "updated_at": datetime.now().isoformat(),
        "year": YEAR,
        "total_races": len(upcoming),
        "races": upcoming,
    }

    with open(RACES_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    log.info(f"Done! {len(upcoming)} upcoming races saved to {RACES_FILE}")
    log.info(f"  {startlist_count} startlists saved to {STARTLISTS_DIR}/")

    # Summary
    print("\n" + "=" * 60)
    print(f"  SCRAPE SUMMARY")
    print(f"  Upcoming races: {len(upcoming)}")
    print(f"  Startlists:     {startlist_count}")
    print(f"  Output:         {RACES_FILE}")
    print("=" * 60)
    for r in upcoming:
        status = "📋" if (STARTLISTS_DIR / f"{r['slug']}.json").exists() else "  "
        print(f"  {status} {r['startdate']}  {r['name']}")


if __name__ == "__main__":
    main()
