# Copyright (c) 2026 Komesu, D.K.
# Licensed under the MIT License.

"""Optional filesystem helpers for caching scraped HTML/JSON.

This module is intentionally tiny and stateless. The caller passes the
target ``Path`` explicitly — there is no module-level ``DATA_DIR``.
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any


def get_data_dir(data_dir: Path, date: dt.date) -> Path:
    """Return the month-partitioned subdirectory for ``date``.

    Follows the ``bcb-sgs_YYYY-MM`` convention already used by the
    upstream ``bcb-sgs-metadata-db`` package.
    """
    return data_dir / f"bcb-sgs_{date:%Y-%m}"


def save_json(data: Any, filepath: Path) -> None:
    """Write ``data`` as pretty-printed UTF-8 JSON, creating parents."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, default=str, ensure_ascii=False)


def save_bytes(data: bytes, filepath: Path) -> None:
    """Write raw bytes, creating parents."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(data)
