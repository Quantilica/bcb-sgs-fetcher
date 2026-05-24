"""Filesystem layout for BCB SGS artifacts.

Single source of truth for the on-disk layout shared by ``bcb-sgs-fetcher``
and ``bcb-sgs-sql``. Built on ``quantilica-core`` (atomic writes,
``stamp_filename``, ``StampedDataRepository``) to avoid duplicated code.

Layout under ``<root>``::

    data/series_{id}@{YYYYMMDDTHHMMSS}.json        # observation snapshots
    bcb-sgs_{YYYY-MM}/metadata/{id:06d}.json       # combined basic+full
    bcb-sgs_{YYYY-MM}/metadata/{id:06d}_basic.html # raw HTML (bulk)
    bcb-sgs_{YYYY-MM}/metadata/{id:06d}_full.html
    bcb-sgs_{YYYY-MM}/metadata/{id:06d}_basic.json # split (single-series cmd)
    bcb-sgs_{YYYY-MM}/metadata/{id:06d}_full.json

Observation snapshots are stamped with **datetime** precision, so
re-fetching a series on the same day never overwrites a prior snapshot;
:func:`latest_series_file` returns the newest one (older date-only stamps
remain valid and sort before same-day datetime stamps).
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any

from quantilica_core.files import write_bytes_atomic, write_text_atomic
from quantilica_core.storage import StampedDataRepository, stamp_filename

from .constants import BASIC, FULL

_DATA_DATASET = "data"
_METADATA_DIR = "metadata"


def get_data_dir(data_dir: Path, date: dt.date) -> Path:
    """Return the month-partitioned subdirectory for ``date``."""
    return data_dir / f"bcb-sgs_{date:%Y-%m}"


def _series_slug(series_id: int) -> str:
    return f"series_{int(series_id)}"


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


def data_file_path(
    output: Path,
    series_id: int,
    timestamp: dt.date | dt.datetime | None = None,
) -> Path:
    """Return the stamped path for a series snapshot.

    ``<output>/data/series_{id}@{YYYYMMDDTHHMMSS}.json``. Defaults to the
    current time; datetime precision means same-day re-fetches coexist
    instead of overwriting.
    """
    ts = timestamp if timestamp is not None else dt.datetime.now()
    name = stamp_filename(
        _series_slug(series_id), "json", ts, precision="datetime"
    )
    return output / _DATA_DATASET / name


def latest_series_file(output: Path, series_id: int) -> Path | None:
    """Return the newest snapshot for a series, or ``None``.

    Delegates the latest-stamp selection to ``StampedDataRepository`` and
    recognizes both date-only and datetime stamps.
    """
    repo = StampedDataRepository(output)
    return repo.get_latest_stamped_file(
        _DATA_DATASET, _series_slug(series_id), "json"
    )


def snapshot_exists_for_date(
    output: Path, series_id: int, date: dt.date
) -> bool:
    """Return True if any snapshot for the series exists for ``date``."""
    data_dir = output / _DATA_DATASET
    if not data_dir.exists():
        return False
    prefix = f"{_series_slug(series_id)}@{date:%Y%m%d}"
    return any(data_dir.glob(f"{prefix}*.json"))


def write_series_data(
    output: Path,
    series_id: int,
    rows: list[Any],
    timestamp: dt.date | dt.datetime | None = None,
) -> Path:
    """Write a list of observation dicts as a stamped snapshot."""
    path = data_file_path(output, series_id, timestamp)
    save_json(rows, path)
    return path


def read_series_data(filepath: Path) -> list[dict]:
    """Read an observations JSON file written by :func:`write_series_data`."""
    data = json.loads(Path(filepath).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Unexpected observations format in {filepath}")
    return data


# ---------------------------------------------------------------------------
# Generic writers
# ---------------------------------------------------------------------------


def save_json(data: Any, filepath: Path) -> None:
    """Write ``data`` as pretty-printed UTF-8 JSON atomically."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=1, default=str, ensure_ascii=False)
    write_text_atomic(filepath, text)


def save_bytes(data: bytes, filepath: Path) -> None:
    """Write raw bytes atomically."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(filepath, data)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def metadata_dir(output: Path, date: dt.date | None = None) -> Path:
    """Return the month-partitioned metadata directory for ``date``."""
    return get_data_dir(output, date or dt.date.today()) / _METADATA_DIR


def _find_metadata_file(output: Path, name: str) -> Path | None:
    """Find ``name`` under ``<output>/metadata`` or the latest month dir."""
    direct = output / _METADATA_DIR / name
    if direct.exists():
        return direct
    for meta_dir in sorted(
        output.glob(f"bcb-sgs_*/{_METADATA_DIR}"), reverse=True
    ):
        candidate = meta_dir / name
        if candidate.exists():
            return candidate
    return None


def find_combined_metadata(output: Path, series_id: int) -> Path | None:
    """Locate the combined ``{id:06d}.json`` (``{"basic", "full"}``)."""
    return _find_metadata_file(output, f"{int(series_id):06d}.json")


def read_combined_metadata(output: Path, series_id: int) -> dict | None:
    """Read the combined metadata file, or ``None`` when absent."""
    path = find_combined_metadata(output, series_id)
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_basic_metadata(output: Path, series_id: int) -> dict | None:
    """Read a split ``{id:06d}_basic.json`` file, or ``None``."""
    path = _find_metadata_file(output, f"{int(series_id):06d}_basic.json")
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_full_metadata(output: Path, series_id: int) -> dict | None:
    """Read a split ``{id:06d}_full.json`` file, or ``None``."""
    path = _find_metadata_file(output, f"{int(series_id):06d}_full.json")
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def has_basic_metadata(output: Path, series_id: int) -> bool:
    """Return True if a split basic-metadata file exists for the series."""
    return (
        _find_metadata_file(output, f"{int(series_id):06d}_basic.json")
        is not None
    )


def write_metadata(
    output: Path,
    series_id: int,
    *,
    basic: dict | None,
    full: dict | None,
    html_basic: bytes | None = None,
    html_full: bytes | None = None,
    date: dt.date | None = None,
) -> None:
    """Write metadata in the ``catalogo sync`` layout.

    A combined ``{id:06d}.json`` (``{"basic": ..., "full": ...}``) and,
    when supplied, the raw ``{id:06d}_basic.html`` / ``_full.html`` pair —
    under ``<output>/bcb-sgs_{YYYY-MM}/metadata/``.
    """
    md = metadata_dir(output, date)
    save_json({BASIC: basic, FULL: full}, md / f"{int(series_id):06d}.json")
    if html_basic is not None:
        save_bytes(html_basic, md / f"{int(series_id):06d}_basic.html")
    if html_full is not None:
        save_bytes(html_full, md / f"{int(series_id):06d}_full.html")
