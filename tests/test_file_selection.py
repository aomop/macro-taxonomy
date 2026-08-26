"""Tests for deterministic dated-filename selection.

These cover the regression that motivated scripts/file_selection.py: selection
used to be by filesystem mtime, which git does not preserve, so a fresh clone
picked an arbitrary seed list.
"""

import os
import time
from datetime import date

import pytest

from scripts.file_selection import dated_files, latest_dated_file, parse_dated_name


# ---------------------------------------------------------------------------
# parse_dated_name
# ---------------------------------------------------------------------------

def test_parses_plain_dated_name(tmp_path):
    path = tmp_path / "tsn_list_20260701.csv"
    assert parse_dated_name(path, "tsn_list") == (date(2026, 7, 1), 0)


def test_parses_same_day_revision(tmp_path):
    path = tmp_path / "tsn_list_20260701_2.csv"
    assert parse_dated_name(path, "tsn_list") == (date(2026, 7, 1), 2)


@pytest.mark.parametrize(
    "name",
    [
        "tsn_list.csv",             # no date
        "tsn_list_202607.csv",      # too short
        "tsn_list_20261301.csv",    # month 13
        "tsn_list_20260701.txt",    # wrong suffix
        "other_20260701.csv",       # wrong prefix
    ],
)
def test_rejects_non_conforming_names(tmp_path, name):
    assert parse_dated_name(tmp_path / name, "tsn_list") is None


# ---------------------------------------------------------------------------
# ordering
# ---------------------------------------------------------------------------

def test_orders_by_filename_date_not_mtime(tmp_path):
    """The oldest-dated file is touched last; date must still win."""
    newest = tmp_path / "tsn_list_20260701.csv"
    middle = tmp_path / "tsn_list_20260218.csv"
    oldest = tmp_path / "tsn_list_20251205.csv"
    for path in (newest, middle, oldest):
        path.write_text("TSN,genus\n")

    # Make the oldest file the most recently modified.
    future = time.time() + 10_000
    os.utime(oldest, (future, future))

    assert latest_dated_file(tmp_path, "tsn_list") == newest
    assert dated_files(tmp_path, "tsn_list") == [oldest, middle, newest]


def test_same_day_revision_breaks_tie(tmp_path):
    """tsn_list_20260701_1.csv is newer than tsn_list_20260701.csv."""
    base = tmp_path / "tsn_list_20260701.csv"
    rev1 = tmp_path / "tsn_list_20260701_1.csv"
    rev2 = tmp_path / "tsn_list_20260701_2.csv"
    for path in (rev2, base, rev1):
        path.write_text("TSN,genus\n")

    assert latest_dated_file(tmp_path, "tsn_list") == rev2
    assert dated_files(tmp_path, "tsn_list") == [base, rev1, rev2]


def test_ignores_unrelated_files(tmp_path):
    (tmp_path / "tsn_list_20260701.csv").write_text("TSN,genus\n")
    (tmp_path / "notes.txt").write_text("scratch")
    (tmp_path / "tsn_list_backup.csv").write_text("TSN,genus\n")

    assert dated_files(tmp_path, "tsn_list") == [tmp_path / "tsn_list_20260701.csv"]


def test_selects_taxonomy_output_too(tmp_path):
    """The same helper serves data/output/taxonomy_YYYYMMDD.csv."""
    old = tmp_path / "taxonomy_20260218.csv"
    new = tmp_path / "taxonomy_20260701.csv"
    for path in (old, new):
        path.write_text("taxon,level\n")

    assert latest_dated_file(tmp_path, "taxonomy") == new


def test_raises_with_hint_when_directory_empty(tmp_path):
    with pytest.raises(FileNotFoundError, match="Run the full pipeline first"):
        latest_dated_file(tmp_path, "taxonomy", hint="Run the full pipeline first.")


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def test_scripts_are_runnable_directly():
    """`python scripts/add_tsns.py` must work, not just `import scripts.add_tsns`.

    Running a script directly puts scripts/ on sys.path rather than the project
    root, so a bare `from scripts.x import y` raises ModuleNotFoundError. The
    README documents direct invocation, so guard it.
    """
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/add_tsns.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
