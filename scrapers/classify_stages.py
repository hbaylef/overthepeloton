#!/usr/bin/env python3
"""
R2 Phase 2 — apply derived stage_type classification to data/races.json.

Pure logic, no scraping: reads the existing races.json, annotates every stage
(and every one-day race) with `stage_type` + `stage_type_source`, then writes
it back.

This is the SAME `annotate_stage_types` step that scrape_races.py runs at the
end of a full scrape — exposed standalone so the classification can be
(re)applied/backfilled without re-scraping PCS.

Usage:
  python scrapers/classify_stages.py
"""

import json
import logging
from collections import Counter
from pathlib import Path

from scrape_races import annotate_stage_types

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RACES_FILE = DATA_DIR / "races.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def main():
    with open(RACES_FILE, encoding="utf-8") as f:
        data = json.load(f)

    races = data.get("races", [])
    annotate_stage_types(races)

    with open(RACES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Summary — counts of derived types and where each came from.
    types = Counter()
    sources = Counter()
    for race in races:
        if race.get("is_one_day_race"):
            types[race.get("stage_type")] += 1
            sources[race.get("stage_type_source")] += 1
        else:
            for stage in race.get("stages", []):
                types[stage.get("stage_type")] += 1
                sources[stage.get("stage_type_source")] += 1

    total = sum(types.values())
    log.info(f"Annotated {total} stages/races in {RACES_FILE}")

    print("\n" + "=" * 48)
    print("  stage_type distribution")
    print("=" * 48)
    for t, n in types.most_common():
        print(f"    {t:16} {n}")
    print("\n  stage_type_source distribution")
    print("-" * 48)
    for s, n in sources.most_common():
        print(f"    {str(s):18} {n}")
    print("=" * 48)


if __name__ == "__main__":
    main()
