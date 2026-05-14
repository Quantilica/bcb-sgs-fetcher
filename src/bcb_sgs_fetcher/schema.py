"""Data contract for BCB SGS time series observations.

Mirrors the :class:`SeriesPoint` dataclass returned by
:func:`fetch_series_data`. Decimal values are widened to ``Float64`` for
storage; full precision is preserved by the upstream API but Parquet
consumers (Polars, DuckDB, Pandas) interoperate more cleanly with
floats than with Decimal for time-series data.
"""

from __future__ import annotations

import polars as pl
from quantilica_io.schema import DataContract, Field

SGS_CONTRACT = DataContract(
    dataset_id="bcb-sgs",
    fields=[
        Field(name="series_id", dtype=pl.Int64),
        Field(name="date", dtype=pl.Date),
        Field(name="value", dtype=pl.Float64, required=False),
        Field(name="date_end", dtype=pl.Date, required=False),
    ],
    metadata={"source": "bcb", "system": "sgs"},
)
