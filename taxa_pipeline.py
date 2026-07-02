#!/usr/bin/env python
"""
Master script to update taxonomy.

Steps:
  1. Optionally add new TSNs to tsn_list_*.csv (if --tsn is supplied).
     - Reads latest tsn_list_*.csv
     - Writes a new dated snapshot.
  2. Build taxonomy table from latest tsn_list (in memory).
  3. Add in_region flags (in memory).
  4. Add common_names (in memory).
  5. Write a single authoritative CSV to data/output/.

Usage examples:
    # Just rebuild taxonomy + flags + common names
    python taxa_pipeline.py

    # Add new TSNs and then rebuild
    python taxa_pipeline.py --tsn 12345 67890

    # Re-run only the common names step on the latest output
    python taxa_pipeline.py --common-names-only
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import List

import pandas as pd

from scripts.build_taxonomy import build_taxonomy
from scripts.flag_regions import flag_regions
from scripts.scrape_common import add_common_names

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "add_tsns_data"
OUTPUT_DIR = DATA_DIR / "output"
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full taxonomy update pipeline.")
    parser.add_argument(
        "--tsn",
        nargs="+",
        help="Optional TSNs to add to the latest tsn_list_*.csv before rebuilding.",
    )
    parser.add_argument(
        "--common-names-only",
        action="store_true",
        help="Re-run only the common names step on the latest output file.",
    )
    return parser.parse_args()


# --- Helpers shared with add_tsns.py ----------------------------------------

def get_latest_tsn_list() -> Path:
    candidates = list(INPUT_DIR.glob("tsn_list_*.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"No tsn_list_*.csv files found in {INPUT_DIR}. "
            "Create an initial tsn_list_YYYYMMDD.csv first."
        )
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    print(f"[pipeline] Using latest TSN list as base: {latest}")
    return latest


def make_new_tsn_list_path() -> Path:
    today = date.today().strftime("%Y%m%d")
    base = INPUT_DIR / f"tsn_list_{today}.csv"
    if not base.exists():
        return base

    i = 1
    while True:
        candidate = INPUT_DIR / f"tsn_list_{today}_{i}.csv"
        if not candidate.exists():
            return candidate
        i += 1


def append_tsns(tsns_to_add: List[str]) -> Path:
    """Append TSNs to latest tsn_list and write a new dated snapshot."""
    latest_path = get_latest_tsn_list()
    df = pd.read_csv(latest_path, dtype={"TSN": str})
    if "TSN" not in df.columns:
        raise ValueError(f"{latest_path} must have a 'TSN' column.")

    existing = set(df["TSN"].astype(str))
    new_tsns = [str(t) for t in tsns_to_add if str(t) not in existing]

    if not new_tsns:
        print("[pipeline] No new TSNs to add (all already present).")
        return latest_path

    print(f"[pipeline] Adding {len(new_tsns)} new TSN(s): {', '.join(new_tsns)}")

    extra_cols = {col: [pd.NA] * len(new_tsns) for col in df.columns if col != "TSN"}
    new_rows = pd.DataFrame({"TSN": new_tsns, **extra_cols})
    updated = pd.concat([df, new_rows], ignore_index=True)

    out_path = make_new_tsn_list_path()
    updated.to_csv(out_path, index=False)
    print(f"[pipeline] New tsn_list snapshot written to: {out_path}")
    return out_path


# --- Pipeline ---------------------------------------------------------------

def get_latest_output() -> Path:
    """Return the most recent taxonomy_*.csv in data/output/."""
    candidates = list(OUTPUT_DIR.glob("taxonomy_*.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"No taxonomy_*.csv files found in {OUTPUT_DIR}. "
            "Run the full pipeline first."
        )
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    print(f"[pipeline] Using latest output as base: {latest}")
    return latest


def main() -> None:
    args = parse_args()

    if args.common_names_only:
        # Re-run only the common names step on the latest output
        base_path = get_latest_output()
        tax = pd.read_csv(base_path, dtype={"tsn": str}, low_memory=False)
        tax = tax.drop(columns=["common_names"], errors="ignore")

        print("\n[pipeline] Re-running common names only...")
        tax = add_common_names(tax)

        today = date.today().strftime("%Y%m%d")
        output_path = OUTPUT_DIR / f"taxonomy_{today}.csv"
        tax.to_csv(output_path, index=False, na_rep="NA")
        print(f"\n[pipeline] Updated taxonomy written to: {output_path}")
        print(f"[pipeline] Shape: {tax.shape[0]} rows x {tax.shape[1]} columns")
        print("\nCommon names update completed successfully.")
        return

    # 1) Optionally add TSNs
    if args.tsn:
        append_tsns(args.tsn)

    # 2) Build base taxonomy (in memory)
    print("\n[pipeline] Step 1/3: Building taxonomy...")
    tax = build_taxonomy()

    # 3) Add region flags (in memory)
    print("\n[pipeline] Step 2/3: Flagging regions...")
    tax = flag_regions(tax)

    # 4) Add common names (in memory)
    print("\n[pipeline] Step 3/3: Adding common names...")
    tax = add_common_names(tax)

    # 5) Write single authoritative output
    today = date.today().strftime("%Y%m%d")
    output_path = OUTPUT_DIR / f"taxonomy_{today}.csv"
    tax.to_csv(output_path, index=False, na_rep="NA")
    print(f"\n[pipeline] Final taxonomy written to: {output_path}")
    print(f"[pipeline] Shape: {tax.shape[0]} rows x {tax.shape[1]} columns")
    print(f"[pipeline] Columns: {list(tax.columns)}")

    print("\nAll steps completed successfully.")


if __name__ == "__main__":
    main()
