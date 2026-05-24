#!/usr/bin/env python3

"""Command-line interface for fetching data from BCB SGS.

Provides access to time-series data and metadata from the Brazilian
Central Bank's Sistema Gerenciador de Séries Temporais.
"""

import argparse
import dataclasses
import datetime as dt
import sys
from pathlib import Path

from quantilica_core.logging import configure_cli_logging

from bcb_sgs_fetcher import (
    ScraperClient,
    SgsDataClient,
    __version__,
    bulk,
    extract_table_data,
    logger,
    parse_metadata_basic,
    parse_metadata_full,
    storage,
)

_DEFAULT_OUTPUT = Path("/data/bcb-sgs")


def handle_fetch(args: argparse.Namespace) -> None:
    if args.series_id is not None and args.ids_file is not None:
        logger.error("Use series_id OU --ids-file, não ambos.")
        sys.exit(1)

    catalog_dir = args.catalog_dir or storage.get_data_dir(
        args.output, dt.date.today()
    )
    series_freqs = bulk.build_series_freqs(
        series_id=args.series_id,
        ids_file=args.ids_file,
        catalog_dir=catalog_dir,
        frequency=args.frequency,
    )
    if not series_freqs:
        logger.warning(
            "Nenhuma série em %s. Rode 'catalogo sync' primeiro ou"
            " informe series_id/--ids-file.",
            catalog_dir,
        )
        return

    with SgsDataClient() as client:
        successful, failed = bulk.fetch_data_bulk(
            series_freqs,
            client,
            args.output,
            period=args.period,
            skip_existing=args.skip_existing,
            workers=args.workers,
            sleeptime=args.sleeptime,
        )
    if failed:
        logger.warning(
            "Completo: %d OK, %d falha(s)", successful, failed
        )
    else:
        logger.info("Completo: %d séries baixadas", successful)


def handle_metadata(args: argparse.Namespace) -> None:
    with ScraperClient() as scraper:
        htmls = scraper.request_metadata_html(series_id=args.series_id)
    basic = parse_metadata_basic(htmls["basic"])
    full = parse_metadata_full(htmls["full"])
    data_dir = storage.get_data_dir(args.output, dt.date.today())
    meta_dir = data_dir / "metadata"
    storage.save_json(
        dataclasses.asdict(basic),
        meta_dir / f"{args.series_id:06d}_basic.json",
    )
    storage.save_json(
        dataclasses.asdict(full),
        meta_dir / f"{args.series_id:06d}_full.json",
    )
    logger.info("Metadados salvos em %s", meta_dir)


def handle_arvore_grupos(args: argparse.Namespace) -> None:
    dest_dir = (
        storage.get_data_dir(args.output, dt.date.today())
        / "arvore-grupos"
    )
    with ScraperClient() as scraper:
        bulk.fetch_arvore_grupos(
            scraper, dest_dir, sleeptime=args.sleeptime
        )
    logger.info("Árvore de grupos salva em %s", dest_dir)


def handle_series_desativadas(args: argparse.Namespace) -> None:
    dest_dir = (
        storage.get_data_dir(args.output, dt.date.today())
        / "series-desativadas"
    )
    with ScraperClient() as scraper:
        bulk.fetch_series_desativadas(
            scraper, dest_dir, sleeptime=args.sleeptime
        )
    logger.info("Séries desativadas salvas em %s", dest_dir)


def handle_metadata_bulk(args: argparse.Namespace) -> None:
    ids: list[int] = list(args.series_id or [])
    if args.ids_file is not None:
        ids += [
            int(line.strip())
            for line in args.ids_file.read_text().splitlines()
            if line.strip()
        ]
    if not ids:
        logger.error(
            "Forneça --ids-file ou pelo menos um --series-id."
        )
        sys.exit(1)

    dest_dir = (
        storage.get_data_dir(args.output, dt.date.today()) / "metadata"
    )
    scraper = ScraperClient()
    try:
        successful, failed = bulk.fetch_metadata_bulk(
            ids,
            scraper,
            dest_dir,
            sleeptime=args.sleeptime,
            skip_existing=args.skip_existing,
        )
    finally:
        scraper.close()
    logger.info("Completo: %d OK, %d falhas", successful, failed)


def handle_extract_ids(args: argparse.Namespace) -> None:
    data_dir = storage.get_data_dir(args.output, dt.date.today())
    ids = bulk.extract_ids_from_data_dir(data_dir)
    if not ids:
        logger.warning("Nenhum ID encontrado em %s", data_dir)
        return
    outfile: Path = args.ids_file or (data_dir / "ids.txt")
    outfile.parent.mkdir(parents=True, exist_ok=True)
    outfile.write_text("\n".join(str(i) for i in ids) + "\n")
    logger.info("Salvos %d IDs únicos em %s", len(ids), outfile)


def handle_pipeline(args: argparse.Namespace) -> None:
    data_dir = storage.get_data_dir(args.output, dt.date.today())

    logger.info("=== Passo 1/4: árvore de grupos ===")
    with ScraperClient() as scraper:
        try:
            bulk.fetch_arvore_grupos(
                scraper,
                data_dir / "arvore-grupos",
                sleeptime=args.sleeptime,
            )
        except Exception as exc:
            logger.error("Passo 1 encerrado com erro: %s", exc)

    logger.info("=== Passo 2/4: séries desativadas ===")
    with ScraperClient() as scraper:
        try:
            bulk.fetch_series_desativadas(
                scraper,
                data_dir / "series-desativadas",
                sleeptime=args.sleeptime,
            )
        except Exception as exc:
            logger.error("Passo 2 encerrado com erro: %s", exc)

    logger.info("=== Passo 3/4: extração de IDs ===")
    ids = bulk.extract_ids_from_data_dir(data_dir)
    if not ids:
        logger.error(
            "Nenhum ID extraído — verifique os erros acima."
        )
        sys.exit(1)
    ids_file = data_dir / "ids.txt"
    ids_file.parent.mkdir(parents=True, exist_ok=True)
    ids_file.write_text("\n".join(str(i) for i in ids) + "\n")
    logger.info(
        "%d IDs únicos extraídos, salvos em %s", len(ids), ids_file
    )

    logger.info("=== Passo 4/4: metadados ===")
    scraper = ScraperClient()
    try:
        successful, failed = bulk.fetch_metadata_bulk(
            ids,
            scraper,
            data_dir / "metadata",
            sleeptime=args.sleeptime,
            skip_existing=args.skip_existing,
        )
    finally:
        scraper.close()

    if failed:
        logger.warning(
            "Pipeline concluído: %d OK, %d falha(s)", successful, failed
        )
    else:
        logger.info(
            "Pipeline concluído: %d séries processadas com sucesso",
            successful,
        )


def handle_search(args: argparse.Namespace) -> None:
    from bs4 import BeautifulSoup

    with ScraperClient() as scraper:
        html = scraper.search_series_by_text(args.text)
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        print("Nenhum resultado encontrado.")
        return
    rows = extract_table_data(table)
    if not rows:
        print("Nenhum resultado encontrado.")
        return
    for row in rows:
        print(f"{row.series_id:>6}  {row.series_name}")


def set_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bcb-sgs-fetcher",
        description="Baixar dados e metadados do SGS/BCB.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Logs detalhados em vez de saída mínima",
    )
    parser.set_defaults(func=lambda _: parser.print_help())

    subparsers = parser.add_subparsers(title="commands", dest="command")

    # === grupo: series ===
    series_parser = subparsers.add_parser(
        "series", help="Operações por série (dados, metadados, busca)"
    )
    series_sub = series_parser.add_subparsers(
        title="series", dest="subcommand", required=True
    )

    # series sync
    sync_parser = series_sub.add_parser(
        "sync",
        help="Baixar dados de todas as séries (padrão), de uma ou de"
        " uma lista",
    )
    sync_parser.add_argument(
        "series_id",
        type=int,
        nargs="?",
        default=None,
        help="ID de uma série (omita para baixar todas)",
    )
    sync_parser.add_argument(
        "-f",
        "--frequency",
        default=None,
        help=(
            "Periodicidade (D, S, M, T, Qd, A); aplicada a todas as"
            " séries quando informada"
        ),
    )
    sync_parser.add_argument(
        "--period",
        choices=["all", "latest"],
        default="all",
        help="Histórico completo (all) ou últimas 20 obs. (latest)",
    )
    sync_parser.add_argument(
        "--ids-file",
        type=Path,
        default=None,
        help="Baixar apenas os IDs deste arquivo (um por linha)",
    )
    sync_parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=None,
        help=(
            "Diretório com arvore-grupos/series-desativadas usado para"
            " enumerar todas as séries"
            " (padrão: <output>/bcb-sgs_YYYY-MM)"
        ),
    )
    sync_parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=False,
        help="Pular séries cujo snapshot do dia já existe",
    )
    sync_parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Downloads concorrentes (padrão: 5)",
    )
    sync_parser.add_argument(
        "--sleeptime",
        type=float,
        default=0.5,
        help="Pausa (s) por worker após cada série (padrão: 0.5)",
    )
    sync_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Diretório de saída",
    )
    sync_parser.set_defaults(func=handle_fetch)

    # series metadata
    meta_parser = series_sub.add_parser(
        "metadata", help="Baixar metadados de uma série"
    )
    meta_parser.add_argument(
        "series_id", type=int, help="ID da série no SGS/BCB"
    )
    meta_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Diretório de saída",
    )
    meta_parser.set_defaults(func=handle_metadata)

    # series search
    search_parser = series_sub.add_parser(
        "search", help="Buscar séries por texto"
    )
    search_parser.add_argument("text", help="Texto de busca")
    search_parser.set_defaults(func=handle_search)

    # === grupo: catalogo ===
    catalogo_parser = subparsers.add_parser(
        "catalogo", help="Catálogo de metadados do SGS/BCB"
    )
    catalogo_sub = catalogo_parser.add_subparsers(
        title="catalogo", dest="subcommand", required=True
    )

    # catalogo sync (pipeline completo)
    pipeline_parser = catalogo_sub.add_parser(
        "sync",
        help=(
            "Sincronizar o catálogo completo de metadados"
            " (arvore-grupos -> series-desativadas -> extract-ids"
            " -> metadata-bulk)"
        ),
    )
    pipeline_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Diretório de saída",
    )
    pipeline_parser.add_argument(
        "--sleeptime",
        type=float,
        default=10.0,
        help="Segundos de espera entre requisições (padrão: 10)",
    )
    pipeline_parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=False,
        help="Pular séries que já têm JSON de metadados no disco",
    )
    pipeline_parser.set_defaults(func=handle_pipeline)

    # catalogo arvore-grupos
    arvore_parser = catalogo_sub.add_parser(
        "arvore-grupos",
        help="Baixar árvore de grupos e listas de séries",
    )
    arvore_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Diretório de saída",
    )
    arvore_parser.add_argument(
        "--sleeptime",
        type=float,
        default=10.0,
        help="Segundos de espera entre requisições",
    )
    arvore_parser.set_defaults(func=handle_arvore_grupos)

    # catalogo series-desativadas
    desativ_parser = catalogo_sub.add_parser(
        "series-desativadas",
        help="Baixar todas as séries desativadas",
    )
    desativ_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Diretório de saída",
    )
    desativ_parser.add_argument(
        "--sleeptime",
        type=float,
        default=10.0,
        help="Segundos de espera entre requisições",
    )
    desativ_parser.set_defaults(func=handle_series_desativadas)

    # catalogo extract-ids
    extract_ids_parser = catalogo_sub.add_parser(
        "extract-ids",
        help=(
            "Extrair IDs de séries dos HTMLs baixados"
            " (arvore-grupos + series-desativadas)"
        ),
    )
    extract_ids_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Diretório de dados (arvore-grupos e series-desativadas)",
    )
    extract_ids_parser.add_argument(
        "--ids-file",
        type=Path,
        default=None,
        help=(
            "Arquivo de saída com os IDs, um por linha"
            " (padrão: <output>/bcb-sgs_YYYY-MM/ids.txt)"
        ),
    )
    extract_ids_parser.set_defaults(func=handle_extract_ids)

    # catalogo metadata-bulk
    meta_bulk_parser = catalogo_sub.add_parser(
        "metadata-bulk",
        help="Baixar metadados de múltiplas séries",
    )
    meta_bulk_parser.add_argument(
        "--ids-file",
        type=Path,
        default=None,
        help="Arquivo com IDs de séries (um por linha)",
    )
    meta_bulk_parser.add_argument(
        "--series-id",
        nargs="+",
        type=int,
        default=None,
        help="IDs de séries explícitos",
    )
    meta_bulk_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Diretório de saída",
    )
    meta_bulk_parser.add_argument(
        "--sleeptime",
        type=float,
        default=10.0,
        help="Segundos de espera entre requisições",
    )
    meta_bulk_parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=False,
        help="Pular séries que já têm JSON de metadados no disco",
    )
    meta_bulk_parser.set_defaults(func=handle_metadata_bulk)

    return parser


def main() -> None:
    parser = set_parser()
    args = parser.parse_args()
    configure_cli_logging(verbose=args.verbose)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nOperação cancelada.")
        sys.exit(1)
    except Exception as e:
        logger.error("Erro: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
