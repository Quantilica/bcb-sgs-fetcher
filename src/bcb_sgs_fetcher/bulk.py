"""Bulk orchestration helpers for batch-fetching from BCB SGS.

These functions implement the full metadata pipeline workflow so it
can be reproduced using ``bcb-sgs-fetcher`` alone, without a database.

Each :class:`~bcb_sgs_fetcher.scraper.ScraperClient` method already
carries ``quantilica-core`` retry decorators, so no additional retry
wrapper is needed here.
"""

import dataclasses
import datetime as dt
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup

from . import logger, storage
from .constants import BASIC, FULL
from .data import Period, SgsDataClient
from .models import GrupoSeriesRow
from .reader import arvore_grupos as ag_reader
from .reader import table_utils
from .reader.metadata import parse_metadata_basic, parse_metadata_full
from .scraper import ScraperClient, looks_like_session_expired

_DIAGNOSTIC_SNIPPET_LIMIT = 300


def _diagnostic_snippet(content: bytes, limit: int = _DIAGNOSTIC_SNIPPET_LIMIT) -> str:
    """Return a whitespace-collapsed snippet of *content* for error logs."""
    text = content[:2048].decode("latin-1", errors="replace")
    return " ".join(text.split())[:limit]


def _parse_validated_html(
    content: bytes,
    label: str,
    *,
    require_table: bool = False,
) -> BeautifulSoup | None:
    """Parse downloaded HTML and validate it carries useful content.

    Validation covers the failure modes observed against the SGS portal:
    empty responses, session-expiry pages (after a JSESSIONID timeout)
    and pages without the expected series table.

    Args:
        content: The raw HTML bytes.
        label: Human-readable origin of the content, used in logs.
        require_table: When True, also require at least one ``<table>``.

    Returns:
        BeautifulSoup | None: The parsed page, or None when invalid —
        in which case a diagnostic error with the received content is
        logged.
    """
    if not content or not content.strip():
        logger.error(
            "Conteúdo vazio recebido de %s (0 bytes) — possível erro de sessão "
            "ou rede; veja logs acima.",
            label,
        )
        return None
    if looks_like_session_expired(content=content):
        logger.error(
            "Conteúdo de %s parece uma página de sessão expirada do SGS. "
            "Snippet recebido: %s",
            label,
            _diagnostic_snippet(content),
        )
        return None
    soup = BeautifulSoup(content.decode("latin-1", errors="replace"), "lxml")
    if require_table and soup.find("table") is None:
        logger.error(
            "Conteúdo de %s não contém a tabela esperada. Snippet recebido: %s",
            label,
            _diagnostic_snippet(content),
        )
        return None
    return soup


def fetch_arvore_grupos(
    scraper: ScraperClient,
    dest_dir: Path,
    sleeptime: float = 10,
    on_grupo: Callable[[str, int, int], None] | None = None,
    on_subgrupo_page: Callable[[str, int, int], None] | None = None,
) -> None:
    """Download the full group tree and paginated series listings.

    Saves to *dest_dir*:

    - ``GruposPrincipais.html`` — root group listing.
    - ``{id:04d}-{nome}.html`` — árvore of each top-level group.
    - ``{id:04d}-{nome}/{grupo_id:04d}-{nome}_{page:03d}.html`` —
      paginated series listings per sub-group.

    Args:
        scraper: The ScraperClient instance.
        dest_dir: Destination directory.
        sleeptime: Sleep time between requests.
        on_grupo: Callback for when a top-level group is processed.
        on_subgrupo_page: Callback for when a sub-group page is processed.
    """
    html = scraper.get_grupos_principais()
    soup = _parse_validated_html(html, "GruposPrincipais.html", require_table=True)
    if soup is None:
        raise RuntimeError(
            "Resposta inválida para GruposPrincipais (vazia ou sessão "
            "expirada) — catálogo não pode ser montado."
        )
    storage.save_bytes(html, dest_dir / "GruposPrincipais.html")

    table = soup.find("table")
    grupo_links = ag_reader.extract_arvore_grupos(table)
    total_grupos = len(grupo_links)

    for done, link in enumerate(grupo_links, 1):
        id_grupo = int(link.hd_oid_grupo_selecionado)
        seq_grupo = int(link.hd_seq_grupo_selecionado)
        nome = link.nome
        dest_file = dest_dir / f"{id_grupo:04d}-{nome}.html"
        if dest_file.exists():
            logger.debug("Skipping %s (already exists)", dest_file)
            if on_grupo is not None:
                on_grupo(nome, done, total_grupos)
            continue
        logger.debug("Fetching grupo: %s", nome)
        try:
            content = scraper.get_arvore_grupo(id_grupo, seq_grupo)
        except Exception as exc:
            logger.error(
                "Failed to fetch grupo %s (id=%s, seq=%s): %s",
                nome,
                id_grupo,
                seq_grupo,
                exc,
            )
            if on_grupo is not None:
                on_grupo(nome, done, total_grupos)
            continue
        if not _parse_validated_html(content, f"árvore do grupo {nome}"):
            logger.error(
                "Conteúdo inválido para a árvore do grupo %s (id=%s) — "
                "arquivo não foi salvo; execute 'catalogo sync' novamente "
                "para refetch.",
                nome,
                id_grupo,
            )
            if on_grupo is not None:
                on_grupo(nome, done, total_grupos)
            continue
        storage.save_bytes(content, dest_file)
        if on_grupo is not None:
            on_grupo(nome, done, total_grupos)
        time.sleep(sleeptime)

    for file in sorted(dest_dir.glob("*-*.html")):
        try:
            soup = BeautifulSoup(file.read_text(encoding="latin-1"), "lxml")
            subgroup_links = ag_reader.extract_grupo_links(soup)
        except Exception as exc:
            logger.error("Failed to parse %s: %s", file, exc)
            continue
        if not subgroup_links:
            logger.debug("Nenhum subgrupo encontrado em %s", file)
        group_dest_dir = dest_dir / file.stem
        for gl in subgroup_links:
            grupo_id = int(gl.grupo_id)
            grupo_nome = gl.grupo_nome.replace("/", " ").replace(":", "_")
            _fetch_grupo_series_pages(
                scraper,
                grupo_id,
                grupo_nome,
                group_dest_dir,
                sleeptime,
                on_subgrupo_page,
            )


def _fetch_grupo_series_pages(
    scraper: ScraperClient,
    grupo_id: int,
    grupo_nome: str,
    dest_dir: Path,
    sleeptime: float,
    on_page: Callable[[str, int, int], None] | None = None,
) -> None:
    page = 1
    dest_file = dest_dir / f"{grupo_id:04d}-{grupo_nome}_{page:03d}.html"
    init_done = False

    content: bytes = b""
    if dest_file.exists():
        content = dest_file.read_bytes()
        if (
            _parse_validated_html(content, f"{dest_file} (cache)", require_table=True)
            is None
        ):
            logger.error(
                "Arquivo em cache inválido %s — removendo para refetch.",
                dest_file,
            )
            dest_file.unlink(missing_ok=True)
            content = b""

    if not content:
        try:
            content = scraper.get_grupo_series(grupo_id)
        except Exception as exc:
            logger.error(
                "Failed to fetch series for grupo %s: %s",
                grupo_id,
                exc,
            )
            return
        if (
            _parse_validated_html(
                content, f"séries do grupo {grupo_id}", require_table=True
            )
            is None
        ):
            return
        storage.save_bytes(content, dest_file)
        time.sleep(sleeptime)
        init_done = True

    soup = BeautifulSoup(content.decode("latin-1"), "lxml")
    n_pages = table_utils.get_n_pages(soup)
    if on_page is not None:
        on_page(grupo_nome, 1, n_pages)

    if n_pages == 1:
        return

    for page in range(2, n_pages + 1):
        dest_file = dest_dir / f"{grupo_id:04d}-{grupo_nome}_{page:03d}.html"
        if dest_file.exists():
            continue
        if not init_done:
            try:
                scraper.get_grupo_series(grupo_id)
                init_done = True
                time.sleep(sleeptime)
            except Exception as exc:
                logger.error(
                    "Failed to init grupo %s for page %d: %s",
                    grupo_id,
                    page,
                    exc,
                )
                return
        logger.debug(
            "Fetching page %d/%d for grupo %s",
            page,
            n_pages,
            grupo_id,
        )
        try:
            content = scraper.change_page(page)
        except Exception as exc:
            logger.error(
                "Failed to fetch page %d for grupo %s: %s",
                page,
                grupo_id,
                exc,
            )
            continue
        if (
            _parse_validated_html(
                content,
                f"página {page} do grupo {grupo_id}",
                require_table=True,
            )
            is None
        ):
            continue
        storage.save_bytes(content, dest_file)
        if on_page is not None:
            on_page(grupo_nome, page, n_pages)
        time.sleep(sleeptime)


def fetch_series_desativadas(
    scraper: ScraperClient,
    dest_dir: Path,
    sleeptime: float = 10,
    on_page: Callable[[int, int], None] | None = None,
) -> None:
    """Download all deactivated-series pages (paginated).

    Saves ``dest_dir/series-desativadas_{page:03d}.html`` per page.

    Args:
        scraper: The ScraperClient instance.
        dest_dir: Destination directory.
        sleeptime: Sleep time between requests.
        on_page: Callback for when a page is processed.
    """
    page = 1
    dest_file = dest_dir / f"series-desativadas_{page:03d}.html"
    try:
        content = scraper.get_series_desativadas()
    except Exception as exc:
        raise RuntimeError(
            f"Falha ao baixar a primeira página de séries desativadas: {exc}"
        ) from exc
    if (
        _parse_validated_html(
            content, "series-desativadas página 1", require_table=True
        )
        is None
    ):
        raise RuntimeError(
            "Resposta inválida para séries desativadas (vazia, sessão "
            "expirada ou sem tabela de séries)."
        )
    storage.save_bytes(content, dest_file)

    soup = BeautifulSoup(content.decode("latin-1"), "lxml")
    n_pages = table_utils.get_n_pages(soup)
    logger.debug("Found %d pages of disabled series", n_pages)
    if on_page is not None:
        on_page(1, n_pages)

    if n_pages == 1:
        return

    time.sleep(sleeptime)

    for page in range(2, n_pages + 1):
        dest_file = dest_dir / f"series-desativadas_{page:03d}.html"
        if dest_file.exists():
            logger.debug("Skipping page %d (already exists)", page)
            if on_page is not None:
                on_page(page, n_pages)
            continue
        try:
            content = scraper.change_page(page)
        except Exception as exc:
            logger.error("Failed to fetch page %d: %s", page, exc)
            continue
        if (
            _parse_validated_html(
                content, f"series-desativadas página {page}", require_table=True
            )
            is None
        ):
            logger.error(
                "Página %d de séries desativadas inválida — arquivo não "
                "salvo; execute 'catalogo sync' novamente para refetch.",
                page,
            )
            continue
        storage.save_bytes(content, dest_file)
        logger.debug("Saved page %d/%d", page, n_pages)
        if on_page is not None:
            on_page(page, n_pages)
        time.sleep(sleeptime)


def fetch_metadata_bulk(
    series_ids: list[int],
    scraper: ScraperClient,
    dest_dir: Path,
    sleeptime: float = 10,
    max_session_retries: int = 3,
    skip_existing: bool = False,
    workers: int = 4,
    on_start: Callable[[int], None] | None = None,
    on_progress: (Callable[[int, int, int, int, int], None] | None) = None,
    on_finish: Callable[[int, str], None] | None = None,
    on_file_progress: Callable[[int, int, int], None] | None = None,
) -> tuple[int, int]:
    """Download and parse metadata for a list of series IDs.

    Per series saves:

    - ``dest_dir/{id:06d}_basic.html`` and ``_full.html`` (raw HTML;
      skipped when both already exist).
    - ``dest_dir/{id:06d}.json`` (parsed combined metadata).

    On parse failure the HTML files are removed so the next run
    re-fetches them.  On session-level errors the session is renewed up
    to *max_session_retries* times before giving up on that series.

    Args:
        series_ids: List of series IDs to fetch.
        scraper: The ScraperClient instance.
        dest_dir: Destination directory.
        sleeptime: Sleep time between requests.
        max_session_retries: Maximum number of session retries.
        skip_existing: Whether to skip existing metadata files.
        workers: Number of concurrent workers.
        on_start: Callback for when a series starts processing.
        on_progress: Callback for overall progress updates.
        on_finish: Callback for when a series finishes processing.
        on_file_progress: Callback for file download progress.

    Returns:
        tuple[int, int]: ``(successful, failed)`` counts.
    """

    total = len(series_ids)
    lock = threading.Lock()
    counters = {"processed": 0, "ok": 0, "failed": 0, "skipped": 0}
    stop = threading.Event()

    thread_local = threading.local()

    def get_scraper() -> ScraperClient:
        if not hasattr(thread_local, "client"):
            thread_local.client = ScraperClient(
                timeout=scraper.timeout,
                language=scraper.language,
                transport=scraper.transport,
            )
        return thread_local.client

    def _worker(series_id: int) -> None:
        if stop.is_set():
            return

        if on_start is not None:
            on_start(series_id)

        outcome = ""
        if skip_existing and (dest_dir / f"{series_id:06d}.json").exists():
            outcome = "skipped"
        else:
            worker_scraper = get_scraper()
            session_retry = 0
            while session_retry < max_session_retries:
                try:

                    def _prog(dl: int, tot: int) -> None:
                        if on_file_progress:
                            on_file_progress(series_id, dl, tot)

                    ok = _fetch_one_metadata(
                        series_id, worker_scraper, dest_dir, sleeptime, _prog
                    )
                    outcome = "ok" if ok else "failed"
                    break
                except Exception as exc:
                    session_retry += 1
                    logger.error("Session error for series %d: %s", series_id, exc)
                    if session_retry < max_session_retries:
                        wait = 10 * session_retry
                        logger.warning(
                            "Renewing session, retrying series %d (%d/%d) after %ds",
                            series_id,
                            session_retry,
                            max_session_retries,
                            wait,
                        )
                        worker_scraper.close()
                        time.sleep(wait)
                        worker_scraper.init_session()
                    else:
                        logger.error(
                            "Giving up on series %d after %d retries",
                            series_id,
                            max_session_retries,
                        )
                        outcome = "failed"

        with lock:
            counters[outcome] += 1
            counters["processed"] += 1
            if on_progress is not None:
                on_progress(
                    counters["processed"],
                    total,
                    counters["ok"],
                    counters["failed"],
                    counters["skipped"],
                )
            if on_finish is not None:
                on_finish(series_id, outcome)

    executor = ThreadPoolExecutor(max_workers=workers)
    futures = [executor.submit(_worker, series_id) for series_id in sorted(series_ids)]
    try:
        for future in as_completed(futures):
            future.result()
    except KeyboardInterrupt:
        logger.warning("Interrompido — cancelando downloads pendentes...")
        stop.set()
        for future in futures:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    logger.info(
        "Completed: %d successful, %d failed, %d skipped",
        counters["ok"],
        counters["failed"],
        counters["skipped"],
    )
    return counters["ok"], counters["failed"]


def _collect_freqs(html_file: Path, freqs: dict[int, str | None]) -> None:
    """Parse one listing page, recording ``series_id -> acronym``.

    Resilient by design: unreadable files, pages without the series
    table and individual malformed rows are logged and skipped instead
    of aborting the whole extraction.
    """
    try:
        content = html_file.read_bytes()
    except OSError as exc:
        logger.error("Não foi possível ler %s: %s", html_file, exc)
        return
    soup = _parse_validated_html(content, str(html_file))
    if soup is None:
        return
    table = soup.select_one("table#tabelaSeries")
    if table is None:
        logger.warning(
            "%s não contém table#tabelaSeries — nenhuma série extraída "
            "deste arquivo. Snippet: %s",
            html_file,
            _diagnostic_snippet(content),
        )
        return
    try:
        rows = table_utils.extract_table_data(table)
    except Exception as exc:
        logger.warning(
            "Falha ao interpretar %s (%s) — extraindo linha a linha",
            html_file,
            exc,
        )
        rows = _extract_rows_individually(table, html_file)
    for row in rows:
        freqs[row.series_id] = row.frequency_acronym


def _extract_rows_individually(table, html_file: Path) -> list[GrupoSeriesRow]:
    """Best-effort per-row extraction, skipping malformed rows."""
    rows_out: list[GrupoSeriesRow] = []
    for tr in table.find_all("tr"):
        tds = tr.select("td")
        # Coluna 1 = "Cód." (series id), coluna 4 = "Per." (frequência).
        if len(tds) < 5:
            continue
        try:
            sid = int(re.sub(r"\s+", " ", tds[1].text.strip()))
        except ValueError:
            continue
        freq = re.sub(r"\s+", " ", tds[4].text.strip()) or None
        rows_out.append(
            GrupoSeriesRow(
                series_id=sid,
                name_index=None,
                frequency_acronym=freq,
                unit=None,
                start_date=None,
                end_date=None,
                source=None,
                special=False,
                message=None,
            )
        )
    if not rows_out:
        logger.warning("Nenhuma linha válida em %s", html_file)
    return rows_out


def extract_series_freq_map_from_data_dir(
    data_dir: Path,
) -> dict[int, str | None]:
    """Map ``series_id -> frequency_acronym`` from listing HTML.

    Reads series listing pages inside ``arvore-grupos`` subdirectories
    and all ``series-desativadas`` pages. The frequency acronym
    (``"D"``/``"S"``/``"M"``/``"T"``/``"Qd"``/``"A"``) comes straight from
    each row, so daily series can later be fetched with the retroactive
    year-by-year strategy without any extra HTTP calls.

    Args:
        data_dir: The data directory containing HTML pages.

    Returns:
        dict[int, str | None]: A mapping of series_id to frequency acronym.
    """
    freqs: dict[int, str | None] = {}

    arvore_dir = data_dir / "arvore-grupos"
    if not arvore_dir.exists():
        logger.warning(
            "Diretório não encontrado: %s — rode 'catalogo sync' ou "
            "'catalogo arvore-grupos' antes de extrair IDs.",
            arvore_dir,
        )
    else:
        series_files = [
            f for f in sorted(arvore_dir.rglob("*.html")) if f.parent != arvore_dir
        ]
        logger.debug(
            "%d arquivo(s) de séries em %s",
            len(series_files),
            arvore_dir,
        )
        before = len(freqs)
        for html_file in series_files:
            _collect_freqs(html_file, freqs)
        if len(freqs) == before and series_files:
            logger.warning(
                "Nenhuma série extraída dos %d arquivo(s) em %s",
                len(series_files),
                arvore_dir,
            )

    desativ_dir = data_dir / "series-desativadas"
    if not desativ_dir.exists():
        logger.warning(
            "Diretório não encontrado: %s — rode 'catalogo sync' ou "
            "'catalogo series-desativadas' antes de extrair IDs.",
            desativ_dir,
        )
    else:
        desativ_files = sorted(desativ_dir.glob("series-desativadas_*.html"))
        logger.debug(
            "%d arquivo(s) de séries desativadas em %s",
            len(desativ_files),
            desativ_dir,
        )
        before = len(freqs)
        for html_file in desativ_files:
            _collect_freqs(html_file, freqs)
        if len(freqs) == before and desativ_files:
            logger.warning(
                "Nenhuma série extraída dos %d arquivo(s) em %s",
                len(desativ_files),
                desativ_dir,
            )

    return freqs


def extract_ids_from_data_dir(data_dir: Path) -> list[int]:
    """Extract all series IDs from downloaded HTML files in *data_dir*.

    Reads series listing pages inside ``arvore-grupos`` subdirectories
    and all ``series-desativadas`` pages. Returns a sorted list of
    unique IDs.

    This is the equivalent of ``sgs-process index`` in bcb-sgs-app:
    it bridges the gap between ``arvore-grupos`` / ``series-desativadas``
    downloads and ``metadata-bulk``.

    Args:
        data_dir: The data directory containing HTML pages.

    Returns:
        list[int]: A sorted list of unique series IDs.
    """
    freqs = extract_series_freq_map_from_data_dir(data_dir)
    if not freqs:
        arvore_dir = data_dir / "arvore-grupos"
        desativ_dir = data_dir / "series-desativadas"
        logger.error(
            "Nenhum ID extraído de %s (arvore-grupos: %s | series-desativadas: "
            "%s). Verifique se os passos anteriores baixaram páginas válidas e "
            "se não há erros de sessão expirada/conteúdo vazio nos logs acima.",
            data_dir,
            "OK" if arvore_dir.exists() else "ausente",
            "OK" if desativ_dir.exists() else "ausente",
        )
        return []
    return sorted(freqs)


def build_series_freqs(
    *,
    series_id: int | None,
    ids_file: Path | None,
    catalog_dir: Path,
    frequency: str | None,
) -> dict[int, str | None]:
    """Resolve the ``series_id -> frequency_acronym`` map for a sync run.

    Precedence: a single *series_id*, then an *ids_file* (one id per line),
    otherwise **all** series read from the listing HTML under *catalog_dir*
    (the default scope). *frequency*, when given, overrides the acronym for
    every series; otherwise daily series are detected per row.

    Args:
        series_id: Optional specific series ID.
        ids_file: Optional file containing series IDs.
        catalog_dir: The catalog directory containing HTML listings.
        frequency: Optional frequency override.

    Returns:
        dict[int, str | None]: A mapping of series_id to frequency acronym.
    """
    if series_id is not None:
        return {series_id: frequency}
    if ids_file is not None:
        ids = [
            int(line.strip())
            for line in ids_file.read_text().splitlines()
            if line.strip()
        ]
        return {sid: frequency for sid in ids}
    freqs = extract_series_freq_map_from_data_dir(catalog_dir)
    if frequency is not None:
        return {sid: frequency for sid in freqs}
    return freqs


def fetch_data_bulk(
    series_freqs: dict[int, str | None],
    client: SgsDataClient,
    output: Path,
    *,
    period: Period = "all",
    skip_existing: bool = False,
    workers: int = 5,
    sleeptime: float = 0.5,
    date: dt.date | None = None,
    on_start: Callable[[int], None] | None = None,
    on_progress: (Callable[[int, int, int, int, int], None] | None) = None,
    on_finish: Callable[[int, str], None] | None = None,
    on_file_progress: Callable[[int, int, int], None] | None = None,
) -> tuple[int, int]:
    """Fetch time-series data for many series concurrently.

    Per series writes ``output/data/series_{id}@YYYYMMDD.json`` (skipped
    when *skip_existing* and the snapshot for *date* already exists).
    Daily series (acronym ``"D"``) use the retroactive year-by-year
    strategy via :meth:`SgsDataClient.fetch_series_data`. The shared
    ``client`` is safe across threads because ``HttpClient`` opens a fresh
    connection per request and already retries 408/429/5xx.

    Empty results are counted as *skipped* (many series are legitimately
    empty) and no file is written.

    ``KeyboardInterrupt`` (Ctrl+C) cancels gracefully: pending series are
    dropped, in-flight ones (including daily backfills) stop at the next
    checkpoint, partial data is not persisted, and the exception is
    re-raised so the caller can report cancellation.

    Args:
        series_freqs: A mapping of series_id to frequency acronym.
        client: The SgsDataClient instance.
        output: Destination output directory.
        period: "all" or "latest".
        skip_existing: Whether to skip existing snapshot files.
        workers: Number of concurrent workers.
        sleeptime: Sleep time between requests.
        date: The target snapshot date.
        on_start: Callback for when a series starts processing.
        on_progress: Callback for overall progress updates.
        on_finish: Callback for when a series finishes processing.
        on_file_progress: Callback for file download progress.

    Returns:
        tuple[int, int]: ``(successful, failed)`` counts.
    """
    snapshot_date = date or dt.date.today()
    total = len(series_freqs)
    lock = threading.Lock()
    stop = threading.Event()
    counters = {"processed": 0, "ok": 0, "failed": 0, "skipped": 0}

    def _worker(series_id: int, freq: str | None) -> None:
        if stop.is_set():
            return
        if on_start is not None:
            on_start(series_id)
        if skip_existing and storage.snapshot_exists_for_date(
            output, series_id, snapshot_date
        ):
            outcome = "skipped"
        else:
            try:

                def _prog(dl: int, tot: int) -> None:
                    if on_file_progress:
                        on_file_progress(series_id, dl, tot)

                points = client.fetch_series_data(
                    series_id=series_id,
                    period=period,
                    frequency_acronym=freq,
                    should_stop=stop.is_set,
                    progress=_prog,
                )
                if stop.is_set():
                    return  # cancelled mid-fetch — don't persist partial
                if points:
                    storage.write_series_data(
                        output,
                        series_id,
                        [dataclasses.asdict(p) for p in points],
                    )
                    outcome = "ok"
                else:
                    logger.warning("Nenhum dado para série %d", series_id)
                    outcome = "skipped"
            except Exception as exc:
                logger.error("Falha ao baixar série %d: %s", series_id, exc)
                outcome = "failed"
            finally:
                if not stop.is_set():
                    time.sleep(sleeptime)
        with lock:
            counters[outcome] += 1
            counters["processed"] += 1
            if on_progress is not None:
                on_progress(
                    counters["processed"],
                    total,
                    counters["ok"],
                    counters["failed"],
                    counters["skipped"],
                )
            if on_finish is not None:
                on_finish(series_id, outcome)

    executor = ThreadPoolExecutor(max_workers=workers)
    futures = [
        executor.submit(_worker, series_id, freq)
        for series_id, freq in sorted(series_freqs.items())
    ]
    try:
        for future in as_completed(futures):
            future.result()
    except KeyboardInterrupt:
        logger.warning("Interrompido — cancelando downloads pendentes...")
        stop.set()
        for future in futures:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    logger.info(
        "Completed: %d successful, %d failed, %d skipped",
        counters["ok"],
        counters["failed"],
        counters["skipped"],
    )
    return counters["ok"], counters["failed"]


def _fetch_one_metadata(
    series_id: int,
    scraper: ScraperClient,
    dest_dir: Path,
    sleeptime: float,
    progress: Callable[[int, int], None] | None = None,
) -> bool:
    dest_basic = dest_dir / f"{series_id:06d}_basic.html"
    dest_full = dest_dir / f"{series_id:06d}_full.html"
    downloaded = False

    if dest_basic.exists() and dest_full.exists():
        html = {
            BASIC: dest_basic.read_bytes(),
            FULL: dest_full.read_bytes(),
        }
    else:
        logger.debug("Fetching metadata for series %d", series_id)
        html = scraper.request_metadata_html(series_id, progress=progress)
        storage.save_bytes(html[BASIC], dest_basic)
        storage.save_bytes(html[FULL], dest_full)
        downloaded = True

    try:
        basic = parse_metadata_basic(html[BASIC].decode("latin-1"))
    except ValueError as exc:
        logger.error(
            "Structural error parsing basic metadata for series %d "
            "(possible session error): %s",
            series_id,
            exc,
        )
        dest_basic.unlink(missing_ok=True)
        dest_full.unlink(missing_ok=True)
        raise
    except Exception as exc:
        logger.error(
            "Error parsing basic metadata for series %d: %s",
            series_id,
            exc,
        )
        dest_basic.unlink(missing_ok=True)
        dest_full.unlink(missing_ok=True)
        if downloaded:
            time.sleep(sleeptime)
        return False

    if basic.series_id != series_id:
        logger.warning(
            "Series ID mismatch for %d (got %d), removing files",
            series_id,
            basic.series_id,
        )
        dest_basic.unlink(missing_ok=True)
        dest_full.unlink(missing_ok=True)
        if downloaded:
            time.sleep(sleeptime)
        return False

    try:
        full = parse_metadata_full(html[FULL].decode("latin-1"))
    except ValueError as exc:
        logger.error(
            "Structural error parsing full metadata for series %d "
            "(possible session error): %s",
            series_id,
            exc,
        )
        dest_basic.unlink(missing_ok=True)
        dest_full.unlink(missing_ok=True)
        raise
    except Exception as exc:
        logger.error(
            "Error parsing full metadata for series %d: %s",
            series_id,
            exc,
        )
        if downloaded:
            time.sleep(sleeptime)
        return False

    metadata = {
        BASIC: dataclasses.asdict(basic),
        FULL: dataclasses.asdict(full),
    }
    storage.save_json(metadata, dest_dir / f"{series_id:06d}.json")

    if downloaded:
        time.sleep(sleeptime)

    return True
