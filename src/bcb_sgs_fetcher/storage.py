"""Optional filesystem helpers for caching scraped HTML/JSON.

This module is intentionally tiny and stateless. The caller passes the
target ``Path`` explicitly — there is no module-level ``DATA_DIR``.
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any

from quantilica_core.files import write_bytes_atomic, write_text_atomic


def get_data_dir(data_dir: Path, date: dt.date) -> Path:
    """Return the month-partitioned subdirectory for ``date``.

    Uses the ``bcb-sgs_YYYY-MM`` month-partitioned convention.
    """
    return data_dir / f"bcb-sgs_{date:%Y-%m}"


def save_json(data: Any, filepath: Path) -> None:
    """Write ``data`` as pretty-printed UTF-8 JSON atomically."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=1, default=str, ensure_ascii=False)
    write_text_atomic(filepath, text)


def save_bytes(data: bytes, filepath: Path) -> None:
    """Write raw bytes atomically."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(filepath, data)
