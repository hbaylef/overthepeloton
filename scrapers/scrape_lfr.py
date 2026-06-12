#!/usr/bin/env python3
"""
La Flamme Rouge (LFR) GPX FALLBACK — Phase B (attended CDP-Chrome → Turso).

Primary GPX source is cyclingstage.com (scrape_gpx.py). This script fills the gap
for **UCI WorldTour + ProSeries** races that still have NO GPX in the store, using
LFR's public "maps" section. It is a FALLBACK: it only touches races that have no
GPX yet (`db.has_gpx` is False) and NEVER overwrites an existing route.

Why CDP-Chrome instead of plain HTTP? LFR sits behind a Cloudflare **managed
challenge** (pre-login) that `requests`/`cloudscraper` cannot pass. So we drive a
REAL Chrome the user already cleared once:

  1. The user launches their normal Chrome with remote debugging + a dedicated
     profile that persists `cf_clearance` between runs:

         chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\\lfr-profile"

     …then visits https://www.la-flamme-rouge.eu once and passes Cloudflare.
  2. This script CONNECTS to that running browser over CDP
     (`connect_over_cdp("http://localhost:9222")`) — it does NOT launch a fresh
     automated Chrome (that sets navigator.webdriver and gets challenged).
  3. ALL network happens INSIDE the browser context (real TLS fingerprint +
     cf_clearance): listing/race pages via navigate + parse `page.content()`;
     GPX via an in-page `fetch()` of /maps/viewtrack/gpx/{id}. If a challenge
     appears mid-run, solve it in the visible window — the script waits.

This also dodges the corporate TLS proxy for LFR traffic (Chrome trusts the
proxy's root cert at the OS level). LFR uploads GPX weeks ahead, so this is an
occasional ATTENDED run, NOT a cron job — it is deliberately NOT in the daily
Actions workflow (LFR also blocks Actions IPs).

LFR mechanics (no login needed for the public maps section):
  - Race listing:  /maps/races?count=0&page={p}&calendar[0]={cal}&year[0]={yr}&name={q}
                   calendar codes: 1=UWT, 2=Europe, 3=Americas, 4=Asia, 8=WC
  - Race page:     /maps/races/view/{race_id}/{name}   (lists the stage tracks)
  - GPX download:  /maps/viewtrack/gpx/{track_id}      (a ready GPX file)

⚠️ TLS gotcha for the WRITE side: the Python → Turso write does NOT go through the
browser; it goes through the corporate proxy. If the Turso write fails with a cert
error, point the libsql client at the corporate CA bundle before running:
    set REQUESTS_CA_BUNDLE=C:\\path\\to\\corp-ca.pem
    set SSL_CERT_FILE=%REQUESTS_CA_BUNDLE%
The script verifies each filled race actually landed in the store (has_gpx) and
shouts if a write silently didn't take.

Requirements: playwright is a LOCAL/dev dependency only (requirements-dev.txt),
NOT in CI. Install once:  pip install -r requirements-dev.txt  &&  playwright install chromium

Usage:
  python scrapers/scrape_lfr.py                  # fill all missing WT+ProSeries
  python scrapers/scrape_lfr.py --dry-run        # resolve races/tracks, store nothing
  python scrapers/scrape_lfr.py --only tour-de-suisse-2026
  python scrapers/scrape_lfr.py --cdp-url http://localhost:9222
"""

import argparse
import logging
import random
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from bs4 import BeautifulSoup

import db  # local module: Turso/SQLite store

# This tool runs LOCALLY on Windows; its logs use ✓/✗/→/⚠ symbols. Force UTF-8 on
# the console streams so they don't crash a default cp1252 terminal.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

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

# How long (seconds) to wait for the user to solve a Cloudflare challenge in the
# visible Chrome window before giving up on a page.
CHALLENGE_WAIT_SECONDS = 180

# Pin a race when the name auto-match fails: race_slug -> LFR race_id (the number
# in /maps/races/view/{id}/...). Fill in after a --dry-run shows the candidates.
LFR_RACE_OVERRIDES: dict = {
    # "tour-de-suisse-2026": 12345,
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ===========================================================================
#  Pure helpers (no network, no browser — unit-tested in test_scrape_lfr.py)
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

    Real LFR markup (verified 2026-06): each race is a table row; the map link is
    `/maps/races/view/{year}/{race_id}` (an arrow icon, NO name in the link), and
    the race name sits in a sibling cell as
    `<div class="displayRaceLine__logo"><strong>Name</strong></div>`.
    Returns [{race_id, name, view_url}] per race (de-duplicated by race_id).
    """
    out, seen = [], set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        m = re.search(r"/maps/races/view/(\d+)/(\d+)", a["href"])
        if not m:
            continue
        year, rid = m.group(1), int(m.group(2))
        if rid in seen:
            continue
        # The name lives elsewhere in the same row, not in this link.
        name = ""
        row = a.find_parent("tr")
        if row:
            logo = row.find("div", class_="displayRaceLine__logo")
            strong = (logo.find("strong") if logo else None) or row.find("strong")
            if strong:
                name = strong.get_text(strip=True)
        if not name:
            continue  # can't match a race we can't name
        seen.add(rid)
        out.append({"race_id": rid,
                    "name": name,
                    "view_url": f"{BASE_URL}/maps/races/view/{year}/{rid}"})
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


def looks_like_gpx(text: Optional[str]) -> bool:
    """Content gate: same check scrape_gpx.py uses. A real GPX is non-trivial XML."""
    if not text or len(text) < 100:
        return False
    head = text[:2000].lower()
    return "<gpx" in head or "<?xml" in head


def race_starts_on_or_after(race: dict, cutoff: date) -> bool:
    """True if the race's startdate is on/after `cutoff`. Missing/unparseable
    dates → False (we only harvest races we can confirm are upcoming)."""
    sd = race.get("startdate")
    if not sd:
        return False
    try:
        return datetime.strptime(str(sd)[:10], "%Y-%m-%d").date() >= cutoff
    except ValueError:
        return False


def targets(races: List[dict], has_gpx: Callable[[str], bool],
            only: Optional[str] = None,
            start_on_or_after: Optional[date] = None) -> List[dict]:
    """WT+ProSeries races that still lack GPX, optionally narrowed to one slug.

    `has_gpx(slug)` is a predicate (in production `db.has_gpx`); kept as an
    argument so the targeting logic stays unit-testable without a database.
    `start_on_or_after` limits to upcoming races (skipped when `only` names a
    race explicitly — an explicit pick wins over the date window).
    """
    out = []
    for r in races:
        if r.get("uci_tour") not in TARGET_TOURS:
            continue
        if only:
            if r["slug"] != only:
                continue
        elif start_on_or_after and not race_starts_on_or_after(r, start_on_or_after):
            continue
        if has_gpx(r["slug"]):
            continue
        out.append(r)
    return out


def polite_sleep():
    time.sleep(random.uniform(*DELAY_RANGE))


# ===========================================================================
#  Browser IO — a REAL Chrome driven over CDP (Playwright), lazily imported so
#  the pure helpers/tests don't need playwright installed.
# ===========================================================================
class CDPFetcher:
    """Connects to a user-launched Chrome (remote-debugging) and does all LFR
    network INSIDE it: page navigation for HTML, in-page fetch() for GPX. This
    inherits the browser's cf_clearance + real TLS fingerprint, so Cloudflare
    lets the requests through."""

    def __init__(self, cdp_url: str = "http://localhost:9222",
                 challenge_wait: int = CHALLENGE_WAIT_SECONDS,
                 dump_dir: Optional[Path] = None):
        from playwright.sync_api import sync_playwright  # local-only dep
        self._pw = sync_playwright().start()
        log.info(f"Connecting to Chrome over CDP at {cdp_url} …")
        self.browser = self._pw.chromium.connect_over_cdp(cdp_url)
        ctx = (self.browser.contexts[0] if self.browser.contexts
               else self.browser.new_context())
        self.page = ctx.pages[0] if ctx.pages else ctx.new_page()
        self.challenge_wait = challenge_wait
        self.dump_dir = dump_dir          # if set, save each fetched page's HTML
        self._dump_seq = 0
        log.info("Connected. Using the existing browser context (cf_clearance reused).")

    def _maybe_dump(self, url: str, content: str):
        if not self.dump_dir or content is None:
            return
        self._dump_seq += 1
        try:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            tag = re.sub(r"[^a-z0-9]+", "_", url.lower())[:60].strip("_")
            path = self.dump_dir / f"{self._dump_seq:02d}_{tag}.html"
            path.write_text(content, encoding="utf-8")
            log.info(f"    [dump] {path}  ({len(content)} bytes)")
        except Exception as e:
            log.warning(f"    dump failed: {e}")

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass

    # -- challenge handling --------------------------------------------------
    def _looks_challenged(self) -> bool:
        try:
            title = (self.page.title() or "").lower()
        except Exception:
            return False
        if "just a moment" in title or "attention required" in title:
            return True
        try:
            html = self.page.content().lower()
        except Exception:
            return False
        return ("challenge-platform" in html or "cf_chl_opt" in html
                or "_cf_chl_" in html)

    def _wait_out_challenge(self):
        waited = 0
        while self._looks_challenged() and waited < self.challenge_wait:
            if waited == 0:
                log.warning("⚠️  Cloudflare challenge detected — solve it in the "
                            "Chrome window. Waiting up to %ss …", self.challenge_wait)
            time.sleep(3)
            waited += 3
        if self._looks_challenged():
            log.error("    still challenged after %ss — skipping this page.",
                      self.challenge_wait)
            return False
        return True

    # -- fetchers ------------------------------------------------------------
    def get_html(self, url: str) -> Optional[str]:
        try:
            log.info(f"  NAV {url}")
            self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            log.warning(f"    nav error: {e}")
            return None
        if self._looks_challenged() and not self._wait_out_challenge():
            return None
        try:
            content = self.page.content()
        except Exception as e:
            log.warning(f"    content error: {e}")
            return None
        self._maybe_dump(url, content)
        return content

    def get_gpx_text(self, track_id: int) -> Optional[str]:
        """Fetch a track's GPX with an in-page fetch() (same origin → cf_clearance
        + browser TLS apply). Returns the raw text or None."""
        url = f"{BASE_URL}/maps/viewtrack/gpx/{track_id}"
        js = """async (u) => {
            try {
                const r = await fetch(u, {credentials: 'include'});
                if (!r.ok) return null;
                return await r.text();
            } catch (e) { return null; }
        }"""
        try:
            return self.page.evaluate(js, url)
        except Exception as e:
            log.warning(f"    gpx fetch error (track {track_id}): {e}")
            return None


# ===========================================================================
#  Resolve + scrape one race  (uses a CDPFetcher)
# ===========================================================================
def find_race_page(fetcher: CDPFetcher, race: dict, year: int) -> Optional[dict]:
    """Find this race's LFR race-view page. Honours LFR_RACE_OVERRIDES, else
    searches the listing by name across the target calendars."""
    slug = race["slug"]
    if slug in LFR_RACE_OVERRIDES:
        rid = LFR_RACE_OVERRIDES[slug]
        return {"race_id": rid, "name": race["name"],
                "view_url": f"{BASE_URL}/maps/races/view/{year}/{rid}",
                "score": 1.0}

    candidates: List[dict] = []
    q = normalize_name(race["name"]).replace(" ", "+")
    for cal in CALENDAR_CODES:
        # LFR pages are 1-INDEXED: its SQL offset is (page-1)*30, so page=0 yields
        # `LIMIT -30,30` → a phpBB "General Error" page (0 candidates). Start at 1.
        for page in range(1, MAX_LISTING_PAGES + 1):
            url = (f"{BASE_URL}/maps/races?count=0&page={page}"
                   f"&calendar%5B0%5D={cal}&year%5B0%5D={year}&years=&name={q}")
            html = fetcher.get_html(url)
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


def scrape_race(fetcher: CDPFetcher, race: dict, year: int,
                dry_run: bool) -> List[dict]:
    """Resolve a race on LFR and fetch its stage GPX. Returns a list of file
    records: dry-run → {stage, track_id, filename}; live → also {content, url}."""
    page = find_race_page(fetcher, race, year)
    if not page:
        return []
    html = fetcher.get_html(page["view_url"])
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
                 "track_id": t,
                 "filename": stage_filename(i + 1, len(track_ids), is_one_day)}
                for i, t in enumerate(track_ids)]

    files = []
    for i, tid in enumerate(track_ids):
        fname = stage_filename(i + 1, len(track_ids), is_one_day)
        text = fetcher.get_gpx_text(tid)
        polite_sleep()
        if looks_like_gpx(text):
            log.info(f"    ✓ {fname} (track {tid}, {len(text)} bytes)")
            files.append({
                "stage": None if is_one_day else i + 1,
                "filename": fname,
                "content": text,
                "url": f"{BASE_URL}/maps/viewtrack/gpx/{tid}",
                "track_id": tid,
            })
        else:
            log.warning(f"    ✗ not GPX (track {tid})")
    return files


# ===========================================================================
#  Main — read targets from the store, fetch via CDP, write GPX to Turso
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(
        description="LFR GPX fallback (WT + ProSeries) - attended CDP-Chrome -> Turso.")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve races/tracks but store nothing")
    ap.add_argument("--only", metavar="SLUG", help="limit to one race slug")
    ap.add_argument("--list-targets", action="store_true",
                    help="print the GPX-less WT+ProSeries races and exit "
                         "(reads the store only; no browser, no LFR)")
    ap.add_argument("--cdp-url", default="http://localhost:9222",
                    help="CDP endpoint of the user-launched Chrome")
    ap.add_argument("--dump-html", action="store_true",
                    help="save each fetched LFR page to scrapers/fixture/lfr_dump/ "
                         "for debugging the parser/URL")
    ap.add_argument("--start-after", metavar="YYYY-MM-DD",
                    help="only harvest races starting on/after this date "
                         "(default: tomorrow). Ignored when --only is given.")
    args = ap.parse_args()
    dump_dir = (Path(__file__).resolve().parent / "fixture" / "lfr_dump"
                if args.dump_html else None)
    if args.start_after:
        cutoff = datetime.strptime(args.start_after, "%Y-%m-%d").date()
    else:
        cutoff = (datetime.now() + timedelta(days=1)).date()

    client = db.open_db()
    log.info(f"GPX store: {'remote Turso' if db.is_remote() else 'local SQLite file'}")

    races = list(db.get_all_documents(client, db.KIND_RACE).values())
    if not races:
        log.error("No races in the store — run scrape_races.py first.")
        client.close()
        return

    default_year = datetime.now().year
    todo = targets(races, lambda s: db.has_gpx(client, s), args.only,
                   start_on_or_after=cutoff)
    scope = f"only '{args.only}'" if args.only else f"starting on/after {cutoff}"
    log.info(f"{len(todo)} WT+ProSeries race(s) missing GPX to try on LFR ({scope})")

    if args.list_targets:
        for r in todo:
            print(f"{r['slug']}\t{r.get('uci_tour')}\t{r['name']}")
        client.close()
        return

    if not todo:
        client.close()
        return

    fetcher = None
    filled = stored_files = 0
    try:
        fetcher = CDPFetcher(args.cdp_url, dump_dir=dump_dir)
        for race in todo:
            year = race.get("year") or default_year
            log.info(f"\n{'='*54}\n{race['name']}  [{race.get('uci_tour')}]\n{'='*54}")
            files = scrape_race(fetcher, race, year, args.dry_run)

            if args.dry_run:
                for f in files:
                    log.info(f"    stage {f['stage']}: track {f['track_id']} → {f['filename']}")
                continue

            n = 0
            for f in files:
                try:
                    db.put_gpx(client, race["slug"], f["filename"], f["content"],
                               stage=f["stage"], source=SOURCE_TAG, url=f["url"])
                    n += 1
                except Exception as e:
                    log.error(f"    ✗ Turso write failed for {f['filename']}: {e}")
            if n:
                # Verify the write actually landed (catches a silent CA-bundle/proxy
                # failure on the Python→Turso path).
                if db.has_gpx(client, race["slug"]):
                    filled += 1
                    stored_files += n
                    log.info(f"  → stored {n} GPX file(s) from LFR")
                else:
                    log.error("  ⚠ wrote %d file(s) but has_gpx is still False — "
                              "check the Turso/CA-bundle config (SSL_CERT_FILE).", n)
    finally:
        if fetcher:
            fetcher.close()

    total_stored = len(db.gpx_slugs(client))
    client.close()

    if not args.dry_run:
        print("\n" + "=" * 64)
        print("  LFR GPX FALLBACK SUMMARY")
        print(f"  Races filled from LFR:        {filled} ({stored_files} files)")
        print(f"  Total races with GPX in store:{total_stored}")
        print("=" * 64)


if __name__ == "__main__":
    main()
