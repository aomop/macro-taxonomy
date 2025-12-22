#!/usr/bin/env python3
"""
Add new TSNs (and their downstream genera) to a tsn_list CSV.

Usage examples:
    # Typical usage: use latest tsn_list_*.csv in data/add_tsns_data
    python scripts/add_tsns.py --tsn 123456

    # Explicitly specify a different tsn_list file as the input template
    python scripts/add_tsns.py --tsn 123456 --csv data/tsn_list_mayflies.csv

Behavior:
    - Determines an *input* tsn_list CSV (columns: TSN, genus):
        * --csv path if provided, otherwise the most recent tsn_list_*.csv in
          data/add_tsns_data.
    - Uses ITIS to get all downstream taxa from the given TSN.
    - Filters to rankName == "Genus" (plus the root TSN if it is a genus).
    - Loads existing rows from the input CSV.
    - Adds any new (TSN, genus) pairs that aren't already present.
    - Writes results to a **new** CSV in data/add_tsns_data named:
        tsn_list_YYYYMMDD.csv
      If that exists, uses:
        tsn_list_YYYYMMDD_1.csv, _2, etc.
    - Does NOT overwrite the input file.
"""

import argparse
import csv
import sys
from pathlib import Path
from datetime import date
import itertools
import json
from collections import Counter, deque

from tqdm.auto import tqdm
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "add_tsns_data"
OUTPUT_DIR = DATA_DIR / "add_tsns_data"
CACHE_DIR = DATA_DIR / "add_tsns_cache"

ITIS_BASE = "https://www.itis.gov/ITISWebService/jsonservice"

# Simple JSON cache of downstream hierarchies
CACHE_PATH = CACHE_DIR / "itis_hierarchy_cache.json"


# ----------------------------------------------------------------------
# Cache helpers
# ----------------------------------------------------------------------
def load_cache():
    """Load cached hierarchyList per TSN from disk, if available."""
    if not CACHE_PATH.exists():
        return {}
    try:
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Ensure keys are strings, values are lists
        if not isinstance(data, dict):
            return {}
        return {str(k): v for k, v in data.items()}
    except Exception as e:
        print(f"[WARN] Could not read cache {CACHE_PATH}: {e}", file=sys.stderr)
        return {}


def save_cache(cache):
    """Save cached hierarchyList per TSN to disk."""
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception as e:
        print(f"[WARN] Could not write cache {CACHE_PATH}: {e}", file=sys.stderr)


# ----------------------------------------------------------------------
# ITIS helpers
# ----------------------------------------------------------------------
def get_genus_descendants(root_tsn: str):
    """
    Given a root TSN, walk *down* the ITIS hierarchy using
    getHierarchyDownFromTSN recursively and return a list of
    (tsn, genus_name) for:

      - all downstream taxa with rankName == 'Genus' (case-insensitive)
      - plus the root itself if its taxonRank is 'Genus'

    Uses a breadth-first search and stops recursing once 'Genus' is reached.
    """

    genera = {}

    # Load / init cache and session
    cache = load_cache()
    session = requests.Session()

    # ------------------------------------------------------------------
    # 1) Check the root TSN itself (in case it's already a Genus)
    # ------------------------------------------------------------------
    print(f"[INFO] Fetching full record for root TSN {root_tsn}...")
    try:
        r = session.get(
            f"{ITIS_BASE}/getFullRecordFromTSN",
            params={"tsn": root_tsn},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()

        core = data.get("scientificName", {})
        taxon_rank = (core.get("taxonRank") or "").strip().lower()
        if taxon_rank == "genus":
            name = core.get("combinedName")
            if name:
                genera[str(root_tsn)] = name
                print(f"[INFO] Root TSN {root_tsn} is a Genus: {name}")
    except Exception as e:
        print(f"[WARN] Could not inspect root TSN {root_tsn}: {e}", file=sys.stderr)

    # ------------------------------------------------------------------
    # 2) BFS down the hierarchy via getHierarchyDownFromTSN
    #    (which returns *immediate* children only)
    #    We cache each TSN's hierarchyList so we never re-fetch it.
    # ------------------------------------------------------------------
    visited = set()
    queue = deque([str(root_tsn)])
    rank_counts = Counter()

    print(f"[INFO] Walking down ITIS hierarchy from TSN {root_tsn}...")
    pbar = tqdm(total=0, desc="Traversing hierarchy", unit="taxa")

    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)

        # Try cache first
        if current in cache:
            hierarchy = cache[current]
        else:
            try:
                r = session.get(
                    f"{ITIS_BASE}/getHierarchyDownFromTSN",
                    params={"tsn": current},
                    timeout=60,
                )
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                print(f"[WARN] ITIS request failed for TSN {current}: {e}", file=sys.stderr)
                continue

            hierarchy = data.get("hierarchyList", [])
            # Only cache "normal" list responses
            if isinstance(hierarchy, list):
                cache[current] = hierarchy

        if not isinstance(hierarchy, list):
            # Some responses can be empty or oddly shaped; just skip
            continue

        for rec in hierarchy:
            # --- NEW GUARD: skip weird / null entries ---------------------
            if not isinstance(rec, dict):
                continue

            raw_rank = rec.get("rankName")
            rank = (raw_rank or "").strip()
            rank_norm = rank.lower()
            rank_counts[rank] += 1

            child_tsn = rec.get("tsn")
            name = rec.get("taxonName")

            if not child_tsn or not name:
                continue

            child_tsn = str(child_tsn)
            pbar.update(1)

            if rank_norm == "genus":
                # We found a genus; collect and do NOT descend further
                genera[child_tsn] = name
            else:
                # Not a genus yet → keep walking down this branch
                if child_tsn not in visited:
                    queue.append(child_tsn)

    pbar.close()

    print("[INFO] Rank breakdown encountered while traversing:")
    for rank, count in rank_counts.most_common():
        print(f"    {rank!r}: {count}")

    # Save updated cache once per run
    save_cache(cache)

    # ------------------------------------------------------------------
    # 3) Return sorted unique genera
    # ------------------------------------------------------------------
    def _safe_int(x):
        try:
            return int(x)
        except Exception:
            return float("inf")

    result = sorted(genera.items(), key=lambda x: _safe_int(x[0]))
    print(f"[INFO] Total unique genera found (including root if genus): {len(result)}")
    return result


# ----------------------------------------------------------------------
# CSV helpers
# ----------------------------------------------------------------------
def find_latest_tsn_list(base_dir: Path) -> Path:
    """
    Find the most recently modified tsn_list_*.csv in base_dir/data/add_tsns_input.
    Raises RuntimeError if none are found.
    """
    candidates = list(base_dir.glob("data/add_tsns_input/tsn_list_*.csv"))
    if not candidates:
        raise RuntimeError(
            f"No tsn_list_*.csv files found in {base_dir.resolve()}/data/add_tsns_input. "
            "Use --csv to specify a file explicitly."
        )

    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return latest


def load_existing_tsn_list(csv_path: Path):
    """
    Read existing CSV with columns TSN, genus.
    Returns dict: {TSN: genus}.
    """
    existing = {}

    if not csv_path.exists():
        print(f"[WARN] {csv_path} does not exist; starting from an empty list.", file=sys.stderr)
        return existing

    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return existing

        # Tolerant of capitalization: TSN/tsn, genus/Genus, etc.
        field_map = {name.lower(): name for name in reader.fieldnames}
        tsn_col = field_map.get("tsn")
        genus_col = field_map.get("genus")

        if tsn_col is None or genus_col is None:
            raise RuntimeError(
                f"{csv_path} must have columns 'TSN' and 'genus' (case-insensitive). "
                f"Found columns: {reader.fieldnames}"
            )

        rows = list(reader)
        print(f"[INFO] Loading existing TSNs from {csv_path} ({len(rows)} rows)...")
        for row in tqdm(rows, desc="Reading CSV", unit="rows"):
            tsn = row.get(tsn_col)
            genus = row.get(genus_col)
            if tsn:
                existing[str(tsn)] = genus

    return existing


def get_dated_output_path() -> Path:
    """
    Return a new output path in data/add_tsns_output that:
      - appends _YYYYMMDD to 'tsn_list'
      - if that exists, appends _YYYYMMDD_1, _YYYYMMDD_2, etc.
    """
    today_str = date.today().strftime("%Y%m%d")
    stem = "tsn_list"
    suffix = ".csv"
    parent = OUTPUT_DIR

    # First candidate: tsn_list_YYYYMMDD.csv
    base_name = f"{stem}_{today_str}{suffix}"
    candidate = parent / base_name

    if not candidate.exists():
        return candidate

    # If exists, try tsn_list_YYYYMMDD_1.csv, _2, ...
    for i in itertools.count(1):
        candidate = parent / f"{stem}_{today_str}_{i}{suffix}"
        if not candidate.exists():
            return candidate


def write_tsn_list(csv_path: Path, tsn_to_genus: dict):
    """
    Write the TSN/genus mapping to CSV, sorted by integer TSN.
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    def _safe_int(x):
        try:
            return int(x)
        except Exception:
            return float("inf")

    rows = [
        {"TSN": tsn, "genus": genus}
        for tsn, genus in sorted(tsn_to_genus.items(), key=lambda x: _safe_int(x[0]))
    ]

    print(f"[INFO] Writing {len(rows)} rows to {csv_path}...")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["TSN", "genus"])
        writer.writeheader()
        writer.writerows(rows)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Add all downstream genera (TSNs + names) from a given TSN "
            "to a tsn_list CSV (columns: TSN, genus). "
            "Does not overwrite the input file; writes to a date-stamped output."
        )
    )

    parser.add_argument(
        "--tsn",
        required=True,
        help="Root TSN to query downstream from (e.g., an Order, Family, etc.).",
    )

    parser.add_argument(
        "--csv",
        default=None,
        help=(
            "Path to a tsn_list CSV (two columns: TSN, genus) to use as input. "
            "If omitted, uses the most recent tsn_list_*.csv in data/add_tsns_input."
        ),
    )

    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    root_tsn = str(args.tsn)

    # Determine which CSV to use as the INPUT template
    if args.csv is not None:
        input_csv = Path(args.csv)
    else:
        input_csv = find_latest_tsn_list(Path("."))

    print(f"[INFO] Root TSN: {root_tsn}")
    print(f"[INFO] Input CSV (read-only): {input_csv}")

    # Get all genera downstream
    new_genera = get_genus_descendants(root_tsn)
    print(f"[INFO] Found {len(new_genera)} genera downstream from TSN {root_tsn}")

    # Load existing from input CSV
    existing = load_existing_tsn_list(input_csv)
    print(f"[INFO] Existing unique TSNs: {len(existing)}")

    # Add any that aren't present
    added = 0
    print("[INFO] Merging genera into TSN list...")
    for tsn, genus in tqdm(new_genera, desc="Merging genera", unit="genera"):
        if tsn not in existing:
            existing[tsn] = genus
            added += 1

    print(f"[INFO] Added {added} new TSNs")

    # Determine output CSV path (date-stamped, non-overwriting)
    output_csv = get_dated_output_path()
    print(f"[INFO] Output CSV (new file): {output_csv}")

    # Write to new output file
    write_tsn_list(output_csv, existing)
    print(f"[INFO] Finished. Output written to {output_csv.resolve()}")


if __name__ == "__main__":
    main()
