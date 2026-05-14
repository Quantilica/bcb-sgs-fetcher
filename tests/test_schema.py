"""Schema-regression tests for SGS_CONTRACT and points_to_dataframe()."""

import datetime as dt
from decimal import Decimal

import polars as pl
import pytest

from bcb_sgs_fetcher import (
    SGS_CONTRACT,
    SeriesPoint,
    points_to_dataframe,
)


def _make_points() -> list[SeriesPoint]:
    return [
        SeriesPoint(
            series_id=11, date=dt.date(2020, 1, 1), value=Decimal("4.5")
        ),
        SeriesPoint(series_id=11, date=dt.date(2020, 1, 2), value=None),
    ]


class TestSgsContractValidate:
    def test_accepts_dataframe_from_points(self):
        df = points_to_dataframe(_make_points())
        SGS_CONTRACT.validate(df)

    def test_rejects_missing_required_field(self):
        df = points_to_dataframe(_make_points()).drop("series_id")
        with pytest.raises(ValueError, match="series_id"):
            SGS_CONTRACT.validate(df)

    def test_rejects_wrong_dtype_on_date(self):
        df = points_to_dataframe(_make_points()).with_columns(
            pl.col("date").cast(pl.Utf8)
        )
        with pytest.raises(TypeError, match="date"):
            SGS_CONTRACT.validate(df)

    def test_value_is_optional(self):
        df = points_to_dataframe(_make_points()).drop("value")
        SGS_CONTRACT.validate(df)

    def test_date_end_is_optional(self):
        df = points_to_dataframe(_make_points()).drop("date_end")
        SGS_CONTRACT.validate(df)

    def test_contract_dataset_id(self):
        assert SGS_CONTRACT.dataset_id == "bcb-sgs"


class TestPointsToDataframe:
    def test_decimal_widened_to_float(self):
        df = points_to_dataframe(_make_points())
        assert df.schema["value"] == pl.Float64
        assert df["value"][0] == pytest.approx(4.5)

    def test_none_preserved_as_null(self):
        df = points_to_dataframe(_make_points())
        assert df["value"][1] is None

    def test_date_end_optional(self):
        points = [
            SeriesPoint(
                series_id=433,
                date=dt.date(2020, 1, 1),
                value=Decimal("0.71"),
                date_end=dt.date(2020, 1, 31),
            ),
        ]
        df = points_to_dataframe(points)
        assert df["date_end"][0] == dt.date(2020, 1, 31)
        assert df.schema["date_end"] == pl.Date

    def test_empty_input(self):
        df = points_to_dataframe([])
        assert len(df) == 0
        # contract still applies
        SGS_CONTRACT.validate(df)
