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
    extract_table_data,
    logger,
    parse_metadata_basic,
    parse_metadata_full,
    storage,
)

_DEFAULT_OUTPUT = Path("/data/bcb-sgs")


def handle_fetch(args: argparse.Namespace) -> None:
    with SgsDataClient() as client:
        points = client.fetch_series_data(
            series_id=args.series_id,
            frequency_acronym=args.frequency,
        )
    if not points:
        logger.warning("Nenhum dado encontrado para série %s", args.series_id)
        return
    data = [dataclasses.asdict(p) for p in points]
    outfile = args.output / f"series_{args.series_id}.json"
    storage.save_json(data, outfile)
    logger.info("Salvo %d pontos em %s", len(points), outfile)


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

    # fetch
    fetch_parser = subparsers.add_parser(
        "fetch", help="Baixar dados de uma série temporal"
    )
    fetch_parser.add_argument(
        "series_id", type=int, help="ID da série no SGS/BCB"
    )
    fetch_parser.add_argument(
        "-f",
        "--frequency",
        default=None,
        help="Periodicidade (D, S, M, T, Qd, A)",
    )
    fetch_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Diretório de saída",
    )
    fetch_parser.set_defaults(func=handle_fetch)

    # metadata
    meta_parser = subparsers.add_parser(
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

    # search
    search_parser = subparsers.add_parser(
        "search", help="Buscar séries por texto"
    )
    search_parser.add_argument("text", help="Texto de busca")
    search_parser.set_defaults(func=handle_search)

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
