#!/usr/bin/env python
"""
Inspect ITIS jurisdiction/geographic strings from the local cache.

This script scans JSON files in data/itis_cache, extracts:
  - jurisdictionValue
  - geographicValue

and produces a CSV with unique values and counts, so you can
see what ITIS actually uses and refine REGION_POSITIVE_PATTERNS.

Usage:
    python inspect_region_terms.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from tqdm.auto import tqdm

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "itis_cache"
OUTPUT_PATH = DATA_DIR / "region_term_summary.csv"


def extract_terms_from_record(data: Dict[str, Any]) -> List[str]:
    """
    Given one cached ITIS record (the dict we stored per TSN), extract all
    jurisdictionValue and geographicValue strings.
    """
    terms: List[str] = []

    # Jurisdiction strings
    juris = data.get("jurisdiction") or {}
    origin_list = juris.get("jurisdictionalOriginList") or []
    for rec in origin_list or []:
        val = rec.get("jurisdictionValue")
        if val:
            terms.append(str(val))

    # Geographic division strings
    geo = data.get("geo_divisions") or {}
    div_list = geo.get("geographicDivisionList") or []
    for rec in div_list or []:
        val = rec.get("geographicValue")
        if val:
            terms.append(str(val))

    return terms


def main() -> None:
    if not CACHE_DIR.exists():
        raise SystemExit(f"Cache directory not found: {CACHE_DIR}")

    cache_files = sorted(CACHE_DIR.glob("*.json"))
    if not cache_files:
        raise SystemExit(
            f"No JSON cache files found in {CACHE_DIR}. "
            "Run flag_regions.py first to populate the cache."
        )

    print(f"[inspect_region_terms] Found {len(cache_files)} cached TSN files.")
    # You can cap this if you want a quicker sample; set to None for all.
    max_files = None  # e.g. 5000 for a faster run
    if max_files is not None:
        cache_files = cache_files[:max_files]
        print(f"[inspect_region_terms] Limiting to first {len(cache_files)} cache files.")

    counter = Counter()

    for path in tqdm(cache_files, desc="Scanning cache", unit="file"):
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
        except Exception:
            # Skip malformed files
            continue

        terms = extract_terms_from_record(data)
        counter.update(terms)

    if not counter:
        print("[inspect_region_terms] No jurisdiction/geographic terms found.")
        return

    # Build a DataFrame of unique terms and counts
    df = pd.DataFrame(
        [{"term": term, "count": count} for term, count in counter.most_common()]
    )

    df.to_csv(OUTPUT_PATH, index=False)
    print(f"[inspect_region_terms] Wrote summary to: {OUTPUT_PATH}")
    print("[inspect_region_terms] Top 20 terms:")
    print(df.head(20))


if __name__ == "__main__":
    main()
