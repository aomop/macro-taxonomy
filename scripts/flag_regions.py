#!/usr/bin/env python
"""
Add an in_region boolean flag to the taxonomy table using ITIS jurisdiction
and geographic division data.

Definition of in_region (per TSN):
  * True  if ANY associated region term maps to True in region_term_lookup.csv.
  * True  if NO geographic / jurisdictional terms are found at all
           (unknown → allowed).
  * False if we have at least one mapped term and ALL mapped values are False.

For performance and data sparsity reasons:
  * We only query ITIS for leaf taxa (taxa with no children, typically species).
  * We then propagate in_region upward through the parentTsn hierarchy:
      - Leaf: as classified from ITIS
      - Internal node: True if ANY child is True, else False
      - Any unresolved nodes default to True (conservative).

Run this AFTER build_taxonomy.py, which should produce a built_taxonomy_*.csv
with columns including at least 'tsn' and 'parentTsn'.

You must also have a term lookup at:
    data/region_term_lookup.csv

with at least the columns:
    term, in_region_term

Where:
  - term: exact strings from region_term_summary.csv (normalized/trimmed)
  - in_region_term: TRUE/FALSE (or 1/0, Yes/No, etc.)

Usage examples:
    # Build taxonomy, then flag regions
    python scripts/build_taxonomy.py
    python scripts/flag_regions.py

    # Full pipeline (adds TSNs, rebuilds, flags regions)
    python taxa_pipeline.py --tsn 12345 67890
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import aiohttp
import pandas as pd
from tqdm.asyncio import tqdm


# ---------------------------------------------------------------------------
# Paths and globals
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "flag_cache"
TERM_LOOKUP_PATH = DATA_DIR / "region_term_lookup.csv"

CACHE_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://www.itis.gov/ITISWebService/jsonservice"


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
# Term lookup
# ---------------------------------------------------------------------------


def _normalize_text(s: str) -> str:
    """Lowercase and strip most punctuation/extra whitespace."""
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_term_lookup() -> Dict[str, bool]:
    """
    Load term -> in_region mapping from region_term_lookup.csv.
    Assumes columns: 'term' and 'in_region_term' (TRUE/FALSE or similar).
    """
    if not TERM_LOOKUP_PATH.exists():
        raise FileNotFoundError(
            f"Term lookup file not found: {TERM_LOOKUP_PATH}\n"
            "Create this from region_term_summary.csv with columns "
            "'term' and 'in_region_term'."
        )

    df = pd.read_csv(TERM_LOOKUP_PATH, dtype={"term": str})
    if "term" not in df.columns or "in_region_term" not in df.columns:
        raise ValueError(
            f"{TERM_LOOKUP_PATH} must contain columns 'term' and 'in_region_term'."
        )

    df["term"] = df["term"].astype(str).map(_normalize_text)

    def as_bool(x: Any) -> bool:
        if isinstance(x, str):
            x = x.strip().lower()
            if x in ("true", "t", "1", "yes", "y"):
                return True
            if x in ("false", "f", "0", "no", "n"):
                return False
        return bool(x)

    term_map = {
        row["term"]: as_bool(row["in_region_term"])
        for _, row in df.iterrows()
    }
    print(f"[flag_regions] Loaded {len(term_map)} region terms from lookup.")
    return term_map


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
# Extract region terms from a cached ITIS record
# ---------------------------------------------------------------------------


def extract_region_terms(itis_data: Dict[str, Any]) -> List[str]:
    """
    Extract normalized region terms from a cached ITIS record.

    Handles both:
      - jurisdictionalOrigins / geoDivisions (current ITIS JSON)
      - jurisdictionalOriginList / geographicDivisionList (fallback)

    Returns a list of normalized strings like:
        ["north america", "southern asia"]
    """
    terms: List[str] = []

    # --- Jurisdiction ---
    juris = itis_data.get("jurisdiction") or {}
    origin_list = (
        juris.get("jurisdictionalOrigins")
        or juris.get("jurisdictionalOriginList")
        or []
    )

    for rec in origin_list:
        if isinstance(rec, dict):
            val = rec.get("jurisdictionValue") or rec.get("jurisdiction")
            if val:
                terms.append(_normalize_text(str(val)))

    # --- Geographic divisions ---
    geo = itis_data.get("geo_divisions") or {}
    div_list = (
        geo.get("geoDivisions")
        or geo.get("geographicDivisionList")
        or []
    )

    for rec in div_list:
        if isinstance(rec, dict):
            val = (
                rec.get("geographicValue")
                or rec.get("geographicArea")
                or rec.get("geographicLocation")
            )
            if val:
                terms.append(_normalize_text(str(val)))

    return terms


def classify_in_region(itis_data: Dict[str, Any], term_map: Dict[str, bool]) -> bool:
    """
    Use the term_map (term -> True/False) to determine in_region.

    Rules:
      * If we find no geo/jurisdictional terms at all:
          -> True  (unknown, don't block)
      * If ANY mapped term is True:
          -> True
      * Else, if we have at least one mapped term (and none are True):
          -> False
      * Else (only unmapped/weird terms):
          -> True  (unknown, conservative)
    """
    terms = extract_region_terms(itis_data)

    if not terms:
        # No geo/jurisdiction data at all
        return True

    mapped_values: List[bool] = []
    for t in terms:
        if t in term_map:
            mapped_values.append(term_map[t])

    if not mapped_values:
        # We saw terms but none are in our lookup (e.g., new pattern) → treat as unknown
        return True

    if any(mapped_values):
        # TRUE overrides FALSE
        return True

    # At least one mapped term and none True → all False
    return False


# ---------------------------------------------------------------------------
# Async driver for leaf TSNs
# ---------------------------------------------------------------------------


async def fetch_and_flag_tsn(
    tsn: str,
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    term_map: Dict[str, bool],
) -> Tuple[str, bool]:
    """
    Fetch ITIS data for a single TSN (using cache when possible),
    then compute in_region.

    Returns:
        (tsn, in_region)
    """
    # 1) Check cache first (no network)
    cached = _load_cache(tsn)
    if cached is None:
        async with sem:
            juris_task = asyncio.create_task(
                _itis_get_async(session, "getJurisdictionalOriginFromTSN", tsn)
            )
            geo_task = asyncio.create_task(
                _itis_get_async(session, "getGeographicDivisionsFromTSN", tsn)
            )
            juris, geo = await asyncio.gather(juris_task, geo_task)

        data: Dict[str, Any] = {
            "tsn": tsn,
            "jurisdiction": juris,
            "geo_divisions": geo,
        }
        _save_cache(tsn, data)
    else:
        data = cached

    in_region = classify_in_region(data, term_map)
    return tsn, in_region


async def flag_all_tsns_async(
    tsns: List[str],
    term_map: Dict[str, bool],
    concurrency: int = 10,
) -> Dict[str, bool]:
    """
    Async driver that flags a set of TSNs using limited concurrency.

    Returns a single in_region map for the provided TSNs.
    """
    in_region_map: Dict[str, bool] = {}

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
            fetch_and_flag_tsn(tsn, session, sem, term_map)
            for tsn in tsns
        ]

        for coro in tqdm.as_completed(
            tasks,
            total=len(tasks),
            desc="Flagging leaf taxa",
            unit="tsn",
        ):
            tsn, in_region = await coro
            in_region_map[tsn] = in_region

    return in_region_map


# ---------------------------------------------------------------------------
# Propagate in_region from leaves up the taxonomy tree
# ---------------------------------------------------------------------------


def propagate_in_region(
    tax: pd.DataFrame,
    leaf_in_region: Dict[str, bool],
) -> Dict[str, bool]:
    """
    Given:
      - tax: taxonomy table with columns 'tsn' and 'parentTsn'
      - leaf_in_region: in_region values for leaf TSNs only

    Return:
      - full_in_region: in_region for *all* TSNs, propagated upward:
          * Leaf TSNs: as given from leaf_in_region
          * Internal nodes: True if any child is True, else False
          * Anything we can't resolve cleanly defaults to True
    """
    tax = tax.copy()
    tax["tsn"] = tax["tsn"].astype(str)
    if "parentTsn" not in tax.columns:
        raise ValueError("Taxonomy file must have a 'parentTsn' column to propagate flags.")
    tax["parentTsn"] = tax["parentTsn"].astype(str)

    all_tsns = set(tax["tsn"].dropna())

    # Build child list per parent
    children_map: Dict[str, List[str]] = defaultdict(list)
    for _, row in tax[["tsn", "parentTsn"]].dropna().iterrows():
        parent = row["parentTsn"]
        child = row["tsn"]
        children_map[parent].append(child)

    # Start with leaves
    full_in_region: Dict[str, bool] = dict(leaf_in_region)

    remaining = all_tsns - set(full_in_region.keys())

    # Iteratively fill parents when all their children are known
    changed = True
    while changed and remaining:
        changed = False
        for tsn in list(remaining):
            children = children_map.get(tsn, [])

            if not children:
                # No children: weird internal or isolated node; default to True
                full_in_region[tsn] = True
                remaining.remove(tsn)
                changed = True
                continue

            # Only propagate if we know all children already
            if all(child in full_in_region for child in children):
                full_in_region[tsn] = any(full_in_region[child] for child in children)
                remaining.remove(tsn)
                changed = True

    # Any leftover (e.g., cycles, missing links) -> default True
    for tsn in remaining:
        full_in_region[tsn] = True

    return full_in_region


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def flag_regions(tax: pd.DataFrame) -> pd.DataFrame:
    """Add in_region column to taxonomy DataFrame and return it."""
    term_map = load_term_lookup()

    tax = tax.copy()
    if "tsn" not in tax.columns:
        raise ValueError("Taxonomy file must have a 'tsn' column.")
    if "parentTsn" not in tax.columns:
        raise ValueError("Taxonomy file must have a 'parentTsn' column to propagate flags.")

    # Ensure string types
    tax["tsn"] = tax["tsn"].astype(str)
    tax["parentTsn"] = tax["parentTsn"].astype(str)

    # Identify leaves (tsns that never appear as parentTsn)
    all_tsns = set(tax["tsn"].dropna())
    parent_tsns = set(tax["parentTsn"].dropna())
    leaf_tsns = sorted(all_tsns - parent_tsns)

    print(f"[flag_regions] Found {len(all_tsns)} unique TSNs in taxonomy.")
    print(f"[flag_regions] Identified {len(leaf_tsns)} leaf TSNs (will query ITIS for these).")

    # Run async flagger for leaves only
    leaf_in_region_map = asyncio.run(
        flag_all_tsns_async(leaf_tsns, term_map, concurrency=10)
    )

    # Propagate up to all TSNs
    full_in_region_map = propagate_in_region(tax, leaf_in_region_map)

    # Map back to dataframe; default True if somehow missing
    tax["in_region"] = tax["tsn"].map(lambda tsn: full_in_region_map.get(tsn, True))

    # Simple summary
    print("[flag_regions] in_region value counts:")
    print(tax["in_region"].value_counts())

    return tax


