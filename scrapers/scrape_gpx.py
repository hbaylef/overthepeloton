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

import db  # local module: Turso/SQLite store (build-order step 2)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Legacy on-disk paths, read once to import already-downloaded routes into the
# store (so we don't re-crawl/re-download them). GPX now lives in Turso.
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


def download_gpx_content(url: str) -> Optional[str]:
    """Download a GPX file and return its XML text, or None if it isn't valid."""
    try:
        resp = SESSION.get(url, timeout=15)
        if resp.status_code == 200 and len(resp.content) > 100:
            # Basic sanity: GPX files should contain XML
            content = resp.content.decode("utf-8", errors="ignore")
            if "<gpx" in content.lower() or "<?xml" in content.lower():
                log.info(f"  ✓ Downloaded: {url.split('/')[-1]} ({len(resp.content)} bytes)")
                return content
            log.warning(f"  ✗ Not a valid GPX: {url}")
        else:
            log.warning(f"  ✗ Download failed ({resp.status_code}): {url}")
    except Exception as e:
        log.warning(f"  ✗ Download error: {e}")
    return None


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


def scrape_race_gpx(client, cs_slug: str, year: int, race_slug: str,
                    is_one_day: bool, num_stages: int = 0) -> list[dict]:
    """
    Discover + download all GPX files for a race and store them in the DB.
    Returns a list of {stage, filename, url} dicts for what was stored.
    """
    results = []

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

        content = download_gpx_content(link)
        if content:
            stage_match = re.search(r"stage-(\d+)", filename)
            stage_num = int(stage_match.group(1)) if stage_match else None
            try:
                db.put_gpx(client, race_slug, output_filename, content,
                           stage=stage_num, source="cyclingstage", url=link)
                results.append({"stage": stage_num, "filename": output_filename, "url": link})
            except Exception as e:
                log.warning(f"  ✗ Could not store {output_filename}: {e}")

        time.sleep(0.5)

    return results


def import_disk_gpx(client, slug: str, disk_index: dict) -> int:
    """One-time bootstrap: import a race's already-committed GPX files into the
    store instead of re-downloading them (published routes never change). Reads
    file metadata (stage/url/source) from the legacy gpx_index.json and the
    bytes from data/gpx/. Returns the number of files imported."""
    entry = disk_index.get(slug)
    if not entry:
        return 0
    source = entry.get("source", "cyclingstage")
    n = 0
    for f in entry.get("files", []):
        path = DATA_DIR / f.get("local_path", "")
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            db.put_gpx(client, slug, f.get("filename"), content,
                       stage=f.get("stage"), source=source, url=f.get("url"))
        except Exception as e:
            log.warning(f"    could not import {path.name}: {e}")
            continue
        n += 1
    return n


def main():
    client = db.open_db()
    log.info(f"GPX store: {'remote Turso' if db.is_remote() else 'local SQLite file'}")

    races = list(db.get_all_documents(client, db.KIND_RACE).values())
    if not races:
        log.error("No races in the store — run scrape_races.py first.")
        client.close()
        return

    # Legacy on-disk index, used once to import already-downloaded routes (incl.
    # La Flamme Rouge files) into the store without re-downloading them.
    disk_index = {}
    if GPX_INDEX_FILE.exists():
        try:
            disk_index = json.loads(GPX_INDEX_FILE.read_text(encoding="utf-8")).get("races", {})
        except Exception:
            pass

    # Over-scraping counters (build-order step 3a/3c).
    skipped_have = imported = crawled = no_map = 0
    downloaded_files = imported_files = 0

    for race in races:
        cs_slug = race.get("cyclingstage_slug")
        race_slug = race["slug"]
        race_name = race["name"]
        is_one_day = race.get("is_one_day_race", False)

        if not cs_slug:
            no_map += 1
            continue

        # 1) Already in the store → published routes never change, so never
        #    re-crawl or re-download. This is the GPX over-scraping fix.
        if db.has_gpx(client, race_slug):
            skipped_have += 1
            continue

        # 2) Not in the store yet, but we already have it on disk from a prior
        #    run → import it (no network) rather than re-downloading.
        n = import_disk_gpx(client, race_slug, disk_index)
        if n:
            imported += 1
            imported_files += n
            log.info(f"  → imported {n} existing GPX file(s) for {race_name} (no download)")
            continue

        # 3) Genuinely missing → crawl cyclingstage and download into the store.
        log.info(f"\n{'='*50}\nProcessing: {race_name} (cs_slug={cs_slug})\n{'='*50}")
        year = race.get("year") or datetime.now().year
        files = scrape_race_gpx(client, cs_slug, year, race_slug, is_one_day,
                                len(race.get("stages", [])))
        if files:
            crawled += 1
            downloaded_files += len(files)
        log.info(f"  → {len(files)} GPX file(s) downloaded for {race_name}")

    total_stored = len(db.gpx_slugs(client))
    client.close()

    print("\n" + "=" * 64)
    print("  GPX SCRAPE SUMMARY")
    print(f"  Races with GPX already in store (skipped, no network): {skipped_have}")
    print(f"  Races imported from disk (no download):                {imported} ({imported_files} files)")
    print(f"  Races crawled + downloaded:                            {crawled} ({downloaded_files} files)")
    print(f"  Races with no cyclingstage mapping:                    {no_map}")
    print(f"  Total races with GPX in store:                         {total_stored}")
    print("=" * 64)


if __name__ == "__main__":
    main()
