#!/usr/bin/env python3
"""
Scrape the LAST 3 SEASONS of per-stage results to feed the results-based rider
rating model (see score_history.py + the project_results_scoring_model memory).

For each race × year it stores ONE doc in Turso `race_data` kind="results"
(slug = "{pcs_slug}-{year}") holding, per stage:
    stage, date, stage_url, stage_type (mountain/hilly/sprint/TT/prologue),
    won_how, startlist_quality (PCS field-strength score), profile_score (PCS
    profile difficulty), finishers, and a per-rider results[] list with
    rider_url, rider_name, rank, status, time, pcs_points, uci_points,
    breakaway_kms.
One-day classics are stored as a single-"stage" doc (results live at the
race's /result page — the nude race URL has no results table).

INCREMENTAL: any edition already stored is skipped (history never changes), so
after the first full backfill a run only scrapes NEW races. A current-year race
is stored only once it has FINISHED (+grace) — never as a partial doc — so it's
captured complete on a later run. Use --force to re-scrape regardless.

Network: needs procyclingstats.com → GitHub Actions or a non-proxied machine.
On this dev machine use --insecure to relax the proxy's strict-TLS rejection
(spike-style; never used in CI). See project_local_scraping_tls memory.

Usage:
  python scrapers/scrape_history.py                 # CALENDAR races, 3 years
  python scrapers/scrape_history.py --insecure --limit 1 --years 2024
  python scrapers/scrape_history.py --discover      # full WT+ProSeries per year
"""

import argparse
import logging
import re
import ssl
import sys
import time
from datetime import datetime, date, timedelta

from procyclingstats import Race, Stage

import db
from scrape_races import (CALENDAR, discover_calendar,
                          scrape_one_day_profile_icon)

try:
    sys.stdout.reconfigure(encoding="utf-8")  # rider names carry accents
except Exception:
    pass

DELAY = 2  # seconds — be polite to PCS
# A current-year race is only stored once it ended this many days ago, so we
# never freeze a partially-run race (final results need a day or two to settle).
FREEZE_GRACE_DAYS = 2
RESULT_FIELDS = ("rider_url", "rider_name", "rank", "status", "time",
                 "pcs_points", "uci_points", "breakaway_kms")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# stage_type taxonomy for the rating model (5 categories). TT/prologue are
# decided by NAME first (the PCS icon can't tell a TT from a flat stage, and
# stage_type() even returns 'RR' for some prologues — see reference memory).
ICON_TO_TYPE = {"p0": "sprint", "p1": "sprint", "p2": "sprint",
                "p3": "hilly", "p4": "mountain", "p5": "mountain"}
ITT_NAME_RE = re.compile(r"\(ITT\)|\bTime[\s-]?trial\b", re.IGNORECASE)
FALLBACK_TYPE = "sprint"


def classify(profile_icon, name):
    """(stage_type, source) for the 5-way model taxonomy."""
    n = (name or "").lower()
    if "prologue" in n:
        return "prologue", "stage_name"
    if ITT_NAME_RE.search(name or ""):
        return "TT", "stage_name_itt"
    t = ICON_TO_TYPE.get(profile_icon)
    if t:
        return t, "profile_icon"
    return FALLBACK_TYPE, "fallback_default"


def _relax_tls():
    """--insecure: drop VERIFY_X509_STRICT + load Windows trust store on both
    cached sessions (procyclingstats lib AND scrape_races' cloudscraper) so the
    proxied dev machine can reach PCS. SPIKE/LOCAL ONLY — never in CI."""
    import urllib3
    from procyclingstats.scraper import Scraper
    from scrape_races import _get_scraper
    urllib3.disable_warnings()
    sessions = [Scraper._get_session(), _get_scraper()]
    for sess in sessions:
        # NB: do NOT set verify=False — cloudscraper's custom ssl_context then
        # hits a check_hostname/CERT_NONE conflict. Keep verification ON, just
        # drop the strict flag and trust the Windows store (where the proxy CA is).
        for adapter in sess.adapters.values():
            ctx = getattr(adapter, "ssl_context", None)
            if ctx is not None:
                ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
                try:
                    ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)
                except Exception:
                    pass


def _is_finished(enddate_str, today):
    """True if the edition ended more than FREEZE_GRACE_DAYS ago (safe to store
    as complete). Unparseable/empty enddate → treated as not finished."""
    if not enddate_str or len(enddate_str) < 10:
        return False
    try:
        end = datetime.strptime(enddate_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return end < today - timedelta(days=FREEZE_GRACE_DAYS)


def _iso_date(mmdd_or_iso, year):
    """PCS stage date 'MM-DD' (or 'YYYY-MM-DD') → a date, or None."""
    if not mmdd_or_iso:
        return None
    s = mmdd_or_iso
    try:
        if len(s) >= 10:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        return datetime.strptime(f"{year}-{s}", "%Y-%m-%d").date()
    except ValueError:
        return None


def fetch_results(stage_url):
    """Return (results_list, finishers, won_how, startlist_quality,
    profile_score) for a stage/one-day result URL, or (None, 0, None, None,
    None) if the table isn't up yet. quality + profile_score come off the same
    Stage object → no extra request. startlist_quality = PCS start-of-race
    field strength; profile_score = PCS stage-profile difficulty."""
    try:
        st = Stage(stage_url)
        rows = st.results(*RESULT_FIELDS)
    except Exception as e:  # noqa: BLE001 — results not published / parse error
        log.warning(f"    results unavailable: {stage_url} ({e})")
        return None, 0, None, None, None
    try:
        won = st.won_how()
    except Exception:
        won = None
    try:
        # (start-of-race score, after-current-stage score) — keep the start one.
        quality = st.race_startlist_quality_score()[0]
    except Exception:
        quality = None
    try:
        profile = st.profile_score()
    except Exception:
        profile = None
    finishers = sum(1 for r in rows if r.get("status") == "DF")
    return rows, finishers, won, quality, profile


def stage_number(stage_url):
    """'…/stage-5' → 5, '…/prologue' → 0, else None."""
    tail = stage_url.rstrip("/").split("/")[-1]
    m = re.match(r"stage-(\d+)", tail)
    if m:
        return int(m.group(1))
    return 0 if "prologue" in tail else None


def scrape_stage_race(race, year, today):
    """Build the stages[] payload for a stage race (only stages already run).
    Takes the already-fetched Race object so .stages() reuses its cached HTML
    (no second page request)."""
    stages_meta = race.stages("date", "profile_icon", "stage_name", "stage_url")
    out = []
    for s in stages_meta:
        d = _iso_date(s.get("date"), year)
        if d is None or d > today:
            continue  # not raced yet
        url = s.get("stage_url")
        stype, source = classify(s.get("profile_icon"), s.get("stage_name"))
        rows, finishers, won, quality, profile = fetch_results(url)
        time.sleep(DELAY)
        if not rows:
            continue
        out.append({
            "stage": stage_number(url),
            "date": d.isoformat(),
            "stage_url": url,
            "stage_type": stype,
            "stage_type_source": source,
            "won_how": won,
            "startlist_quality": quality,
            "profile_score": profile,
            "finishers": finishers,
            "results": rows,
        })
    return out


def scrape_one_day(race_url, pcs_slug, year, name):
    """Build the single-"stage" payload for a one-day classic."""
    icon = scrape_one_day_profile_icon(pcs_slug, year)
    time.sleep(DELAY)
    stype, source = classify(icon, name)
    rows, finishers, won, quality, profile = fetch_results(race_url + "/result")
    time.sleep(DELAY)
    if not rows:
        return []
    return [{
        "stage": None,
        "date": None,
        "stage_url": race_url + "/result",
        "stage_type": stype,
        "stage_type_source": source,
        "won_how": won,
        "startlist_quality": quality,
        "profile_score": profile,
        "finishers": finishers,
        "results": rows,
    }]


def process_race(client, pcs_slug, year, today, force):
    """Scrape one race-edition and upsert its results doc. Returns a short
    status string for the summary."""
    slug = f"{pcs_slug}-{year}"
    # Incremental: anything already stored is skipped (history never changes;
    # current-year docs are only ever written once the race is finished). Pass
    # --force to re-scrape regardless (e.g. a corrected result).
    if db.has_document(client, db.KIND_RESULTS, slug) and not force:
        return "frozen"

    race_url = f"race/{pcs_slug}/{year}"
    try:
        race = Race(race_url)
        is_one_day = race.is_one_day_race()
        name = race.name()
        enddate = race.enddate()
    except Exception as e:  # noqa: BLE001 — race page missing for that year
        log.warning(f"  {slug}: race page unavailable ({e})")
        return "missing"
    time.sleep(DELAY)

    # Current year: only store a race once it's FINISHED, so we never freeze a
    # partial doc. Ongoing/upcoming races are captured a later week, complete.
    if year == today.year and not _is_finished(enddate, today):
        return "ongoing"

    if is_one_day:
        stages = scrape_one_day(race_url, pcs_slug, year, name)
    else:
        stages = scrape_stage_race(race, year, today)
    if not stages:
        return "no-results"

    doc = {
        "race_slug": slug,
        "race": name,
        "pcs_slug": pcs_slug,
        "year": year,
        "is_one_day_race": is_one_day,
        "scraped_at": datetime.now().isoformat(),
        "stages": stages,
    }
    db.put_document(client, db.KIND_RESULTS, slug, doc)
    n_res = sum(len(s["results"]) for s in stages)
    log.info(f"  {slug}: {len(stages)} stage(s), {n_res} result rows stored")
    return "written"


def race_slugs(discover, year):
    """pcs_slugs to scrape for a year — CALENDAR by default, full WT+ProSeries
    via --discover (falls back to CALENDAR if discovery fails). Women's / other
    excluded races (db.EXCLUDE_RESULT_PCS_SLUGS) are filtered out."""
    base = [v[0] for v in CALENDAR.values()]  # CALENDAR value[0] == pcs_slug
    if discover:
        rows = discover_calendar(year)
        if rows:
            base += [r["pcs_slug"] for r in rows]
        else:
            log.warning(f"  discovery failed for {year} — using CALENDAR only")
    # de-dupe (keep order, CALENDAR first) and drop excluded slugs.
    seen, out = set(), []
    for s in base:
        if s not in seen and s not in db.EXCLUDE_RESULT_PCS_SLUGS:
            seen.add(s)
            out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser(description="Scrape 3 seasons of PCS results.")
    ap.add_argument("--years", type=int, nargs="*",
                    help="explicit years (default: current + 2 prior)")
    ap.add_argument("--discover", action="store_true",
                    help="full WT+ProSeries calendar per year (default: CALENDAR)")
    ap.add_argument("--limit", type=int, default=None,
                    help="max races per year (for testing)")
    ap.add_argument("--force", action="store_true",
                    help="re-scrape editions even if already stored (e.g. a fix)")
    ap.add_argument("--insecure", action="store_true",
                    help="LOCAL ONLY: relax strict-TLS for the proxied dev machine")
    args = ap.parse_args()

    if args.insecure:
        _relax_tls()

    today = date.today()
    years = args.years or [today.year, today.year - 1, today.year - 2]

    client = db.open_db()
    log.info(f"History store: {'remote Turso' if db.is_remote() else 'local SQLite file'}")
    log.info(f"Years: {years}  ·  source: {'discover' if args.discover else 'CALENDAR'}")

    tally = {}
    for year in years:
        slugs = race_slugs(args.discover, year)
        if args.limit:
            slugs = slugs[:args.limit]
        log.info(f"=== {year}: {len(slugs)} races ===")
        for pcs_slug in slugs:
            status = process_race(client, pcs_slug, year, today, args.force)
            tally[status] = tally.get(status, 0) + 1

    stored = len(db.list_slugs(client, db.KIND_RESULTS))
    client.close()

    print("\n" + "=" * 56)
    print("  HISTORY SCRAPE SUMMARY")
    for k in ("written", "frozen", "ongoing", "no-results", "missing"):
        if k in tally:
            print(f"  {k:12} {tally[k]}")
    print(f"  result docs in store: {stored}")
    print("=" * 56)


if __name__ == "__main__":
    main()
