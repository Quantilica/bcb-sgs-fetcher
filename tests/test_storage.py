import datetime as dt
import json

from bcb_sgs_fetcher import storage


def test_data_file_path_uses_datetime_precision(tmp_path):
    ts = dt.datetime(2026, 1, 2, 13, 45, 30)
    path = storage.data_file_path(tmp_path, 433, ts)
    assert path.name == "series_433@20260102T134530.json"
    assert path.parent == tmp_path / "data"


def test_write_series_data_roundtrip(tmp_path):
    rows = [{"series_id": 1, "date": "2020-01-01", "value": "1.5", "date_end": None}]
    path = storage.write_series_data(tmp_path, 1, rows)
    assert storage.read_series_data(path) == rows


def test_same_day_refetch_does_not_overwrite(tmp_path):
    storage.write_series_data(
        tmp_path, 7, [{"a": 1}], dt.datetime(2026, 1, 1, 10, 0, 0)
    )
    storage.write_series_data(
        tmp_path, 7, [{"a": 2}], dt.datetime(2026, 1, 1, 11, 0, 0)
    )
    files = sorted((tmp_path / "data").glob("series_7@*.json"))
    assert [f.name for f in files] == [
        "series_7@20260101T100000.json",
        "series_7@20260101T110000.json",
    ]
    latest = storage.latest_series_file(tmp_path, 7)
    assert latest.name == "series_7@20260101T110000.json"


def test_latest_series_file_recognizes_legacy_date_stamp(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    # legacy date-only stamp (pre-migration) and a newer datetime stamp
    (data / "series_9@20260101.json").write_text("[]")
    (data / "series_9@20260201T120000.json").write_text("[]")
    latest = storage.latest_series_file(tmp_path, 9)
    assert latest.name == "series_9@20260201T120000.json"
    assert storage.latest_series_file(tmp_path, 404) is None


def test_latest_series_datetime_parses_datetime_stamp(tmp_path):
    storage.write_series_data(
        tmp_path, 7, [{"a": 1}], dt.datetime(2026, 1, 1, 10, 0, 0)
    )
    assert storage.latest_series_datetime(tmp_path, 7) == dt.datetime(
        2026, 1, 1, 10, 0, 0
    )


def test_latest_series_datetime_parses_legacy_date_stamp(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "series_9@20260201.json").write_text("[]")
    assert storage.latest_series_datetime(tmp_path, 9) == dt.datetime(
        2026, 2, 1, 0, 0, 0
    )


def test_latest_series_datetime_none_when_absent(tmp_path):
    assert storage.latest_series_datetime(tmp_path, 404) is None


def test_snapshot_exists_for_date(tmp_path):
    storage.write_series_data(
        tmp_path, 7, [{"a": 1}], dt.datetime(2026, 1, 1, 10, 0, 0)
    )
    assert storage.snapshot_exists_for_date(tmp_path, 7, dt.date(2026, 1, 1))
    assert not storage.snapshot_exists_for_date(tmp_path, 7, dt.date(2026, 1, 2))


def test_write_and_read_combined_metadata(tmp_path):
    assert storage.find_combined_metadata(tmp_path, 5) is None
    storage.write_metadata(
        tmp_path,
        5,
        basic={"series_id": 5, "name": "X"},
        full=None,
        html_basic=b"<basic/>",
        html_full=b"<full/>",
        date=dt.date(2026, 1, 1),
    )
    md = tmp_path / "bcb-sgs_2026-01" / "metadata"
    assert (md / "000005_basic.html").read_bytes() == b"<basic/>"
    assert (md / "000005_full.html").read_bytes() == b"<full/>"
    assert storage.read_combined_metadata(tmp_path, 5) == {
        "basic": {"series_id": 5, "name": "X"},
        "full": None,
    }


def test_split_metadata_readers(tmp_path):
    md = storage.metadata_dir(tmp_path, dt.date(2026, 1, 1))
    md.mkdir(parents=True)
    (md / "000008_basic.json").write_text(json.dumps({"series_id": 8}))
    (md / "000008_full.json").write_text(json.dumps({"last_update": "x"}))
    assert storage.has_basic_metadata(tmp_path, 8)
    assert storage.read_basic_metadata(tmp_path, 8) == {"series_id": 8}
    assert storage.read_full_metadata(tmp_path, 8) == {"last_update": "x"}
    assert not storage.has_basic_metadata(tmp_path, 999)
