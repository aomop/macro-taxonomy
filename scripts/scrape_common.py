#!/usr/bin/env python
"""
Add English common names to the taxonomy table.

Sources (in priority order):
  1. ITIS — queried by TSN for all taxa
  2. iNaturalist — queried by scientific name for taxa where ITIS returned
     nothing (genus-and-above by default; species optionally)

Names are NOT propagated up the hierarchy from child taxa.

Run this AFTER build_taxonomy.py, which should produce a built_taxonomy_*.csv
with columns including at least 'tsn', 'taxon', and 'level'.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import aiohttp
import pandas as pd
from tqdm.asyncio import tqdm


# ---------------------------------------------------------------------------
# Paths and globals
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
ITIS_CACHE_DIR = DATA_DIR / "common_names_cache"
INAT_CACHE_DIR = DATA_DIR / "inat_cache"

ITIS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
INAT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

ITIS_BASE_URL = "https://www.itis.gov/ITISWebService/jsonservice"
INAT_BASE_URL = "https://api.inaturalist.org/v1"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _load_json_cache(cache_dir: Path, key: str) -> Dict[str, Any] | None:
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_json_cache(cache_dir: Path, key: str, data: Dict[str, Any]) -> None:
    path = cache_dir / f"{key}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# Rate limiter for iNaturalist
# ---------------------------------------------------------------------------


class RateLimiter:
    """Serialises request starts to at most *per_second* per second."""

    def __init__(self, per_second: float) -> None:
        self._min_interval = 1.0 / per_second
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = asyncio.get_event_loop().time()


# ---------------------------------------------------------------------------
# Generic async HTTP helpers
# ---------------------------------------------------------------------------


async def _get_json(
    session: aiohttp.ClientSession,
    url: str,
    label: str = "",
    rate_limiter: RateLimiter | None = None,
) -> Dict[str, Any] | None:
    """GET *url* and return parsed JSON, or None on any error."""
    try:
        if rate_limiter:
            await rate_limiter.acquire()
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            try:
                return await resp.json(content_type=None)
            except Exception:
                text = await resp.text()
                try:
                    return json.loads(text)
                except Exception:
                    return None
    except Exception as e:
        print(f"[WARN] Request failed ({label}): {e}")
        return None


# ---------------------------------------------------------------------------
# Name normalisation helpers
# ---------------------------------------------------------------------------


def _normalise_key(name: str) -> str:
    """Lowercase and strip everything except letters and digits for dedup."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def dedupe_common_names(names: List[str]) -> List[str]:
    """
    Deduplicate near-identical common names and title-case the results.

    Names that differ only by spacing, hyphens, or capitalisation are treated
    as duplicates.  The longest variant is kept (most likely to have proper
    spacing), then title-cased.

    Example:
        ["lightning bugs", "lightningbugs", "fireflies"]
        -> ["Fireflies", "Lightning Bugs"]
    """
    groups: Dict[str, str] = {}
    for name in names:
        key = _normalise_key(name)
        if not key:
            continue
        existing = groups.get(key, "")
        if len(name) > len(existing):
            groups[key] = name
    return sorted(v.title() for v in groups.values())


# ---------------------------------------------------------------------------
# ITIS: fetch common names by TSN
# ---------------------------------------------------------------------------


def extract_itis_common_names(itis_data: Dict[str, Any]) -> List[str]:
    """Extract English common names from an ITIS getCommonNamesFromTSN response."""
    names: List[str] = []
    for rec in itis_data.get("commonNames") or []:
        if isinstance(rec, dict):
            if rec.get("language") == "English" and rec.get("commonName"):
                names.append(rec["commonName"].strip())
    return dedupe_common_names(names)


async def _fetch_itis_common_names(
    tsn: str,
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
) -> Tuple[str, List[str]]:
    """Return (tsn, list_of_english_names) using ITIS, with per-TSN cache."""
    cached = _load_json_cache(ITIS_CACHE_DIR, tsn)
    if cached is None:
        async with sem:
            url = f"{ITIS_BASE_URL}/getCommonNamesFromTSN?tsn={tsn}"
            data = await _get_json(session, url, label=f"ITIS TSN {tsn}")
        if data is None:
            data = {"tsn": tsn, "commonNames": []}
        _save_json_cache(ITIS_CACHE_DIR, tsn, data)
    else:
        data = cached
    return tsn, extract_itis_common_names(data)


async def fetch_all_itis(
    tsns: List[str],
    concurrency: int = 10,
) -> Dict[str, List[str]]:
    """Fetch ITIS common names for all *tsns* (async, cached)."""
    result: Dict[str, List[str]] = {}
    timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=10)
    connector = aiohttp.TCPConnector(limit=concurrency)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        sem = asyncio.Semaphore(concurrency)
        tasks = [_fetch_itis_common_names(tsn, session, sem) for tsn in tsns]
        for coro in tqdm.as_completed(
            tasks,
            total=len(tasks),
            desc="ITIS common names",
            unit="tsn",
        ):
            tsn, names = await coro
            result[tsn] = names
    return result


# ---------------------------------------------------------------------------
# iNaturalist: fetch common names by scientific name + rank
# ---------------------------------------------------------------------------

# iNat supports these rank strings (lowercase).  Map our taxonomy's 'level'
# column values to iNat rank strings.
_RANK_MAP = {
    "Kingdom": "kingdom",
    "Subkingdom": "subkingdom",
    "Infrakingdom": "infrakingdom",
    "Superphylum": "superphylum",
    "Phylum": "phylum",
    "Subphylum": "subphylum",
    "Superclass": "superclass",
    "Class": "class",
    "Subclass": "subclass",
    "Infraclass": "infraclass",
    "Superorder": "superorder",
    "Order": "order",
    "Suborder": "suborder",
    "Infraorder": "infraorder",
    "Superfamily": "superfamily",
    "Family": "family",
    "Subfamily": "subfamily",
    "Tribe": "tribe",
    "Subtribe": "subtribe",
    "Genus": "genus",
    "Subgenus": "subgenus",
    "Species": "species",
}


def extract_inat_common_name(inat_data: Dict[str, Any], scientific_name: str) -> str:
    """
    Return the preferred English common name from an iNat /v1/taxa response,
    matching *scientific_name* exactly.  Returns empty string if not found.
    """
    for result in inat_data.get("results") or []:
        if result.get("name") == scientific_name:
            name = result.get("preferred_common_name") or result.get("english_common_name") or ""
            return name.strip().title()
    return ""


async def _fetch_inat_common_name(
    tsn: str,
    scientific_name: str,
    rank: str,
    session: aiohttp.ClientSession,
    rate_limiter: RateLimiter,
) -> Tuple[str, str]:
    """Return (tsn, common_name) from iNaturalist, with per-TSN cache."""
    cached = _load_json_cache(INAT_CACHE_DIR, tsn)
    if cached is not None:
        return tsn, extract_inat_common_name(cached, scientific_name)

    # Build query URL
    inat_rank = _RANK_MAP.get(rank, rank.lower())
    url = f"{INAT_BASE_URL}/taxa?q={scientific_name}&rank={inat_rank}&per_page=5"

    data = await _get_json(session, url, label=f"iNat {scientific_name}", rate_limiter=rate_limiter)
    if data is None:
        data = {"results": []}
    _save_json_cache(INAT_CACHE_DIR, tsn, data)
    return tsn, extract_inat_common_name(data, scientific_name)


async def fetch_all_inat(
    taxa: List[Tuple[str, str, str]],
    rate_per_sec: float = 1.5,
) -> Dict[str, str]:
    """
    Fetch iNat common names for a list of (tsn, scientific_name, rank) tuples.

    Returns a dict mapping TSN -> common name string (may be empty).
    Rate-limited to *rate_per_sec* actual HTTP requests per second.
    """
    result: Dict[str, str] = {}
    if not taxa:
        return result

    rate_limiter = RateLimiter(rate_per_sec)
    timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=10)
    connector = aiohttp.TCPConnector(limit=5)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        tasks = [
            _fetch_inat_common_name(tsn, name, rank, session, rate_limiter)
            for tsn, name, rank in taxa
        ]
        for coro in tqdm.as_completed(
            tasks,
            total=len(tasks),
            desc="iNaturalist common names",
            unit="taxon",
        ):
            tsn, name = await coro
            result[tsn] = name
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def add_common_names(
    tax: pd.DataFrame,
    inat_for_species: bool = False,
) -> pd.DataFrame:
    """
    Add a ``common_names`` column to *tax* and return it.

    1. Query ITIS for every TSN.
    2. For taxa with no ITIS name, query iNaturalist by scientific name.
       By default only genus-and-above are sent to iNat.  Pass
       ``inat_for_species=True`` to also query species (much slower on the
       first uncached run).
    """
    tax = tax.copy()
    for col in ("tsn", "taxon", "level"):
        if col not in tax.columns:
            raise ValueError(f"Taxonomy must have a '{col}' column.")

    tax["tsn"] = tax["tsn"].astype(str)

    all_tsns = sorted(set(tax["tsn"].dropna()))
    print(f"[common_names] {len(all_tsns)} unique TSNs in taxonomy.")

    # --- Step 1: ITIS -------------------------------------------------------
    print("[common_names] Step 1/2: Querying ITIS for all TSNs...")
    itis_map = asyncio.run(fetch_all_itis(all_tsns, concurrency=10))

    # Compute ITIS results as semicolon-separated strings
    itis_str: Dict[str, str] = {
        tsn: "; ".join(names) if names else ""
        for tsn, names in itis_map.items()
    }

    itis_hits = sum(1 for v in itis_str.values() if v)
    print(f"[common_names] ITIS provided names for {itis_hits}/{len(all_tsns)} taxa.")

    # --- Step 2: iNaturalist for gaps ---------------------------------------
    # Build list of (tsn, scientific_name, rank) for taxa that ITIS missed.
    gap_rows = tax[tax["tsn"].map(lambda t: not itis_str.get(t, ""))].copy()
    if not inat_for_species:
        gap_rows = gap_rows[gap_rows["level"] != "Species"]

    # Deduplicate by TSN (same TSN may appear in multiple rows in theory)
    gap_rows = gap_rows.drop_duplicates(subset="tsn")
    taxa_to_query = list(
        zip(gap_rows["tsn"], gap_rows["taxon"], gap_rows["level"])
    )

    n_gaps = len(taxa_to_query)
    if n_gaps:
        est_minutes = n_gaps / (1.5 * 60)
        print(
            f"[common_names] Step 2/2: Querying iNaturalist for {n_gaps} taxa "
            f"without ITIS names (est. ~{est_minutes:.0f} min uncached)..."
        )
        inat_map = asyncio.run(fetch_all_inat(taxa_to_query, rate_per_sec=1.5))
        inat_hits = sum(1 for v in inat_map.values() if v)
        print(f"[common_names] iNaturalist provided names for {inat_hits}/{n_gaps} queried taxa.")
    else:
        inat_map = {}
        print("[common_names] Step 2/2: No gaps to fill with iNaturalist.")

    # --- Merge: ITIS takes priority, iNat fills gaps ------------------------
    def _resolve(tsn: str) -> str:
        itis_name = itis_str.get(tsn, "")
        if itis_name:
            return itis_name
        return inat_map.get(tsn, "")

    tax["common_names"] = tax["tsn"].map(_resolve)

    has_names = (tax["common_names"] != "").sum()
    total = len(tax)
    print(f"[common_names] {has_names}/{total} taxa have common names ({100 * has_names / total:.1f}%)")
    return tax


