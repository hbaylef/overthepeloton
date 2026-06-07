#!/usr/bin/env python3
"""
R-deferred — La Flamme Rouge (LFR) GPX FALLBACK for races cyclingstage.com misses.

Primary GPX source is cyclingstage.com (scrape_gpx.py). This script fills the gap
for **UCI WorldTour + ProSeries** races that still have no GPX, using LFR's public
"maps" section. It is a FALLBACK: it only touches races that are still
`gpx_available: false` in data/gpx_index.json, and MERGES its results in (it never
rebuilds the index), tagging them `"source": "la_flamme_rouge"`.

LFR mechanics (no login needed for the public maps section):
  - Race listing:  /maps/races?count=0&page={p}&calendar[0]={cal}&year[0]={yr}&name={q}
                   calendar codes: 1=UWT, 2=Europe, 3=Americas, 4=Asia, 8=WC
  - Race page:     /maps/races/view/{race_id}/{name}   (lists the stage tracks)
  - GPX download:  /maps/viewtrack/gpx/{track_id}      (a ready GPX file)

⚠️ RUN LOCALLY ONLY. LFR blocks/limits automated access and GH Actions IPs; this is
not wired into the daily workflow. Be polite — random delays between requests.

⚠️ This machine has a TLS-intercepting proxy that breaks Python cert verification.
For THIS local-only tool you may pass --insecure (or set LFR_INSECURE=1) to skip
verification. Do NOT copy that into the cron scrapers.

Because LFR's exact HTML can't be inspected from this dev sandbox, the parsing is
defensive and verbose; the first real run is a calibration pass. Use --dry-run to
see what it resolves without downloading, and LFR_RACE_OVERRIDES below to pin a
race_id when the name auto-match misses.

Usage:
  python scrapers/scrape_lfr.py                 # fill all missing WT+ProSeries
  python scrapers/scrape_lfr.py --dry-run       # resolve only, download nothing
  python scrapers/scrape_lfr.py --only tour-de-suisse-2026
  python scrapers/scrape_lfr.py --insecure      # behind a TLS-intercepting proxy
"""

import argparse
import json
import logging
import os
import random
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RACES_FILE = DATA_DIR / "races.json"
GPX_DIR = DATA_DIR / "gpx"
GPX_INDEX_FILE = DATA_DIR / "gpx_index.json"

BASE_URL = "https://www.la-flamme-rouge.eu"
SOURCE_TAG = "la_flamme_rouge"

# Only fill these UCI classes (the user wants WorldTour + ProSeries only).
TARGET_TOURS = {"1.UWT", "2.UWT", "1.Pro", "2.Pro"}

# LFR race-listing calendar codes to search (UWT + the continental calendars that
# carry ProSeries races). World Championships (8) excluded.
CALENDAR_CODES = [1, 2, 3, 4]

# Politeness: randomised delay (seconds) between LFR requests. LFR tolerates the
# public maps section but blocks hammering — keep these generous.
DELAY_RANGE = (3.0, 7.0)
MAX_LISTING_PAGES = 12          # safety cap when crawling a calendar's listing

# Pin a race when the name auto-match fails: race_slug -> LFR race_id (the number
# in /maps/races/view/{id}/...). Fill in after a --dry-run shows the candidates.
LFR_RACE_OVERRIDES: dict = {
    # "tour-de-suisse-2026": 12345,
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ===========================================================================
#  Pure helpers (no network — unit-tested in test_scrape_lfr.py)
# ===========================================================================
_STOPWORDS = {"tour", "de", "la", "le", "du", "of", "the", "et", "a", "grand",
              "prix", "gp", "race", "classic", "ronde", "van"}


def normalize_name(s: str) -> str:
    """Lowercase, strip accents, drop a trailing year and punctuation → a compact
    comparable string. 'Critérium du Dauphiné 2026' → 'criterium dauphine'."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"\b(19|20)\d{2}\b", " ", s)        # drop years
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_tokens(s: str) -> set:
    """Significant tokens of a normalized name (stopwords + short tokens dropped)."""
    return {t for t in normalize_name(s).split()
            if t not in _STOPWORDS and len(t) > 2}


def name_match_score(target: str, candidate: str) -> float:
    """0..1 similarity by significant-token overlap (Jaccard). Used to pick the LFR
    race that matches one of ours."""
    a, b = name_tokens(target), name_tokens(candidate)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def parse_race_listing(html: str) -> List[dict]:
    """Extract races from an LFR /maps/races listing page.
    Returns [{race_id, name, view_url}] for every /maps/races/view/{id}/{slug} link."""
    out, seen = [], set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        m = re.search(r"/maps/races/view/(\d+)/([^/?\"'#]+)", a["href"])
        if not m:
            continue
        rid = int(m.group(1))
        if rid in seen:
            continue
        seen.add(rid)
        name = a.get_text(strip=True) or m.group(2).replace("-", " ")
        out.append({"race_id": rid,
                    "name": name,
                    "view_url": f"{BASE_URL}/maps/races/view/{rid}/{m.group(2)}"})
    return out


def best_race_match(target_name: str, candidates: List[dict],
                    threshold: float = 0.34) -> Optional[dict]:
    """Pick the listing candidate whose name best matches `target_name` (above a
    minimum score). Returns the candidate dict (with an added `score`) or None."""
    best, best_score = None, 0.0
    for c in candidates:
        sc = name_match_score(target_name, c["name"])
        if sc > best_score:
            best, best_score = c, sc
    if best and best_score >= threshold:
        return {**best, "score": round(best_score, 2)}
    return None


def parse_stage_tracks(html: str) -> List[int]:
    """Extract the ordered, de-duplicated stage track ids from a race-view page
    (any href like /maps/viewtrack/{id} or /maps/viewtrack/hd/{id})."""
    ids, seen = [], set()
    for m in re.finditer(r"/maps/viewtrack/(?:hd/)?(\d+)", html):
        tid = int(m.group(1))
        if tid not in seen:
            seen.add(tid)
            ids.append(tid)
    return ids


def stage_filename(i: int, total: int, is_one_day: bool) -> str:
    """Output filename for the i-th (1-based) track of a race."""
    if is_one_day or total <= 1:
        return "route.gpx"
    return f"stage-{i}-route.gpx"


# ===========================================================================
#  Network IO
# ===========================================================================
def make_session(insecure: bool) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    s.verify = not insecure
    if insecure:
        log.warning("TLS verification DISABLED (--insecure) — local proxy workaround only.")
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return s


def polite_sleep():
    time.sleep(random.uniform(*DELAY_RANGE))


def fetch_html(session: requests.Session, url: str) -> Optional[str]:
    try:
        log.info(f"  GET {url}")
        r = session.get(url, timeout=20)
        if r.status_code == 200:
            return r.text
        log.warning(f"    HTTP {r.status_code}")
    except Exception as e:
        log.warning(f"    error: {e}")
    return None


def download_gpx(session: requests.Session, track_id: int, out_path: Path) -> Optional[str]:
    """Download one track's GPX. Content-validated (same gate as scrape_gpx.py).
    Returns the source URL on success, else None."""
    url = f"{BASE_URL}/maps/viewtrack/gpx/{track_id}"
    try:
        r = session.get(url, timeout=20)
        if r.status_code == 200 and len(r.content) > 100:
            text = r.content.decode("utf-8", errors="ignore").lower()
            if "<gpx" in text or "<?xml" in text:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(r.content)
                log.info(f"    ✓ {out_path.name} ({len(r.content)} bytes)")
                return url
            log.warning(f"    ✗ not GPX (track {track_id})")
        else:
            log.warning(f"    ✗ HTTP {r.status_code} (track {track_id})")
    except Exception as e:
        log.warning(f"    ✗ download error (track {track_id}): {e}")
    return None


# ===========================================================================
#  Resolve + scrape one race
# ===========================================================================
def find_race_page(session: requests.Session, race: dict, year: int) -> Optional[dict]:
    """Find this race's LFR race-view page. Honours LFR_RACE_OVERRIDES, else
    searches the listing by name across the target calendars."""
    slug = race["slug"]
    if slug in LFR_RACE_OVERRIDES:
        rid = LFR_RACE_OVERRIDES[slug]
        return {"race_id": rid, "name": race["name"],
                "view_url": f"{BASE_URL}/maps/races/view/{rid}/{race.get('pcs_slug', slug)}",
                "score": 1.0}

    candidates: List[dict] = []
    q = normalize_name(race["name"]).replace(" ", "+")
    for cal in CALENDAR_CODES:
        for page in range(MAX_LISTING_PAGES):
            url = (f"{BASE_URL}/maps/races?count=0&page={page}"
                   f"&calendar%5B0%5D={cal}&year%5B0%5D={year}&years=&name={q}")
            html = fetch_html(session, url)
            polite_sleep()
            if not html:
                break
            rows = parse_race_listing(html)
            if not rows:
                break
            candidates.extend(rows)
            if len(rows) < 10:          # last page of this calendar's results
                break
        match = best_race_match(race["name"], candidates)
        if match:
            log.info(f"  matched '{race['name']}' → LFR #{match['race_id']} "
                     f"'{match['name']}' (score {match['score']})")
            return match
    log.warning(f"  no LFR race match for '{race['name']}' "
                f"({len(candidates)} candidates seen)")
    return None


def scrape_race(session: requests.Session, race: dict, year: int,
                dry_run: bool) -> List[dict]:
    """Resolve a race on LFR and download its stage GPX. Returns gpx_index `files`."""
    page = find_race_page(session, race, year)
    if not page:
        return []
    html = fetch_html(session, page["view_url"])
    polite_sleep()
    if not html:
        return []
    track_ids = parse_stage_tracks(html)
    if not track_ids:
        log.warning(f"  no stage tracks found on {page['view_url']}")
        return []

    is_one_day = race.get("is_one_day_race", False)
    log.info(f"  {len(track_ids)} track(s) found"
             + (" [dry-run]" if dry_run else ""))
    if dry_run:
        return [{"stage": (None if is_one_day else i + 1),
                 "track_id": t} for i, t in enumerate(track_ids)]

    files = []
    race_dir = GPX_DIR / race["slug"]
    for i, tid in enumerate(track_ids):
        fname = stage_filename(i + 1, len(track_ids), is_one_day)
        out_path = race_dir / fname
        src = download_gpx(session, tid, out_path)
        polite_sleep()
        if src:
            files.append({
                "stage": None if is_one_day else i + 1,
                "filename": fname,
                "url": src,
                "local_path": str(out_path.relative_to(DATA_DIR)),
            })
    return files


# ===========================================================================
#  Index merge + main
# ===========================================================================
def targets(races: List[dict], gpx_index: dict, only: Optional[str]) -> List[dict]:
    """WT+ProSeries races that still lack GPX (and aren't already LFR-sourced),
    optionally narrowed to a single slug."""
    out = []
    for r in races:
        if r.get("uci_tour") not in TARGET_TOURS:
            continue
        if only and r["slug"] != only:
            continue
        entry = gpx_index.get("races", {}).get(r["slug"], {})
        if entry.get("gpx_available"):
            continue
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser(description="LFR GPX fallback (WT + ProSeries).")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve races/tracks but download nothing")
    ap.add_argument("--only", metavar="SLUG", help="limit to one race slug")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification (local TLS-proxy workaround)")
    args = ap.parse_args()
    insecure = args.insecure or os.environ.get("LFR_INSECURE") == "1"

    if not RACES_FILE.exists() or not GPX_INDEX_FILE.exists():
        log.error("races.json / gpx_index.json missing — run scrape_races + scrape_gpx first.")
        return
    races_data = json.loads(RACES_FILE.read_text(encoding="utf-8"))
    gpx_index = json.loads(GPX_INDEX_FILE.read_text(encoding="utf-8"))
    year = races_data.get("year", datetime.now().year)

    todo = targets(races_data["races"], gpx_index, args.only)
    log.info(f"{len(todo)} WT+ProSeries race(s) missing GPX to try on LFR")

    session = make_session(insecure)
    filled = 0
    for race in todo:
        log.info(f"\n{'='*54}\n{race['name']}  [{race.get('uci_tour')}]\n{'='*54}")
        files = scrape_race(session, race, year, args.dry_run)
        if args.dry_run:
            for f in files:
                log.info(f"    stage {f['stage']}: track {f['track_id']}")
            continue
        if files:
            gpx_index["races"][race["slug"]] = {
                "name": race["name"],
                "gpx_available": True,
                "total_files": len(files),
                "source": SOURCE_TAG,
                "files": files,
            }
            filled += 1
            log.info(f"  → filled {len(files)} file(s) from LFR")

    if not args.dry_run and filled:
        gpx_index["updated_at"] = datetime.now().isoformat()
        GPX_INDEX_FILE.write_text(
            json.dumps(gpx_index, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"\nUpdated {GPX_INDEX_FILE.name}: filled {filled} race(s) from LFR.")
    elif not args.dry_run:
        log.info("\nNo races filled from LFR.")


if __name__ == "__main__":
    main()
