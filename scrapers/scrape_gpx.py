#!/usr/bin/env python3
"""
Scrape GPX route files from cyclingstage.com for races listed in data/races.json.

Strategy:
  1. For each race in races.json that has a cyclingstage_slug:
     a. If it's a stage race → visit the GPX index page and grab all .gpx links
     b. If it's a one-day race → visit the route page and grab the .gpx link
  2. Download each GPX file to data/gpx/{race-slug}/
  3. Update races.json with GPX availability info

Outputs:
  - data/gpx/{race-slug}/stage-1-route.gpx, stage-2-route.gpx, ...
  - data/gpx/{race-slug}/route.gpx  (for one-day races)
  - data/gpx_index.json  (index of all available GPX files)

Usage:
  python scrapers/scrape_gpx.py

Note: Must be run from an environment that can access cyclingstage.com.
"""

import json
import os
import re
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RACES_FILE = DATA_DIR / "races.json"
GPX_DIR = DATA_DIR / "gpx"
GPX_INDEX_FILE = DATA_DIR / "gpx_index.json"
DELAY_BETWEEN_REQUESTS = 1.5  # seconds

BASE_URL = "https://www.cyclingstage.com"
CDN_URL = "https://cdn.cyclingstage.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
})

# ---------------------------------------------------------------------------
# URL patterns for CyclingStage.com
#
# Stage race GPX pages:
#   https://www.cyclingstage.com/{slug}-{year}-gpx/
#   e.g. /tour-de-france-2026-gpx/  /giro-2026-gpx/  /vuelta-2026-gpx/
#
# Stage race stage pages (for smaller races without a GPX index):
#   https://www.cyclingstage.com/{slug}-{year}/route-...
#
# One-day race route pages:
#   https://www.cyclingstage.com/{slug}-{year}/route-{code}-{year}/
#   e.g. /strade-bianche-2026/route-sb-2026/
#   e.g. /amstel-gold-race-2026-2026/route-agr-2026/
#
# GPX file URLs are always on cdn.cyclingstage.com:
#   https://cdn.cyclingstage.com/images/{path}/stage-N-route.gpx
#   https://cdn.cyclingstage.com/images/{path}/route.gpx
# ---------------------------------------------------------------------------

# Known GPX page URL patterns for stage races (slug → GPX page path)
# These map the cyclingstage_slug to the GPX index page URL.
STAGE_RACE_GPX_PAGES = {
    "giro": "{slug}-{year}-gpx",
    "tour-de-france": "{slug}-{year}-gpx",
    "vuelta": "{slug}-{year}-gpx",
    "paris-nice": "{slug}-{year}-gpx",
    "tirreno-adriatico": "{slug}-{year}-gpx",
    "volta-a-catalunya": "{slug}-{year}-gpx",
    "tour-of-the-basque-country": "{slug}-{year}-gpx",
    "tour-de-romandie": "{slug}-{year}-gpx",
    "tour-de-suisse": "{slug}-{year}-gpx",
    "tour-auvergne-rhone-alpes": "{slug}-{year}-gpx",
    "tour-down-under": "{slug}-{year}-gpx",
    "uae-tour": "{slug}-{year}-gpx",
    "renewi-tour": "{slug}-{year}-gpx",
}

# Known route page patterns for one-day classics (slug → route page path)
# These are less predictable, so we also try auto-discovery.
CLASSIC_ROUTE_PAGES = {
    "omloop-het-nieuwsblad": "{slug}-{year}/route-ohn-{year}",
    "kuurne-brussels-kuurne": "{slug}-{year}/route-kbk-{year}",
    "strade-bianche": "{slug}-{year}/route-sb-{year}",
    "milan-san-remo": "{slug}-{year}/route-msr-{year}",
    "e3-saxo-classic": "{slug}-{year}/route-e3-{year}",
    "in-flanders-fields": "{slug}-{year}/route-iff-{year}",
    "dwars-door-vlaanderen": "{slug}-{year}/route-ddv-{year}",
    "tour-of-flanders": "{slug}-{year}/route-rvv-{year}",
    "paris-roubaix": "{slug}-{year}/route-pr-{year}",
    "amstel-gold-race": "{slug}-{year}/route-agr-{year}",
    "la-fleche-wallonne": "{slug}-{year}/route-fw-{year}",
    "liege-bastogne-liege": "{slug}-{year}/route-lbl-{year}",
    "clasica-de-san-sebastian": "{slug}-{year}/route-css-{year}",
    "gp-quebec": "{slug}-{year}/route-gpq-{year}",
    "gp-montreal": "{slug}-{year}/route-gpm-{year}",
    "tour-of-lombardy": "{slug}-{year}/route-il-{year}",
    "paris-tours": "{slug}-{year}/route-pt-{year}",
}


def fetch_page(url: str) -> Optional[BeautifulSoup]:
    """Fetch a page and return parsed BeautifulSoup, or None on failure."""
    try:
        log.info(f"  Fetching: {url}")
        resp = SESSION.get(url, timeout=15)
        if resp.status_code == 200:
            return BeautifulSoup(resp.text, "html.parser")
        else:
            log.warning(f"  HTTP {resp.status_code} for {url}")
            return None
    except Exception as e:
        log.warning(f"  Error fetching {url}: {e}")
        return None


def find_gpx_links(soup: BeautifulSoup) -> list[str]:
    """Extract all GPX download URLs from a page."""
    gpx_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.endswith(".gpx"):
            # Make absolute URL
            if href.startswith("http"):
                gpx_links.append(href)
            elif href.startswith("/"):
                gpx_links.append(f"{BASE_URL}{href}")
            else:
                gpx_links.append(f"{BASE_URL}/{href}")
    return gpx_links


def download_gpx(url: str, output_path: Path) -> bool:
    """Download a GPX file. Returns True on success."""
    try:
        resp = SESSION.get(url, timeout=15)
        if resp.status_code == 200 and len(resp.content) > 100:
            # Basic sanity: GPX files should contain XML
            content = resp.content.decode("utf-8", errors="ignore")
            if "<gpx" in content.lower() or "<?xml" in content.lower():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                log.info(f"  ✓ Downloaded: {output_path.name} ({len(resp.content)} bytes)")
                return True
            else:
                log.warning(f"  ✗ Not a valid GPX: {url}")
                return False
        else:
            log.warning(f"  ✗ Download failed ({resp.status_code}): {url}")
            return False
    except Exception as e:
        log.warning(f"  ✗ Download error: {e}")
        return False


def discover_gpx_links(cs_slug: str, year: int) -> list[str]:
    """
    Discover all GPX file URLs for a race by crawling cyclingstage.com.
    Tries multiple entry points and follows internal links to stage/route pages.
    Returns a deduplicated list of GPX download URLs.
    """
    found_gpx = set()
    visited = set()

    # Entry points, tried in order — first hit usually wins for grand tours
    entry_points = [
        f"{BASE_URL}/{cs_slug}-{year}-gpx/",        # grand tour GPX index
        f"{BASE_URL}/{cs_slug}-{year}-route/",      # route overview page
        f"{BASE_URL}/{cs_slug}-{year}/",            # main race page (classics)
        f"{BASE_URL}/gpx-{year}-pro-cycling-races/", # site-wide GPX list
    ]

    # Also crawl any sub-pages found that look like stage/route pages
    # (e.g. /giro-2026-route/stage-16-italy-2026/)
    to_crawl = list(entry_points)
    slug_compact = cs_slug.replace("-", "")

    while to_crawl:
        url = to_crawl.pop(0)
        if url in visited:
            continue
        visited.add(url)

        soup = fetch_page(url)
        time.sleep(DELAY_BETWEEN_REQUESTS)
        if not soup:
            continue

        # Harvest .gpx links on this page
        for link in find_gpx_links(soup):
            # Only keep GPX whose URL mentions this race (avoid grabbing
            # other races' GPX from the site-wide index page)
            if slug_compact in link.lower().replace("-", ""):
                found_gpx.add(link)

        # Follow internal links that look like sub-pages of this race.
        # We crawl from the GPX index, the route overview AND the main race
        # page: some races (e.g. Tour Auvergne-Rhône-Alpes) have no -gpx/
        # index and expose GPX only on per-stage route pages linked from the
        # main page (e.g. /stage-1-route-tara-2026/).
        if url in entry_points[:3]:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/"):
                    href = BASE_URL + href
                if not href.startswith(BASE_URL):
                    continue
                # Match URLs like {race-slug}-{year}-route/... or {race-slug}-{year}/route-...
                if f"{cs_slug}-{year}" in href and (
                    "/route-" in href or "/stage-" in href or "-route/" in href
                ):
                    if href not in visited and len(to_crawl) < 30:
                        to_crawl.append(href)

    return sorted(found_gpx)


def construct_cdn_gpx_urls(cs_slug: str, year: int, num_stages: int,
                           is_one_day: bool) -> list[str]:
    """
    Build the predictable CDN GPX URLs directly, without crawling.

    CyclingStage stores GPX at a stable path:
      https://cdn.cyclingstage.com/images/{slug}/{year}/stage-N-route.gpx
      https://cdn.cyclingstage.com/images/{slug}/{year}/route.gpx  (one-day)

    Used as a fallback for races whose HTML pages don't expose .gpx links in
    a crawlable way (e.g. no -gpx/ index page). Downloaded URLs are still
    content-validated by download_gpx, so wrong guesses fail safely.
    """
    if is_one_day:
        return [f"{CDN_URL}/images/{cs_slug}/{year}/route.gpx"]
    return [
        f"{CDN_URL}/images/{cs_slug}/{year}/stage-{n}-route.gpx"
        for n in range(1, max(num_stages, 0) + 1)
    ]


def scrape_race_gpx(cs_slug: str, year: int, race_slug: str,
                    is_one_day: bool, num_stages: int = 0) -> list[dict]:
    """
    Download all GPX files discovered for a race.
    Returns a list of {stage, filename, url, local_path} dicts.
    """
    results = []
    race_gpx_dir = GPX_DIR / race_slug

    gpx_links = discover_gpx_links(cs_slug, year)
    if not gpx_links:
        # Crawling found nothing — fall back to the predictable CDN paths.
        log.info("  No GPX links discovered by crawling; trying CDN paths")
        gpx_links = construct_cdn_gpx_urls(cs_slug, year, num_stages, is_one_day)
    if not gpx_links:
        return results

    log.info(f"  Discovered {len(gpx_links)} GPX link(s)")

    for link in gpx_links:
        filename = link.split("/")[-1]
        # For one-day races, normalise to route.gpx when there's only one
        if is_one_day and len(gpx_links) == 1:
            output_filename = "route.gpx"
        else:
            output_filename = filename
        output_path = race_gpx_dir / output_filename

        if download_gpx(link, output_path):
            stage_match = re.search(r"stage-(\d+)", filename)
            stage_num = int(stage_match.group(1)) if stage_match else None
            results.append({
                "stage": stage_num,
                "filename": output_filename,
                "url": link,
                "local_path": str(output_path.relative_to(DATA_DIR)),
            })

        time.sleep(0.5)

    return results


def main():
    # Load race data from Step 1
    if not RACES_FILE.exists():
        log.error(f"races.json not found at {RACES_FILE}. Run scrape_races.py first!")
        return

    with open(RACES_FILE) as f:
        races_data = json.load(f)

    GPX_DIR.mkdir(parents=True, exist_ok=True)

    # Preserve La Flamme Rouge fallback entries: scrape_lfr.py runs locally only
    # (not in Actions), so this daily rebuild must not wipe the GPX it filled for
    # races cyclingstage misses. We keep an LFR entry when cyclingstage still finds
    # nothing AND the LFR files are still on disk.
    prior_lfr = {}
    if GPX_INDEX_FILE.exists():
        try:
            prior = json.loads(GPX_INDEX_FILE.read_text(encoding="utf-8"))
            for slug, e in prior.get("races", {}).items():
                if e.get("source") == "la_flamme_rouge" and e.get("gpx_available"):
                    prior_lfr[slug] = e
        except Exception:
            pass

    year = races_data.get("year", datetime.now().year)
    gpx_index = {
        "updated_at": datetime.now().isoformat(),
        "year": year,
        "races": {},
    }

    total_gpx_files = 0

    for race in races_data["races"]:
        cs_slug = race.get("cyclingstage_slug")
        race_slug = race["slug"]
        race_name = race["name"]
        is_one_day = race.get("is_one_day_race", False)

        if not cs_slug:
            log.info(f"Skipping {race_name} — no CyclingStage.com mapping")
            gpx_index["races"][race_slug] = {
                "name": race_name,
                "gpx_available": False,
                "reason": "no_cyclingstage_mapping",
                "files": [],
            }
            continue

        log.info(f"\n{'='*50}")
        log.info(f"Processing: {race_name} (cs_slug={cs_slug})")
        log.info(f"{'='*50}")

        num_stages = len(race.get("stages", []))
        files = scrape_race_gpx(cs_slug, year, race_slug, is_one_day, num_stages)

        # cyclingstage found nothing → keep the LFR fallback if its files remain.
        if not files and race_slug in prior_lfr:
            e = prior_lfr[race_slug]
            if all((DATA_DIR / f["local_path"]).exists() for f in e.get("files", [])):
                gpx_index["races"][race_slug] = e
                total_gpx_files += e.get("total_files", 0)
                log.info(f"  → preserved {e.get('total_files', 0)} La Flamme Rouge "
                         f"file(s) for {race_name}")
                continue

        gpx_index["races"][race_slug] = {
            "name": race_name,
            "gpx_available": len(files) > 0,
            "total_files": len(files),
            "files": files,
        }

        total_gpx_files += len(files)
        log.info(f"  → {len(files)} GPX file(s) for {race_name}")

    # Save GPX index
    with open(GPX_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(gpx_index, f, indent=2, ensure_ascii=False)

    log.info(f"\n{'='*60}")
    log.info(f"  GPX SCRAPE COMPLETE")
    log.info(f"  Total GPX files downloaded: {total_gpx_files}")
    log.info(f"  Index saved to: {GPX_INDEX_FILE}")
    log.info(f"{'='*60}")

    # Summary
    print(f"\n  GPX availability:")
    for slug, info in gpx_index["races"].items():
        status = "✓" if info["gpx_available"] else "✗"
        count = info.get("total_files", 0)
        print(f"    {status} {info['name']:35s} {count} file(s)")


if __name__ == "__main__":
    main()
