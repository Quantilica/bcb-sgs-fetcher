"""Tests for bulk data download and the frequency-map helper."""

import argparse
import datetime as dt
import json
import re
import time

import httpx2
import pytest

from bcb_sgs_fetcher import (
    SgsDataClient,
    bulk,
    storage,
)
from bcb_sgs_fetcher.bulk import (
    extract_ids_from_data_dir,
    extract_series_freq_map_from_data_dir,
    fetch_data_bulk,
)
from bcb_sgs_fetcher.cli import handle_fetch

_LISTING_TEMPLATE = """
<table id="tabelaSeries">
  <thead>
    <tr>
      <th>Sel.</th><th>Cód.</th><th>Nome completo</th><th>Unid.</th>
      <th>Per.</th><th>Início dd/MM/aaaa</th><th>Últ. valor</th>
      <th>Fonte</th><th>Esp.</th><th>Met.</th>
    </tr>
  </thead>
  {rows}
</table>
"""

# ``Últ. valor`` is "-" so date parsing is frequency-independent here.
_ROW_TEMPLATE = (
    "<tr><td>x</td><td>{sid}</td><td>Serie {sid}</td><td>u</td>"
    "<td>{freq}</td><td>01/01/2000</td><td>-</td>"
    "<td>BCB</td><td>N</td><td>met</td></tr>"
)


def _write_listing(path, rows):
    """Write a ``table#tabelaSeries`` listing page (latin-1)."""
    body = "".join(_ROW_TEMPLATE.format(sid=sid, freq=freq) for sid, freq in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    html = _LISTING_TEMPLATE.format(rows=body)
    path.write_text(html, encoding="latin-1")


def _build_data_dir(tmp_path):
    """Create a data dir with arvore-grupos + series-desativadas pages."""
    _write_listing(
        tmp_path / "arvore-grupos" / "grupo1" / "0001-grupo_001.html",
        [(1, "D"), (4189, "M")],
    )
    _write_listing(
        tmp_path / "series-desativadas" / "series-desativadas_001.html",
        [(99, "A")],
    )
    return tmp_path


def _handler_by_id(payload_for):
    """MockTransport handler dispatching on the series id in the URL."""
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        match = re.search(r"bcdata\.sgs\.(\d+)/dados", url)
        sid = int(match.group(1))
        calls.append(sid)
        return httpx2.Response(
            200,
            content=json.dumps(payload_for(sid, url)).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    return handler, calls


# --- frequency map / extract_ids ----------------------------------------


def test_extract_series_freq_map_from_data_dir(tmp_path):
    data_dir = _build_data_dir(tmp_path)
    freqs = extract_series_freq_map_from_data_dir(data_dir)
    assert freqs == {1: "D", 4189: "M", 99: "A"}


def test_extract_ids_still_returns_sorted_ids(tmp_path):
    data_dir = _build_data_dir(tmp_path)
    assert extract_ids_from_data_dir(data_dir) == [1, 99, 4189]


# --- build_series_freqs --------------------------------------------------


def test_build_series_freqs_single_id():
    assert bulk.build_series_freqs(
        series_id=7,
        ids_file=None,
        catalog_dir=None,
        frequency="D",
    ) == {7: "D"}


def test_build_series_freqs_ids_file(tmp_path):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("1\n2\n3\n")
    assert bulk.build_series_freqs(
        series_id=None,
        ids_file=ids_file,
        catalog_dir=None,
        frequency=None,
    ) == {1: None, 2: None, 3: None}


def test_build_series_freqs_defaults_to_all(tmp_path):
    data_dir = _build_data_dir(tmp_path)
    # No series_id and no ids_file -> all series from the catalog.
    assert bulk.build_series_freqs(
        series_id=None,
        ids_file=None,
        catalog_dir=data_dir,
        frequency=None,
    ) == {1: "D", 4189: "M", 99: "A"}


def test_build_series_freqs_all_with_override(tmp_path):
    data_dir = _build_data_dir(tmp_path)
    freqs = bulk.build_series_freqs(
        series_id=None,
        ids_file=None,
        catalog_dir=data_dir,
        frequency="M",
    )
    assert freqs == {1: "M", 4189: "M", 99: "M"}


# --- fetch_data_bulk -----------------------------------------------------


def test_fetch_data_bulk_writes_stamped_files(tmp_path):
    def payload_for(sid, url):
        return [{"data": "01/01/2024", "valor": "10.5"}]

    handler, _calls = _handler_by_id(payload_for)
    transport = httpx2.MockTransport(handler)

    with SgsDataClient(transport=transport) as client:
        ok, failed = fetch_data_bulk(
            {1: "M", 4189: "M"},
            client,
            tmp_path,
            workers=1,
            sleeptime=0,
        )

    assert (ok, failed) == (2, 0)
    for sid in (1, 4189):
        dest = storage.latest_series_file(tmp_path, sid)
        assert dest is not None
        records = json.loads(dest.read_text(encoding="utf-8"))
        assert records[0]["series_id"] == sid
        assert records[0]["date"] == "2024-01-01"


def test_fetch_data_bulk_skip_existing(tmp_path):
    def payload_for(sid, url):
        return [{"data": "01/01/2024", "valor": "1"}]

    handler, calls = _handler_by_id(payload_for)
    transport = httpx2.MockTransport(handler)
    today = dt.date.today()

    # Pre-create today's snapshot for series 1.
    storage.save_json([{"series_id": 1}], storage.data_file_path(tmp_path, 1, today))

    with SgsDataClient(transport=transport) as client:
        ok, failed = fetch_data_bulk(
            {1: "M"},
            client,
            tmp_path,
            skip_existing=True,
            workers=1,
            sleeptime=0,
        )

    assert (ok, failed) == (0, 0)
    assert calls == []  # no HTTP request was made


def test_fetch_data_bulk_empty_counts_as_skipped(tmp_path):
    seen: dict[str, int] = {}

    def on_progress(processed, total, ok, failed, skipped):
        seen.update(
            processed=processed,
            total=total,
            ok=ok,
            failed=failed,
            skipped=skipped,
        )

    handler, _calls = _handler_by_id(lambda sid, url: [])
    transport = httpx2.MockTransport(handler)

    with SgsDataClient(transport=transport) as client:
        ok, failed = fetch_data_bulk(
            {1: "M"},
            client,
            tmp_path,
            workers=1,
            sleeptime=0,
            on_progress=on_progress,
        )

    assert (ok, failed) == (0, 0)
    assert seen["skipped"] == 1
    assert storage.latest_series_file(tmp_path, 1) is None


def test_fetch_data_bulk_daily_uses_backfill(tmp_path):
    def payload_for(sid, url):
        if "/ultimos/20" in url:
            return [
                {"data": "05/03/2024", "valor": "5.5"},
                {"data": "04/03/2024", "valor": "5.4"},
            ]
        if "dataInicial=01/01/2023" in url:
            return [{"data": "15/06/2023", "valor": "5.0"}]
        return {"error": "no data"}  # not a list -> stops the loop

    handler, calls = _handler_by_id(payload_for)
    transport = httpx2.MockTransport(handler)

    with SgsDataClient(transport=transport) as client:
        ok, failed = fetch_data_bulk(
            {1: "D"},
            client,
            tmp_path,
            workers=1,
            sleeptime=0,
        )

    assert (ok, failed) == (1, 0)
    dest = storage.latest_series_file(tmp_path, 1)
    records = json.loads(dest.read_text(encoding="utf-8"))
    dates = {r["date"] for r in records}
    assert {"2024-03-05", "2024-03-04", "2023-06-15"} <= dates
    # /ultimos/20 anchor + at least one year-window request.
    assert len(calls) >= 2


def test_fetch_data_bulk_concurrent(tmp_path):
    def payload_for(sid, url):
        return [{"data": "01/01/2024", "valor": str(sid)}]

    handler, _calls = _handler_by_id(payload_for)
    transport = httpx2.MockTransport(handler)
    ids = {sid: "M" for sid in (1, 2, 3, 4, 5)}

    with SgsDataClient(transport=transport) as client:
        ok, failed = fetch_data_bulk(ids, client, tmp_path, workers=3, sleeptime=0)

    assert (ok, failed) == (5, 0)
    for sid in ids:
        assert storage.latest_series_file(tmp_path, sid) is not None


class _BoomClient:
    """Fake client that raises KeyboardInterrupt on its first call."""

    def __init__(self):
        self.calls: list[int] = []

    def fetch_series_data(
        self,
        series_id,
        period,
        frequency_acronym,
        should_stop,
        progress=None,
    ):
        self.calls.append(series_id)
        if len(self.calls) == 1:
            raise KeyboardInterrupt
        time.sleep(0.05)
        return []


def test_fetch_data_bulk_keyboardinterrupt_cancels(tmp_path):
    client = _BoomClient()
    ids = {sid: None for sid in range(1, 31)}
    with pytest.raises(KeyboardInterrupt):
        fetch_data_bulk(ids, client, tmp_path, workers=1, sleeptime=0)
    # Pending series were cancelled rather than all 30 being fetched.
    assert len(client.calls) < 30


# --- CLI validation ------------------------------------------------------


def _sync_args(**overrides):
    base = dict(
        series_id=None,
        ids_file=None,
        catalog_dir=overrides.pop("catalog_dir", None),
        frequency=None,
        period="all",
        skip_existing=False,
        workers=5,
        sleeptime=0.0,
        output=overrides.pop("output", None),
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_sync_rejects_series_id_and_ids_file_together(tmp_path):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("1\n")
    with pytest.raises(SystemExit):
        handle_fetch(_sync_args(output=tmp_path, series_id=1, ids_file=ids_file))


def test_sync_defaults_to_all_and_warns_when_empty(tmp_path):
    # No sources, empty catalog dir -> no error, just a warning + return.
    handle_fetch(_sync_args(output=tmp_path, catalog_dir=tmp_path))
