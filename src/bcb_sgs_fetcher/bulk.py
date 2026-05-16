"""Bulk orchestration helpers for batch-fetching from BCB SGS.

These functions implement the full metadata pipeline workflow so it
can be reproduced using ``bcb-sgs-fetcher`` alone, without a database.

Each :class:`~bcb_sgs_fetcher.scraper.ScraperClient` method already
carries :mod:`tenacity` retry decorators, so no additional retry
wrapper is needed here.
"""

import dataclasses
import time
from pathlib import Path

from bs4 import BeautifulSoup

from . import logger, storage
from .constants import BASIC, FULL
from .reader import arvore_grupos as ag_reader
from .reader import table_utils
from .reader.metadata import parse_metadata_basic, parse_metadata_full
from .scraper import ScraperClient


def fetch_arvore_grupos(
    scraper: ScraperClient,
    dest_dir: Path,
    sleeptime: float = 10,
) -> None:
    """Download the full group tree and paginated series listings.

    Saves to *dest_dir*:

    - ``GruposPrincipais.html`` — root group listing.
    - ``{id:04d}-{nome}.html`` — árvore of each top-level group.
    - ``{id:04d}-{nome}/{grupo_id:04d}-{nome}_{page:03d}.html`` —
      paginated series listings per sub-group.
    """
    html = scraper.get_grupos_principais()
    storage.save_bytes(html, dest_dir / "GruposPrincipais.html")

    soup = BeautifulSoup(html.decode("latin-1"), "lxml")
    table = soup.find("table")
    grupo_links = ag_reader.extract_arvore_grupos(table)

    for link in grupo_links:
        id_grupo = int(link.hd_oid_grupo_selecionado)
        seq_grupo = int(link.hd_seq_grupo_selecionado)
        nome = link.nome
        dest_file = dest_dir / f"{id_grupo:04d}-{nome}.html"
        if dest_file.exists():
            logger.debug("Skipping %s (already exists)", dest_file)
            continue
        logger.info("Fetching grupo: %s", nome)
        try:
            content = scraper.get_arvore_grupo(id_grupo, seq_grupo)
            storage.save_bytes(content, dest_file)
        except Exception as exc:
            logger.error("Failed to fetch grupo %s: %s", nome, exc)
            continue
        time.sleep(sleeptime)

    for file in sorted(dest_dir.glob("*-*.html")):
        try:
            soup = BeautifulSoup(
                file.read_text(encoding="latin-1"), "lxml"
            )
            subgroup_links = ag_reader.extract_grupo_links(soup)
        except Exception as exc:
            logger.error("Failed to parse %s: %s", file, exc)
            continue
        group_dest_dir = dest_dir / file.stem
        for gl in subgroup_links:
            grupo_id = int(gl.grupo_id)
            grupo_nome = (
                gl.grupo_nome.replace("/", " ").replace(":", "_")
            )
            _fetch_grupo_series_pages(
                scraper,
                grupo_id,
                grupo_nome,
                group_dest_dir,
                sleeptime,
            )


def _fetch_grupo_series_pages(
    scraper: ScraperClient,
    grupo_id: int,
    grupo_nome: str,
    dest_dir: Path,
    sleeptime: float,
) -> None:
    page = 1
    dest_file = (
        dest_dir / f"{grupo_id:04d}-{grupo_nome}_{page:03d}.html"
    )
    init_done = False

    if dest_file.exists():
        content = dest_file.read_bytes()
    else:
        try:
            content = scraper.get_grupo_series(grupo_id)
            storage.save_bytes(content, dest_file)
            time.sleep(sleeptime)
            init_done = True
        except Exception as exc:
            logger.error(
                "Failed to fetch series for grupo %s: %s",
                grupo_id,
                exc,
            )
            return

    soup = BeautifulSoup(content.decode("latin-1"), "lxml")
    n_pages = table_utils.get_n_pages(soup)
    if n_pages == 1:
        return

    for page in range(2, n_pages + 1):
        dest_file = (
            dest_dir / f"{grupo_id:04d}-{grupo_nome}_{page:03d}.html"
        )
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
        logger.info(
            "Fetching page %d/%d for grupo %s",
            page,
            n_pages,
            grupo_id,
        )
        try:
            content = scraper.change_page(page)
            storage.save_bytes(content, dest_file)
        except Exception as exc:
            logger.error(
                "Failed to fetch page %d for grupo %s: %s",
                page,
                grupo_id,
                exc,
            )
            continue
        time.sleep(sleeptime)


def fetch_series_desativadas(
    scraper: ScraperClient,
    dest_dir: Path,
    sleeptime: float = 10,
) -> None:
    """Download all deactivated-series pages (paginated).

    Saves ``dest_dir/series-desativadas_{page:03d}.html`` per page.
    """
    page = 1
    dest_file = dest_dir / f"series-desativadas_{page:03d}.html"
    content = scraper.get_series_desativadas()
    storage.save_bytes(content, dest_file)

    soup = BeautifulSoup(content.decode("latin-1"), "lxml")
    n_pages = table_utils.get_n_pages(soup)
    logger.info("Found %d pages of disabled series", n_pages)

    if n_pages == 1:
        return

    time.sleep(sleeptime)

    for page in range(2, n_pages + 1):
        dest_file = dest_dir / f"series-desativadas_{page:03d}.html"
        if dest_file.exists():
            logger.debug("Skipping page %d (already exists)", page)
            continue
        try:
            content = scraper.change_page(page)
            storage.save_bytes(content, dest_file)
            logger.info("Saved page %d/%d", page, n_pages)
        except Exception as exc:
            logger.error("Failed to fetch page %d: %s", page, exc)
            continue
        time.sleep(sleeptime)


def fetch_metadata_bulk(
    series_ids: list[int],
    scraper: ScraperClient,
    dest_dir: Path,
    sleeptime: float = 10,
    max_session_retries: int = 3,
) -> tuple[int, int]:
    """Download and parse metadata for a list of series IDs.

    Per series saves:

    - ``dest_dir/{id:06d}_basic.html`` and ``_full.html`` (raw HTML;
      skipped when both already exist).
    - ``dest_dir/{id:06d}.json`` (parsed combined metadata).

    On parse failure the HTML files are removed so the next run
    re-fetches them.  On session-level errors the session is renewed up
    to *max_session_retries* times before giving up on that series.

    Returns:
        ``(successful, failed)`` counts.
    """
    successful = 0
    failed = 0

    for series_id in sorted(series_ids):
        session_retry = 0
        while session_retry < max_session_retries:
            try:
                ok = _fetch_one_metadata(
                    series_id, scraper, dest_dir, sleeptime
                )
                if ok:
                    successful += 1
                else:
                    failed += 1
                break
            except Exception as exc:
                session_retry += 1
                logger.error(
                    "Session error for series %d: %s", series_id, exc
                )
                if session_retry < max_session_retries:
                    wait = 10 * session_retry
                    logger.warning(
                        "Renewing session, retrying series %d "
                        "(%d/%d) after %ds",
                        series_id,
                        session_retry,
                        max_session_retries,
                        wait,
                    )
                    if scraper.session is not None:
                        scraper.session.close()
                    time.sleep(wait)
                    scraper.init_session()
                else:
                    logger.error(
                        "Giving up on series %d after %d retries",
                        series_id,
                        max_session_retries,
                    )
                    failed += 1

    logger.info(
        "Completed: %d successful, %d failed", successful, failed
    )
    return successful, failed


def _fetch_one_metadata(
    series_id: int,
    scraper: ScraperClient,
    dest_dir: Path,
    sleeptime: float,
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
        logger.info("Fetching metadata for series %d", series_id)
        html = scraper.request_metadata_html(series_id)
        storage.save_bytes(html[BASIC], dest_basic)
        storage.save_bytes(html[FULL], dest_full)
        downloaded = True

    try:
        basic = parse_metadata_basic(html[BASIC].decode("latin-1"))
    except Exception as exc:
        logger.error(
            "Error parsing basic metadata for series %d: %s",
            series_id,
            exc,
        )
        dest_basic.unlink(missing_ok=True)
        dest_full.unlink(missing_ok=True)
        return False

    if basic.series_id != series_id:
        logger.warning(
            "Series ID mismatch for %d (got %d), removing files",
            series_id,
            basic.series_id,
        )
        dest_basic.unlink(missing_ok=True)
        dest_full.unlink(missing_ok=True)
        return False

    try:
        full = parse_metadata_full(html[FULL].decode("latin-1"))
    except Exception as exc:
        logger.error(
            "Error parsing full metadata for series %d: %s",
            series_id,
            exc,
        )
        return False

    metadata = {
        BASIC: dataclasses.asdict(basic),
        FULL: dataclasses.asdict(full),
    }
    storage.save_json(metadata, dest_dir / f"{series_id:06d}.json")

    if downloaded:
        time.sleep(sleeptime)

    return True
