"""Parquet writer for BCB SGS series points.

Provides an optional Parquet sink in addition to the JSON output offered
by :mod:`bcb_sgs_fetcher.storage`. The JSON path remains the default;
this writer is opt-in for callers that want a typed, columnar artifact
with manifest-backed provenance.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import polars as pl
from quantilica_core.manifests import DownloadManifest
from quantilica_io.writer import to_parquet

from .models import SeriesPoint
from .schema import SGS_CONTRACT


def points_to_dataframe(points: Iterable[SeriesPoint]) -> pl.DataFrame:
    """Build a Polars DataFrame from an iterable of ``SeriesPoint``.

    Decimal values are widened to ``float`` (see ``SGS_CONTRACT``).
    """
    rows = [
        {
            "series_id": p.series_id,
            "date": p.date,
            "value": float(p.value) if p.value is not None else None,
            "date_end": p.date_end,
        }
        for p in points
    ]
    df = pl.DataFrame(
        rows,
        schema={
            "series_id": pl.Int64,
            "date": pl.Date,
            "value": pl.Float64,
            "date_end": pl.Date,
        },
    )
    return SGS_CONTRACT.cast(df)


def save_parquet(
    points: Iterable[SeriesPoint],
    filepath: str | Path,
    *,
    manifest: DownloadManifest | None = None,
    compression: str = "zstd",
) -> Path:
    """Serialize series points to Parquet alongside the existing JSON path.

    Returns the written file path.
    """
    df = points_to_dataframe(points)
    return to_parquet(
        df,
        Path(filepath),
        manifest=manifest,
        compression=compression,
    )
