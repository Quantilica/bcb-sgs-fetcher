"""Tests for bulk data download and the frequency-map helper."""

import argparse
import datetime as dt
import json
import re
import time

import httpx2
import pytest

from bcb_sgs_fetcher import (
    ScraperClient,
    SgsDataClient,
    bulk,
    storage,
)
from bcb_sgs_fetcher.bulk import (
    _fetch_grupo_series_pages,
    extract_ids_from_data_dir,
    extract_series_freq_map_from_data_dir,
    fetch_arvore_grupos,
    fetch_data_bulk,
    fetch_series_desativadas,
)
from bcb_sgs_fetcher.cli import handle_fetch
from bcb_sgs_fetcher.models import SeriesMetadataBasic, SeriesMetadataFull

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


# --- scraped-page validation / session-expiry resilience ------------------

_GRUPOS_PRINCIPAIS_PAGE = """
<html><body><table>
  <tr>
    <td><a href="javascript:parent.montarArvoreGrupos('',1,1,'')">Indicadores</a>
    grupo de indicadores</td>
  </tr>
</table></body></html>
""".encode("latin-1")


def _listing_page(rows, n_pages=1):
    """Build a valid listing page with ``table#tabelaSeries`` and pagination.

    Rows are direct children of the table (no ``<tbody>``), matching the
    structure expected by :func:`extract_table_data`.
    """
    row_html = "".join(
        f"<tr><td>x</td><td>{sid}</td><td>Serie {sid}</td><td>u</td>"
        f"<td>{freq}</td><td>01/01/2000</td><td>-</td><td>BCB</td><td>N</td>"
        f"<td>met</td></tr>"
        for sid, freq in rows
    )
    pagination = "".join(
        f'<a href="javascript:getPagina({p})">{p}</a>' for p in range(2, n_pages + 1)
    )
    table = (
        '<table id="tabelaSeries"><thead><tr><th>Sel.</th><th>C\xf3d.</th>'
        "<th>Nome completo</th><th>Unid.</th><th>Per.</th>"
        "<th>In\xedcio dd/MM/aaaa</th><th>\xdalt. valor</th><th>Fonte</th>"
        "<th>Esp.</th><th>Met.</th></tr></thead>"
        f"{row_html}</table>"
    )
    return (f"<html><body>{pagination}{table}</body></html>").encode("latin-1")


class _ExpiredScraper:
    """Scraper stub that always answers with a session-expired page."""

    def get_series_desativadas(self):
        return b"<html><body>Sess\xe3o expirada</body></html>"

    def change_page(self, page):
        raise AssertionError("change_page should not be called")


class _BadRootScraper:
    """Scraper stub whose GruposPrincipais response is a session page."""

    def get_grupos_principais(self):
        return b"<html><body>Sess\xe3o expirada</body></html>"


class _BadArvoreScraper:
    """Scraper stub with a valid root page but empty árvore responses."""

    def __init__(self):
        self.arvore_calls: list[tuple[int, int]] = []

    def get_grupos_principais(self):
        return _GRUPOS_PRINCIPAIS_PAGE

    def get_arvore_grupo(self, id_grupo, seq_grupo):
        self.arvore_calls.append((id_grupo, seq_grupo))
        return b""


class _TwoPageDesativadasScraper:
    """Scraper stub: valid page 1, invalid (expired) subsequent pages."""

    def __init__(self):
        self.pages_fetched: list[int] = []

    def get_series_desativadas(self):
        return _listing_page([(99, "A")], n_pages=2)

    def change_page(self, page):
        self.pages_fetched.append(page)
        return b"<html><body>sessao expirada</body></html>"


def test_fetch_series_desativadas_raises_on_expired_first_page(tmp_path):
    dest = tmp_path / "series-desativadas"
    with pytest.raises(RuntimeError, match="inválida|inv"):
        fetch_series_desativadas(_ExpiredScraper(), dest, sleeptime=0)
    assert list(dest.glob("*.html")) == []


def test_fetch_series_desativadas_skips_invalid_subsequent_page(tmp_path):
    dest = tmp_path / "series-desativadas"
    dest.mkdir()
    pages_seen: list[tuple[int, int]] = []
    scraper = _TwoPageDesativadasScraper()

    fetch_series_desativadas(
        scraper, dest, sleeptime=0, on_page=lambda p, n: pages_seen.append((p, n))
    )

    # Page 1 is saved; the invalid page 2 is diagnosed and not saved.
    assert (dest / "series-desativadas_001.html").exists()
    assert not (dest / "series-desativadas_002.html").exists()
    assert scraper.pages_fetched == [2]
    assert pages_seen == [(1, 2)]


def test_fetch_series_desativadas_happy_path(tmp_path):
    dest = tmp_path / "series-desativadas"

    class _OkScraper:
        def get_series_desativadas(self):
            return _listing_page([(99, "A")])

        def change_page(self, page):
            raise AssertionError("no pagination expected")

    fetch_series_desativadas(_OkScraper(), dest, sleeptime=0)
    assert (dest / "series-desativadas_001.html").exists()
    assert extract_ids_from_data_dir(tmp_path) == [99]


def test_fetch_arvore_grupos_raises_on_invalid_root_page(tmp_path):
    dest = tmp_path / "arvore-grupos"
    with pytest.raises(RuntimeError):
        fetch_arvore_grupos(_BadRootScraper(), dest, sleeptime=0)
    assert list(dest.glob("*.html")) == []


def test_fetch_arvore_grupos_skips_invalid_arvore_content(tmp_path):
    dest = tmp_path / "arvore-grupos"
    grupos: list[tuple[str, int, int]] = []
    scraper = _BadArvoreScraper()

    fetch_arvore_grupos(
        scraper, dest, sleeptime=0, on_grupo=lambda n, d, t: grupos.append((n, d, t))
    )

    # Root page is saved, but the empty árvore response is not persisted.
    assert (dest / "GruposPrincipais.html").exists()
    assert not (dest / "0001-Indicadores.html").exists()
    assert scraper.arvore_calls == [(1, 1)]
    assert grupos == [("Indicadores", 1, 1)]


def test_fetch_arvore_grupos_saves_valid_content(tmp_path):
    dest = tmp_path / "arvore-grupos"

    class _OkScraper:
        def get_grupos_principais(self):
            return _GRUPOS_PRINCIPAIS_PAGE

        def get_arvore_grupo(self, id_grupo, seq_grupo):
            return b"<html><body><p>arvore sem subgrupos</p></body></html>"

    fetch_arvore_grupos(_OkScraper(), dest, sleeptime=0)
    assert (dest / "GruposPrincipais.html").exists()
    assert (dest / "0001-Indicadores.html").exists()


def test_fetch_grupo_series_pages_refetches_invalid_cache(tmp_path):
    dest = tmp_path / "grupo1"
    dest.mkdir(parents=True)
    cached = dest / "0001-grupo_001.html"
    cached.write_bytes(b"<html><body>Sess\xe3o expirada</body></html>")
    calls: list[int] = []

    class _OkScraper:
        def get_grupo_series(self, grupo_id):
            calls.append(grupo_id)
            return _listing_page([(1, "D"), (2, "M")])

        def change_page(self, page):
            raise AssertionError("no pagination expected")

    _fetch_grupo_series_pages(_OkScraper(), 1, "grupo", dest, 0)

    # The poisoned cache file was replaced with a fresh valid download.
    assert calls == [1]
    assert b"tabelaSeries" in cached.read_bytes()


def test_extract_ids_resilient_to_malformed_rows(tmp_path):
    page = _listing_page([(7, "M")])
    # Append a row with a non-numeric id that breaks extract_table_data;
    # extraction must fall back to per-row parsing and keep the good row.
    malformed = (
        b"<tr><td>x</td><td>abc</td><td>Bad</td><td>u</td><td>M</td>"
        b"<td>xx</td><td>-</td><td>BCB</td><td>N</td><td>met</td></tr>"
    )
    page = page.replace(b"</table>", malformed + b"</table>")
    listing = tmp_path / "arvore-grupos" / "g" / "0001-g_001.html"
    listing.parent.mkdir(parents=True)
    listing.write_bytes(page)

    assert extract_ids_from_data_dir(tmp_path) == [7]


def test_extract_ids_returns_empty_for_missing_dirs(tmp_path):
    assert extract_ids_from_data_dir(tmp_path / "vazio") == []


def test_extract_ids_reports_missing_dirs(tmp_path, caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="bcb_sgs_fetcher")
    extract_ids_from_data_dir(tmp_path / "vazio")
    assert any("arvore-grupos" in r.message for r in caplog.records)


# --- metadata-bulk session renewal ----------------------------------------


def test_fetch_metadata_bulk_renews_session(tmp_path, monkeypatch):
    from quantilica.core import retry as core_retry

    # Skip real backoff sleeps everywhere (retry + session renewal waits).
    monkeypatch.setitem(
        core_retry.retry_call.__kwdefaults__, "sleep", lambda _delay: None
    )
    monkeypatch.setattr(bulk.time, "sleep", lambda _s: None)

    transport = httpx2.MockTransport(
        lambda r: httpx2.Response(200, content=b"<html>ok</html>")
    )
    calls = {"n": 0}
    original = ScraperClient.request_metadata_html

    def flaky(self, series_id, progress=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("session died")
        return original(self, series_id, progress=progress)

    monkeypatch.setattr(ScraperClient, "request_metadata_html", flaky)
    basic = SeriesMetadataBasic(series_id=1)
    full = SeriesMetadataFull()
    monkeypatch.setattr(bulk, "parse_metadata_basic", lambda html: basic)
    monkeypatch.setattr(bulk, "parse_metadata_full", lambda html: full)

    scraper = ScraperClient(transport=transport)
    try:
        ok, failed = bulk.fetch_metadata_bulk(
            [1],
            scraper,
            tmp_path,
            sleeptime=0,
            workers=1,
            max_session_retries=3,
        )
    finally:
        scraper.close()

    assert (ok, failed) == (1, 0)
    assert calls["n"] == 2  # first attempt failed, renewed, second succeeded
    assert (tmp_path / "000001.json").exists()
