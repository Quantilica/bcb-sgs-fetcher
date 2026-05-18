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
    bulk,
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


@app.command("arvore-grupos")
def arvore_grupos_cmd(
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Diretório de saída"),
    ] = _DEFAULT_OUTPUT,
    sleeptime: Annotated[
        float,
        typer.Option(
            "--sleeptime",
            help="Segundos de espera entre requisições",
        ),
    ] = 10.0,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Logs detalhados")
    ] = False,
) -> None:
    """Baixar a árvore de grupos e listas de séries do SGS/BCB."""
    configure_cli_logging(verbose=verbose)
    dest_dir = (
        storage.get_data_dir(output, dt.date.today())
        / "arvore-grupos"
    )
    with ScraperClient() as scraper:
        bulk.fetch_arvore_grupos(
            scraper, dest_dir, sleeptime=sleeptime
        )
    typer.echo(f"Árvore de grupos salva em {dest_dir}")


@app.command("series-desativadas")
def series_desativadas_cmd(
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Diretório de saída"),
    ] = _DEFAULT_OUTPUT,
    sleeptime: Annotated[
        float,
        typer.Option(
            "--sleeptime",
            help="Segundos de espera entre requisições",
        ),
    ] = 10.0,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Logs detalhados")
    ] = False,
) -> None:
    """Baixar todas as séries desativadas do SGS/BCB."""
    configure_cli_logging(verbose=verbose)
    dest_dir = (
        storage.get_data_dir(output, dt.date.today())
        / "series-desativadas"
    )
    with ScraperClient() as scraper:
        bulk.fetch_series_desativadas(
            scraper, dest_dir, sleeptime=sleeptime
        )
    typer.echo(f"Séries desativadas salvas em {dest_dir}")


@app.command("metadata-bulk")
def metadata_bulk_cmd(
    ids_file: Annotated[
        Path | None,
        typer.Option(
            "--ids-file",
            help="Arquivo com IDs de séries (um por linha)",
        ),
    ] = None,
    series_id: Annotated[
        list[int] | None,
        typer.Option(
            "--series-id",
            help="ID de série (repita para múltiplos)",
        ),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Diretório de saída"),
    ] = _DEFAULT_OUTPUT,
    sleeptime: Annotated[
        float,
        typer.Option(
            "--sleeptime",
            help="Segundos de espera entre requisições",
        ),
    ] = 10.0,
    skip_existing: Annotated[
        bool,
        typer.Option(
            "--skip-existing",
            help="Pular séries que já têm JSON de metadados no disco",
        ),
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Logs detalhados")
    ] = False,
) -> None:
    """Baixar metadados de múltiplas séries do SGS/BCB."""
    configure_cli_logging(verbose=verbose)

    ids: list[int] = list(series_id or [])
    if ids_file is not None:
        ids += [
            int(line.strip())
            for line in ids_file.read_text().splitlines()
            if line.strip()
        ]

    if not ids:
        typer.echo(
            "Erro: forneça --ids-file ou pelo menos um --series-id.",
            err=True,
        )
        raise typer.Exit(code=1)

    dest_dir = (
        storage.get_data_dir(output, dt.date.today()) / "metadata"
    )
    scraper = ScraperClient()
    try:
        successful, failed = bulk.fetch_metadata_bulk(
            ids,
            scraper,
            dest_dir,
            sleeptime=sleeptime,
            skip_existing=skip_existing,
        )
    finally:
        scraper.close()
    typer.echo(f"Completo: {successful} OK, {failed} falhas.")


@app.command("extract-ids")
def extract_ids_cmd(
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Diretório de dados"),
    ] = _DEFAULT_OUTPUT,
    ids_file: Annotated[
        Path | None,
        typer.Option(
            "--ids-file",
            help="Arquivo de saída (padrão: <output>/bcb-sgs_YYYY-MM/ids.txt)",
        ),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Logs detalhados")
    ] = False,
) -> None:
    """Extrair IDs de séries dos HTMLs baixados (arvore-grupos + series-desativadas)."""
    import datetime as dt

    configure_cli_logging(verbose=verbose)
    data_dir = storage.get_data_dir(output, dt.date.today())
    ids = bulk.extract_ids_from_data_dir(data_dir)
    if not ids:
        typer.echo(f"Nenhum ID encontrado em {data_dir}", err=True)
        raise typer.Exit(code=1)
    outfile = ids_file or (data_dir / "ids.txt")
    outfile.parent.mkdir(parents=True, exist_ok=True)
    outfile.write_text("\n".join(str(i) for i in ids) + "\n")
    typer.echo(f"Salvos {len(ids)} IDs únicos em {outfile}")


@app.command("pipeline")
def pipeline_cmd(
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Diretório de saída"),
    ] = _DEFAULT_OUTPUT,
    sleeptime: Annotated[
        float,
        typer.Option(
            "--sleeptime",
            help="Segundos de espera entre requisições (padrão: 10)",
        ),
    ] = 10.0,
    skip_existing: Annotated[
        bool,
        typer.Option(
            "--skip-existing",
            help="Pular séries que já têm JSON de metadados no disco",
        ),
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Logs detalhados")
    ] = False,
) -> None:
    """Executar o pipeline completo de metadados (arvore-grupos -> series-desativadas -> extract-ids -> metadata-bulk)."""
    import datetime as dt
    import sys

    configure_cli_logging(verbose=verbose)
    data_dir = storage.get_data_dir(output, dt.date.today())

    typer.echo("=== Passo 1/4: árvore de grupos ===")
    with ScraperClient() as scraper:
        try:
            bulk.fetch_arvore_grupos(
                scraper, data_dir / "arvore-grupos", sleeptime=sleeptime
            )
        except Exception as exc:
            typer.echo(f"Passo 1 encerrado com erro: {exc}", err=True)

    typer.echo("=== Passo 2/4: séries desativadas ===")
    with ScraperClient() as scraper:
        try:
            bulk.fetch_series_desativadas(
                scraper,
                data_dir / "series-desativadas",
                sleeptime=sleeptime,
            )
        except Exception as exc:
            typer.echo(f"Passo 2 encerrado com erro: {exc}", err=True)

    typer.echo("=== Passo 3/4: extração de IDs ===")
    ids = bulk.extract_ids_from_data_dir(data_dir)
    if not ids:
        typer.echo("Nenhum ID extraído — verifique os erros acima.", err=True)
        raise typer.Exit(code=1)
    ids_file = data_dir / "ids.txt"
    ids_file.parent.mkdir(parents=True, exist_ok=True)
    ids_file.write_text("\n".join(str(i) for i in ids) + "\n")
    typer.echo(f"{len(ids)} IDs únicos extraídos, salvos em {ids_file}")

    typer.echo("=== Passo 4/4: metadados ===")
    scraper = ScraperClient()
    try:
        successful, failed = bulk.fetch_metadata_bulk(
            ids,
            scraper,
            data_dir / "metadata",
            sleeptime=sleeptime,
            skip_existing=skip_existing,
        )
    finally:
        scraper.close()

    if failed:
        typer.echo(f"Pipeline concluído: {successful} OK, {failed} falha(s).")
    else:
        typer.echo(f"Pipeline concluído: {successful} séries processadas com sucesso.")


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
