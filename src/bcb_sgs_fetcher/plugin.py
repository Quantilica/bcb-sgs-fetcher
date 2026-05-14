"""Typer plugin for quantilica-cli integration."""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path
from typing import Annotated

import typer
from quantilica_core.logging import configure_cli_logging

from bcb_sgs_fetcher import (
    ScraperClient,
    SgsDataClient,
    extract_table_data,
    parse_metadata_basic,
    parse_metadata_full,
    storage,
)

app = typer.Typer(help="Dados do SGS/BCB (séries temporais).")

_DEFAULT_OUTPUT = Path("/data/bcb-sgs")


@app.command("fetch")
def fetch(
    series_id: Annotated[
        int,
        typer.Argument(help="ID da série no SGS/BCB"),
    ],
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Diretório de saída"),
    ] = _DEFAULT_OUTPUT,
    frequency: Annotated[
        str | None,
        typer.Option(
            "-f",
            "--frequency",
            help="Periodicidade (D, S, M, T, Qd, A). D=retroativo.",
        ),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Logs detalhados")
    ] = False,
) -> None:
    """Baixar dados de uma série temporal do SGS/BCB."""
    configure_cli_logging(verbose=verbose)
    with SgsDataClient() as client:
        points = client.fetch_series_data(
            series_id=series_id,
            frequency_acronym=frequency,
        )
    if not points:
        typer.echo(f"Nenhum dado encontrado para série {series_id}.")
        return
    data = [dataclasses.asdict(p) for p in points]
    outfile = output / f"series_{series_id}.json"
    storage.save_json(data, outfile)
    typer.echo(f"Salvo {len(points)} pontos em {outfile}")


@app.command("metadata")
def metadata(
    series_id: Annotated[
        int,
        typer.Argument(help="ID da série no SGS/BCB"),
    ],
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Diretório de saída"),
    ] = _DEFAULT_OUTPUT,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Logs detalhados")
    ] = False,
) -> None:
    """Baixar metadados de uma série temporal do SGS/BCB."""
    configure_cli_logging(verbose=verbose)
    with ScraperClient() as scraper:
        htmls = scraper.request_metadata_html(series_id=series_id)
    basic = parse_metadata_basic(htmls["basic"])
    full = parse_metadata_full(htmls["full"])
    data_dir = storage.get_data_dir(output, dt.date.today())
    meta_dir = data_dir / "metadata"
    storage.save_json(
        dataclasses.asdict(basic),
        meta_dir / f"{series_id:06d}_basic.json",
    )
    storage.save_json(
        dataclasses.asdict(full),
        meta_dir / f"{series_id:06d}_full.json",
    )
    typer.echo(f"Metadados salvos em {meta_dir}")


@app.command("search")
def search(
    text: Annotated[str, typer.Argument(help="Texto de busca")],
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Logs detalhados")
    ] = False,
) -> None:
    """Buscar séries no SGS/BCB por texto."""
    from bs4 import BeautifulSoup

    configure_cli_logging(verbose=verbose)
    with ScraperClient() as scraper:
        html = scraper.search_series_by_text(text)
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        typer.echo("Nenhum resultado encontrado.")
        return
    rows = extract_table_data(table)
    if not rows:
        typer.echo("Nenhum resultado encontrado.")
        return
    for row in rows:
        typer.echo(f"{row.series_id:>6}  {row.series_name}")
