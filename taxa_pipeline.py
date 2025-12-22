#!/usr/bin/env python
"""
Master script to update taxonomy.

Steps:
  1. Optionally add new TSNs to tsn_list_*.csv (if --tsn is supplied).
     - Reads latest tsn_list_*.csv
     - Writes a new dated snapshot.
  2. Run build_taxonomy.py to regenerate the taxonomy table from latest tsn_list.
  3. Run flag_regions.py to add in_region and in_continent flags to the latest taxonomy.

Usage examples:
    # Just rebuild taxonomy + flags
    python taxa_pipeline.py

    # Add new TSNs and then rebuild + flag
    python taxa_pipeline.py --tsn 12345 67890
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import List

import pandas as pd

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "add_tsns_output"
OUTPUT_DIR = DATA_DIR / "build_input"
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BUILD_TAXONOMY_SCRIPT = PROJECT_ROOT / "scripts" / "build_taxonomy.py"
FLAG_REGIONS_SCRIPT = PROJECT_ROOT / "scripts" / "flag_regions.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full taxonomy update pipeline.")
    parser.add_argument(
        "--tsn",
        nargs="+",
        help="Optional TSNs to add to the latest tsn_list_*.csv before rebuilding.",
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
    base = OUTPUT_DIR / f"tsn_list_{today}.csv"
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
        return sync_latest_tsn_list()

    print(f"[pipeline] Adding {len(new_tsns)} new TSN(s): {', '.join(new_tsns)}")

    extra_cols = {col: [pd.NA] * len(new_tsns) for col in df.columns if col != "TSN"}
    new_rows = pd.DataFrame({"TSN": new_tsns, **extra_cols})
    updated = pd.concat([df, new_rows], ignore_index=True)

    out_path = make_new_tsn_list_path()
    updated.to_csv(out_path, index=False)
    print(f"[pipeline] New tsn_list snapshot written to: {out_path}")
    return out_path


def sync_latest_tsn_list() -> Path:
    """Ensure latest tsn_list is available in the pipeline output directory."""
    latest_input = get_latest_tsn_list()
    candidates = list(OUTPUT_DIR.glob("tsn_list_*.csv"))
    latest_output = max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

    if latest_output and latest_output.read_bytes() == latest_input.read_bytes():
        return latest_output

    out_path = make_new_tsn_list_path()
    out_path.write_bytes(latest_input.read_bytes())
    print(f"[pipeline] Synced TSN list to build input: {out_path}")
    return out_path


# --- Subprocess helpers -----------------------------------------------------

def run_script(script_path: Path) -> None:
    """Run another Python script with the same interpreter, raising on failure."""
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")

    print(f"\n[RUN] {script_path}")
    result = subprocess.run(
        [sys.executable, str(script_path)],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Script failed: {script_path} (exit code {result.returncode})"
        )


def main() -> None:
    args = parse_args()

    # 1) Optionally add TSNs
    if args.tsn:
        append_tsns(args.tsn)
    else:
        sync_latest_tsn_list()

    # 2) Rebuild taxonomy (uses latest tsn_list internally)
    run_script(BUILD_TAXONOMY_SCRIPT)

    # 3) Add region flags (uses latest built_taxonomy internally)
    run_script(FLAG_REGIONS_SCRIPT)

    print("\nAll steps completed successfully.")


if __name__ == "__main__":
    main()
