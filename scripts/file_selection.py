"""Deterministic selection of the newest dated file in a directory.

Filenames across this pipeline follow ``<prefix>_YYYYMMDD<suffix>``, with an
optional ``_N`` for same-day revisions (``tsn_list_20260701_1.csv``), as written
by :func:`taxa_pipeline.make_new_tsn_list_path`.

Selecting the "latest" of these by filesystem mtime is not reliable. Git does not
preserve modification times, so on a fresh clone every candidate is stamped at
checkout time and the winner is effectively arbitrary — a rebuild could silently
start from a seed list two revisions old. These helpers sort on the date encoded
in the filename instead, which is what the surrounding documentation has always
described.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple


def _dated_name_pattern(prefix: str, suffix: str) -> re.Pattern:
    """Compile the ``<prefix>_YYYYMMDD[_N]<suffix>`` filename pattern."""
    return re.compile(
        rf"^{re.escape(prefix)}_(\d{{8}})(?:_(\d+))?{re.escape(suffix)}$"
    )


def parse_dated_name(
    path: Path, prefix: str, suffix: str = ".csv"
) -> Optional[Tuple[date, int]]:
    """Return ``(date, revision)`` parsed from *path*'s filename.

    Returns ``None`` if the name does not match the convention or encodes an
    impossible date, so callers can skip unrelated files rather than crash.
    """
    match = _dated_name_pattern(prefix, suffix).match(path.name)
    if not match:
        return None

    stamp, revision = match.groups()
    try:
        parsed = date(int(stamp[0:4]), int(stamp[4:6]), int(stamp[6:8]))
    except ValueError:
        return None

    return parsed, int(revision) if revision is not None else 0


def dated_files(directory: Path, prefix: str, suffix: str = ".csv") -> List[Path]:
    """Return matching files in *directory*, sorted oldest to newest.

    Ordering is by the date in the filename, then by the ``_N`` revision, then by
    name — so the result is stable regardless of filesystem or clone order.
    """
    matches = []
    for path in Path(directory).glob(f"{prefix}_*{suffix}"):
        parsed = parse_dated_name(path, prefix, suffix)
        if parsed is not None:
            matches.append((parsed, path.name, path))

    matches.sort(key=lambda item: (item[0], item[1]))
    return [path for _, _, path in matches]


def latest_dated_file(
    directory: Path, prefix: str, suffix: str = ".csv", hint: str = ""
) -> Path:
    """Return the newest ``<prefix>_YYYYMMDD[_N]<suffix>`` file in *directory*.

    Raises:
        FileNotFoundError: if no file in *directory* matches the convention.
    """
    files = dated_files(directory, prefix, suffix)
    if not files:
        message = f"No {prefix}_YYYYMMDD{suffix} files found in {directory}."
        raise FileNotFoundError(f"{message} {hint}".strip())
    return files[-1]
