#!/usr/bin/env python
"""
Add English common names to the taxonomy table using ITIS common name data.

For performance and data sparsity reasons:
  * We only query ITIS for leaf taxa (taxa with no children, typically species).
  * We then propagate common names upward through the parentTsn hierarchy:
      - Leaf: as retrieved from ITIS
      - Internal node: collect all unique common names from children
      - Any unresolved nodes get empty common names

Run this AFTER build_taxonomy.py, which should produce a built_taxonomy_*.csv
with columns including at least 'tsn' and 'parentTsn'.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import aiohttp
import pandas as pd
from tqdm.auto import tqdm  # progress bar


# ---------------------------------------------------------------------------
# Paths and globals
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "build_output"
OUTPUT_DIR = DATA_DIR / "common_names_output"
CACHE_DIR = DATA_DIR / "common_names_cache"

CACHE_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://www.itis.gov/ITISWebService/jsonservice"


def get_latest_taxonomy_file() -> Path:
    """Return the most recently modified built_taxonomy_*.csv in data/build_output/."""
    candidates = list(INPUT_DIR.glob("built_taxonomy_*.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"No built_taxonomy_*.csv files found in {INPUT_DIR}. "
            "Run build_taxonomy.py first."
        )
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    print(f"[common_names] Using taxonomy file: {latest}")
    return latest


def make_output_path(input_path: Path) -> Path:
    """
    Given a taxonomy file path like built_taxonomy_20251205.csv,
    return built_taxonomy_20251205_with_common_names.csv in the output directory.
    """
    stem = input_path.stem  # e.g. 'built_taxonomy_20251205'
    return OUTPUT_DIR / f"{stem}_with_common_names.csv"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_path(tsn: str) -> Path:
    return CACHE_DIR / f"{tsn}.json"


def _load_cache(tsn: str) -> Dict[str, Any] | None:
    path = _cache_path(tsn)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_cache(tsn: str, data: Dict[str, Any]) -> None:
    path = _cache_path(tsn)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# ITIS async HTTP
# ---------------------------------------------------------------------------


async def _itis_get_async(
    session: aiohttp.ClientSession, endpoint: str, tsn: str
) -> Dict[str, Any] | None:
    """
    Async GET wrapper for ITIS JSON endpoints.

    Returns None on error (we'll treat missing info as unknown/empty).
    """
    url = f"{BASE_URL}/{endpoint}?tsn={tsn}"
    try:
        async with session.get(url, timeout=10) as resp:
            if resp.status != 200:
                # Not fatal; just means no info
                return None

            # ITIS sometimes uses text/json;charset=iso-8859-1 instead of application/json,
            # which confuses aiohttp's default json() check. Override content_type.
            try:
                return await resp.json(content_type=None)
            except Exception:
                # Fallback: parse manually from text
                text = await resp.text()
                import json as _json

                try:
                    return _json.loads(text)
                except Exception:
                    return None
    except Exception as e:
        print(f"[WARN] ITIS request failed for TSN {tsn}, endpoint {endpoint}: {e}")
        return None


# ---------------------------------------------------------------------------
# Extract English common names from ITIS data
# ---------------------------------------------------------------------------


def extract_common_names(itis_data: Dict[str, Any]) -> List[str]:
    """
    Extract English common names from ITIS getCommonNamesFromTSN response.
    
    Returns a list of unique English common names.
    """
    names: Set[str] = set()
    
    common_names_list = itis_data.get("commonNames") or []
    
    for rec in common_names_list:
        if isinstance(rec, dict):
            language = rec.get("language", "")
            common_name = rec.get("commonName", "")
            
            # Only include English names
            if language == "English" and common_name:
                names.add(common_name.strip())
    
    return sorted(names)  # Return sorted list for consistency


# ---------------------------------------------------------------------------
# Async driver for leaf TSNs
# ---------------------------------------------------------------------------


async def fetch_common_names_for_tsn(
    tsn: str,
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
) -> Tuple[str, List[str]]:
    """
    Fetch ITIS common names for a single TSN (using cache when possible).

    Returns:
        (tsn, list_of_english_common_names)
    """
    # 1) Check cache first (no network)
    cached = _load_cache(tsn)
    if cached is None:
        async with sem:
            data = await _itis_get_async(session, "getCommonNamesFromTSN", tsn)
        
        if data is None:
            data = {"tsn": tsn, "commonNames": []}
        
        _save_cache(tsn, data)
    else:
        data = cached

    common_names = extract_common_names(data)
    return tsn, common_names


async def fetch_all_common_names_async(
    tsns: List[str],
    concurrency: int = 10,
) -> Dict[str, List[str]]:
    """
    Async driver that fetches common names for a set of TSNs using limited concurrency.

    Returns a dict mapping TSN -> list of English common names.
    """
    common_names_map: Dict[str, List[str]] = {}

    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=10,
        sock_connect=10,
        sock_read=10,
    )
    connector = aiohttp.TCPConnector(limit=concurrency)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        sem = asyncio.Semaphore(concurrency)

        tasks = [
            fetch_common_names_for_tsn(tsn, session, sem)
            for tsn in tsns
        ]

        for coro in tqdm(
            asyncio.as_completed(tasks),
            total=len(tasks),
            desc="Fetching common names for leaf taxa",
            unit="tsn",
        ):
            tsn, names = await coro
            common_names_map[tsn] = names

    return common_names_map


# ---------------------------------------------------------------------------
# Propagate common names from leaves up the taxonomy tree
# ---------------------------------------------------------------------------


def propagate_common_names(
    tax: pd.DataFrame,
    leaf_common_names: Dict[str, List[str]],
) -> Dict[str, str]:
    """
    Given:
      - tax: taxonomy table with columns 'tsn' and 'parentTsn'
      - leaf_common_names: common names for leaf TSNs only

    Return:
      - full_common_names: common names for *all* TSNs as semicolon-separated strings
          * Leaf TSNs: as given from leaf_common_names
          * Internal nodes: all unique common names from all children
          * Anything we can't resolve gets empty string
    """
    tax = tax.copy()
    tax["tsn"] = tax["tsn"].astype(str)
    if "parentTsn" not in tax.columns:
        raise ValueError("Taxonomy file must have a 'parentTsn' column to propagate names.")
    tax["parentTsn"] = tax["parentTsn"].astype(str)

    all_tsns = set(tax["tsn"].dropna())

    # Build child list per parent
    children_map: Dict[str, List[str]] = defaultdict(list)
    for _, row in tax[["tsn", "parentTsn"]].dropna().iterrows():
        parent = row["parentTsn"]
        child = row["tsn"]
        children_map[parent].append(child)

    # Start with leaves - convert lists to sets for easier merging
    full_common_names: Dict[str, Set[str]] = {
        tsn: set(names) for tsn, names in leaf_common_names.items()
    }

    remaining = all_tsns - set(full_common_names.keys())

    # Iteratively fill parents when all their children are known
    changed = True
    while changed and remaining:
        changed = False
        for tsn in list(remaining):
            children = children_map.get(tsn, [])

            if not children:
                # No children: weird internal or isolated node; empty names
                full_common_names[tsn] = set()
                remaining.remove(tsn)
                changed = True
                continue

            # Only propagate if we know all children already
            if all(child in full_common_names for child in children):
                # Collect all unique common names from children
                combined_names: Set[str] = set()
                for child in children:
                    combined_names.update(full_common_names[child])
                
                full_common_names[tsn] = combined_names
                remaining.remove(tsn)
                changed = True

    # Any leftover (e.g., cycles, missing links) -> empty
    for tsn in remaining:
        full_common_names[tsn] = set()

    # Convert sets to semicolon-separated strings, sorted for consistency
    return {
        tsn: "; ".join(sorted(names)) if names else ""
        for tsn, names in full_common_names.items()
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    taxonomy_path = get_latest_taxonomy_file()
    output_path = make_output_path(taxonomy_path)

    tax = pd.read_csv(
        taxonomy_path,
        dtype={"tsn": str, "parentTsn": str},
        low_memory=False,
    )
    if "tsn" not in tax.columns:
        raise ValueError("Taxonomy file must have a 'tsn' column.")
    if "parentTsn" not in tax.columns:
        raise ValueError("Taxonomy file must have a 'parentTsn' column to propagate names.")

    # Ensure string types
    tax["tsn"] = tax["tsn"].astype(str)
    tax["parentTsn"] = tax["parentTsn"].astype(str)

    # Identify leaves (tsns that never appear as parentTsn)
    all_tsns = set(tax["tsn"].dropna())
    parent_tsns = set(tax["parentTsn"].dropna())
    leaf_tsns = sorted(all_tsns - parent_tsns)

    print(f"[common_names] Found {len(all_tsns)} unique TSNs in taxonomy.")
    print(f"[common_names] Identified {len(leaf_tsns)} leaf TSNs (will query ITIS for these).")

    # If you want to test on a subset, uncomment and adjust:
    # test_size = 100
    # leaf_tsns = leaf_tsns[:test_size]
    # print(f"[common_names] TEST RUN: querying ITIS for {len(leaf_tsns)} leaf TSNs.")

    # Run async fetcher for leaves only
    leaf_common_names_map = asyncio.run(
        fetch_all_common_names_async(leaf_tsns, concurrency=10)
    )

    # Propagate up to all TSNs
    full_common_names_map = propagate_common_names(tax, leaf_common_names_map)

    # Map back to dataframe
    tax["common_names"] = tax["tsn"].map(lambda tsn: full_common_names_map.get(tsn, ""))

    tax.to_csv(output_path, index=False)
    print(f"[common_names] Written taxonomy with common names to: {output_path}")

    # Simple summary
    has_names = (tax["common_names"] != "").sum()
    total = len(tax)
    print(f"[common_names] {has_names}/{total} taxa have common names ({100*has_names/total:.1f}%)")
    
    # Show some examples
    print("\n[common_names] Sample entries with common names:")
    sample = tax[tax["common_names"] != ""].head(10)
    for _, row in sample.iterrows():
        print(f"  TSN {row['tsn']}: {row['common_names']}")


if __name__ == "__main__":
    main()