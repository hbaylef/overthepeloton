#!/usr/bin/env python3
"""
Record per-rider ABANDONS and STAGE-PODIUM MEDALS for each ongoing/recent stage
race, and embed them into the startlist files so the frontend can strike out
abandoners and show medals next to stage winners / podium finishers.

PCS exposes per-rider ``rank`` + ``status`` on every stage's result table:
  status: DF (did finish), DNF, DNS, OTL, DSQ.
We scan each stage that has already happened. For abandons, a rider's LAST
appearance decides their fate — if that status isn't DF, they abandoned there.
For medals, we record each podium across all stages (rank 1/2/3) with the stage
it was won on, so the UI can show the ranking + stage (e.g. 🥇 #1 S5).

Per-rider fields added inside data/startlists/{slug}.json:
    "status":          "DNF" | "DNS" | "OTL" | "DSQ"   (abandoners only)
    "abandoned_stage": "S5" | "P" | ...   (short label of the stage they left on)
    "medals":          [{"rank": 1, "stage": "S5"}, ...]   (stage podiums; medallists only)
Non-abandoners / non-medallists have the respective fields removed (kept clean).

Scope: only STAGE races that have started and aren't yet frozen (ended more
than GRACE_DAYS ago) — mirrors scrape_races.py's freeze so we don't re-scrape
races that are over. One-day races aren't processed here.

Ordering: this MUST run AFTER scrape_riders.py and geocode_birthplaces.py,
which both rewrite the startlist files (and would otherwise clobber these
fields). It rewrites only the status/abandoned_stage/medals keys.

Network: needs procyclingstats.com — Actions / non-proxied machine only.

Usage:
  python scrapers/scrape_results.py
"""

import json
import re
import time
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from procyclingstats import Stage

import db  # local module: Turso/SQLite store (build-order step 2)

DELAY_BETWEEN_REQUESTS = 2  # seconds — be polite to PCS
GRACE_DAYS = 2              # keep scanning a race this many days past its end

# A status other than DF means the rider is no longer in the race.
ABANDON_STATUSES = {"DNF", "DNS", "OTL", "DSQ"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def stage_label(stage_url: str) -> str:
    """Short tag for a stage from its URL: 'stage-5' → 'S5', 'prologue' → 'P'."""
    tail = stage_url.rstrip("/").split("/")[-1]
    m = re.match(r"stage-(\d+)", tail)
    if m:
        return "S" + m.group(1)
    if "prologue" in tail:
        return "P"
    return tail


def parse_date(s: Optional[str], year: int) -> Optional[date]:
    """Parse a stage 'MM-DD' (or full 'YYYY-MM-DD') into a date."""
    if not s:
        return None
    try:
        if len(s) >= 10:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        return datetime.strptime(f"{year}-{s}", "%Y-%m-%d").date()
    except ValueError:
        return None


def _to_int(v) -> Optional[int]:
    """PCS ranks come through as strings ('1') or ints; non-finishers as ''/None."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def fetch_stage_results(stage_url: str) -> Optional[dict]:
    """Return {rider_url: {"rank": int|None, "status": str}} for a stage, or
    None if results aren't up yet."""
    try:
        rows = Stage(stage_url).results("rider_url", "rank", "status")
    except Exception as e:
        log.warning(f"  results unavailable for {stage_url}: {e}")
        return None
    out = {}
    for row in rows:
        u = row.get("rider_url")
        if u:
            out[u] = {"rank": _to_int(row.get("rank")), "status": row.get("status")}
    return out or None


def compute_abandons(scanned_stages: list) -> dict:
    """Pure: given an ordered list of (stage_label, {rider_url: {rank,status}})
    for the stages that have happened, return {rider_url: (status, label)} for
    every rider who abandoned. A rider's LAST appearance decides their fate —
    if that status isn't DF, they left the race on that stage."""
    last_seen = {}                              # url -> (label, status)
    for label, rows in scanned_stages:
        for url, info in rows.items():
            last_seen[url] = (label, info.get("status"))
    return {url: (st, label)
            for url, (label, st) in last_seen.items()
            if st in ABANDON_STATUSES}


# A stage podium (rank 1/2/3) earns a medal: 🥇 1st, 🥈 2nd, 🥉 3rd.
PODIUM_RANKS = (1, 2, 3)


def compute_medals(scanned_stages: list) -> dict:
    """Pure: collect each rider's stage podiums across all scanned stages. Returns
    {rider_url: [{"rank": 1, "stage": "S5"}, ...]} for medallists only — one entry
    per podium finish (two stage wins = two rank-1 entries), best rank first
    (stage order preserved within a rank), so the UI can show e.g. 🥇 #1 S5."""
    medals = {}
    for label, rows in scanned_stages:
        for url, info in rows.items():
            rank = info.get("rank")
            if rank in PODIUM_RANKS:
                medals.setdefault(url, []).append({"rank": rank, "stage": label})
    for podiums in medals.values():
        podiums.sort(key=lambda p: p["rank"])   # stable: keeps stage order within a rank
    return medals


def apply_results(riders: list, abandons: dict, medals: dict) -> tuple:
    """Pure: write status/abandoned_stage + medals onto matching riders, clearing
    those fields on everyone else so a re-run never leaves stale data. Mutates
    the list in place; returns (n_abandoned, n_medallists)."""
    n_ab = n_med = 0
    for r in riders:
        url = r.get("rider_url")
        hit = abandons.get(url)
        if hit:
            r["status"], r["abandoned_stage"] = hit
            n_ab += 1
        else:
            r.pop("status", None)
            r.pop("abandoned_stage", None)
        med = medals.get(url)
        if med:
            r["medals"] = med
            n_med += 1
        else:
            r.pop("medals", None)
    return n_ab, n_med


def scan_stages(race: dict, today, fetch=fetch_stage_results) -> list:
    """Fetch results for every stage of `race` that has already happened.
    Returns the ordered list of (label, {url: {rank,status}}) the compute_*
    functions consume."""
    year = race.get("year") or today.year
    scanned = []
    for stage in race.get("stages", []):
        d = parse_date(stage.get("date"), year)
        url = stage.get("stage_url")
        if not url or d is None or d > today:
            continue
        rows = fetch(url)
        time.sleep(DELAY_BETWEEN_REQUESTS)
        if rows:
            scanned.append((stage_label(url), rows))
    return scanned


def process_race(client, race: dict, today, fetch=fetch_stage_results) -> Optional[tuple]:
    """Scan a stage race's completed stages and write abandon status + stage
    medals into its startlist (in the store). Returns (n_abandoned,
    n_medallists), or None if nothing was done."""
    slug = race.get("slug")
    data = db.get_document(client, db.KIND_STARTLIST, slug)
    if not data:
        return None

    scanned = scan_stages(race, today, fetch)
    if not scanned:
        return None

    n_ab, n_med = apply_results(data.get("riders", []),
                                compute_abandons(scanned), compute_medals(scanned))
    db.put_document(client, db.KIND_STARTLIST, slug, data)
    log.info(f"  {slug}: {len(scanned)} stage(s), "
             f"{n_ab} abandoned, {n_med} medallist(s)")
    return n_ab, n_med


def main():
    client = db.open_db()
    log.info(f"Results store: {'remote Turso' if db.is_remote() else 'local SQLite file'}")

    races = list(db.get_all_documents(client, db.KIND_RACE).values())
    if not races:
        log.error("No races in the store — run scrape_races.py first.")
        client.close()
        return

    today = datetime.now().date()   # Actions runs in UTC; date granularity is enough
    total_abandons = total_medals = 0
    processed = 0

    for race in races:
        if race.get("is_one_day_race"):
            continue
        start_d = parse_date(race.get("startdate"), today.year)
        end_d = parse_date(race.get("enddate"), today.year)
        if start_d and start_d > today:
            continue                                   # not started yet
        if end_d and end_d < today - timedelta(days=GRACE_DAYS):
            continue                                   # over & frozen
        log.info(f"Scanning {race.get('name')} for abandons + stage medals…")
        res = process_race(client, race, today)
        if res is not None:
            total_abandons += res[0]
            total_medals += res[1]
            processed += 1

    client.close()

    print("\n" + "=" * 64)
    print(f"  RESULTS SCRAPE SUMMARY")
    print(f"  Stage races scanned: {processed}")
    print(f"  Riders abandoned:    {total_abandons}")
    print(f"  Stage medallists:    {total_medals}")
    print("=" * 64)


if __name__ == "__main__":
    main()
