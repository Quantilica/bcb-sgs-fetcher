"""Typer plugin for quantilica-cli integration."""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.rule import Rule
from rich.table import Table

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
console = Console()

_DEFAULT_OUTPUT = Path("/data/bcb-sgs")


def _setup_logging(verbose: bool) -> None:
    """Configure logging via RichHandler to avoid breaking progress bars.

    verbose=False → WARNING only (errors/warnings surface, no INFO noise).
    verbose=True  → DEBUG through Rich console (properly interleaved).
    """
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, show_path=False)],
        force=True,
    )


def _make_progress(*extra_cols: object) -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        *extra_cols,
        console=console,
    )


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
    _setup_logging(verbose)
    with console.status(
        f"[cyan]Baixando série {series_id}...[/cyan]"
    ):
        with SgsDataClient() as client:
            points = client.fetch_series_data(
                series_id=series_id,
                frequency_acronym=frequency,
            )
    if not points:
        console.print(
            f"[yellow]Nenhum dado encontrado para série {series_id}.[/yellow]"
        )
        return
    data = [dataclasses.asdict(p) for p in points]
    outfile = output / f"series_{series_id}.json"
    storage.save_json(data, outfile)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Início", style="cyan")
    table.add_column("Fim", style="cyan")
    table.add_column("Pontos", style="bold green", justify="right")
    table.add_row(
        str(points[0].date),
        str(points[-1].date),
        str(len(points)),
    )
    console.print(table)
    console.print(
        f"[green]✓[/green] Salvo em [dim]{outfile}[/dim]"
    )


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
    _setup_logging(verbose)
    with console.status(
        f"[cyan]Baixando metadados da série {series_id}...[/cyan]"
    ):
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

    lines = []
    if basic.name:
        lines.append(f"[bold]{basic.name}[/bold]")
    if basic.frequency:
        lines.append(f"Periodicidade: [cyan]{basic.frequency}[/cyan]")
    if basic.unit:
        lines.append(f"Unidade: [cyan]{basic.unit}[/cyan]")
    if basic.start_date or basic.end_date:
        lines.append(
            f"Período: [dim]{basic.start_date} → {basic.end_date}[/dim]"
        )
    if basic.source:
        lines.append(f"Fonte: [dim]{basic.source}[/dim]")
    if lines:
        console.print(
            Panel("\n".join(lines), title=f"Série {series_id}")
        )
    console.print(
        f"[green]✓[/green] Metadados salvos em [dim]{meta_dir}[/dim]"
    )


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
    _setup_logging(verbose)
    dest_dir = (
        storage.get_data_dir(output, dt.date.today())
        / "arvore-grupos"
    )
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            "[cyan]Iniciando...[/cyan]", total=None
        )

        def on_grupo(nome: str, done: int, total: int) -> None:
            progress.update(
                task,
                completed=done,
                total=total,
                description=f"[cyan]{nome[:40]}[/cyan]",
            )

        with ScraperClient() as scraper:
            bulk.fetch_arvore_grupos(
                scraper, dest_dir, sleeptime=sleeptime, on_grupo=on_grupo
            )

    console.print(
        f"[green]✓[/green] Árvore de grupos salva em [dim]{dest_dir}[/dim]"
    )


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
    _setup_logging(verbose)
    dest_dir = (
        storage.get_data_dir(output, dt.date.today())
        / "series-desativadas"
    )
    with _make_progress() as progress:
        task = progress.add_task(
            "[cyan]Séries desativadas[/cyan]", total=None
        )

        def on_page(page: int, n_pages: int) -> None:
            progress.update(task, completed=page, total=n_pages)

        with ScraperClient() as scraper:
            bulk.fetch_series_desativadas(
                scraper,
                dest_dir,
                sleeptime=sleeptime,
                on_page=on_page,
            )

    console.print(
        f"[green]✓[/green] Séries desativadas salvas em"
        f" [dim]{dest_dir}[/dim]"
    )


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
    _setup_logging(verbose)

    ids: list[int] = list(series_id or [])
    if ids_file is not None:
        ids += [
            int(line.strip())
            for line in ids_file.read_text().splitlines()
            if line.strip()
        ]

    if not ids:
        console.print(
            "[red]Erro:[/red] forneça --ids-file ou pelo menos um"
            " --series-id.",
        )
        raise typer.Exit(code=1)

    dest_dir = (
        storage.get_data_dir(output, dt.date.today()) / "metadata"
    )
    with _make_progress() as progress:
        task = progress.add_task(
            "[cyan]0✓  0✗  0 skip[/cyan]", total=len(ids)
        )

        def on_progress(
            processed: int,
            total: int,
            ok: int,
            failed: int,
            skipped: int,
        ) -> None:
            progress.update(
                task,
                completed=processed,
                description=(
                    f"[green]{ok}✓[/green]"
                    f"  [red]{failed}✗[/red]"
                    f"  [dim]{skipped} skip[/dim]"
                ),
            )

        scraper = ScraperClient()
        try:
            successful, failed_count = bulk.fetch_metadata_bulk(
                ids,
                scraper,
                dest_dir,
                sleeptime=sleeptime,
                skip_existing=skip_existing,
                on_progress=on_progress,
            )
        finally:
            scraper.close()

    if failed_count:
        console.print(
            f"[yellow]⚠[/yellow]  {successful} OK"
            f" · [red]{failed_count} falha(s)[/red]"
        )
    else:
        console.print(
            f"[green]✓[/green]  [bold]{successful}[/bold]"
            " séries baixadas com sucesso."
        )


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
    """Extrair IDs dos HTMLs baixados (arvore-grupos + series-desativadas)."""
    _setup_logging(verbose)
    data_dir = storage.get_data_dir(output, dt.date.today())
    with console.status("[cyan]Extraindo IDs dos HTMLs...[/cyan]"):
        ids = bulk.extract_ids_from_data_dir(data_dir)
    if not ids:
        console.print(
            f"[red]Erro:[/red] Nenhum ID encontrado em {data_dir}"
        )
        raise typer.Exit(code=1)
    outfile = ids_file or (data_dir / "ids.txt")
    outfile.parent.mkdir(parents=True, exist_ok=True)
    outfile.write_text("\n".join(str(i) for i in ids) + "\n")
    console.print(
        f"[green]✓[/green] [bold]{len(ids)}[/bold] IDs únicos"
        f" → [dim]{outfile}[/dim]"
    )


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
    """Pipeline completo de metadados do SGS/BCB (4 passos)."""
    _setup_logging(verbose)
    data_dir = storage.get_data_dir(output, dt.date.today())

    results: dict[str, str] = {}

    # --- Passo 1 ---
    console.print(Rule("[bold]Passo 1/4: Árvore de grupos[/bold]"))
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Iniciando...[/cyan]", total=None)

        def on_grupo(nome: str, done: int, total: int) -> None:
            progress.update(
                task,
                completed=done,
                total=total,
                description=f"[cyan]{nome[:40]}[/cyan]",
            )

        with ScraperClient() as scraper:
            try:
                bulk.fetch_arvore_grupos(
                    scraper,
                    data_dir / "arvore-grupos",
                    sleeptime=sleeptime,
                    on_grupo=on_grupo,
                )
                results["Árvore de grupos"] = "[green]✓[/green]"
            except Exception as exc:
                results["Árvore de grupos"] = f"[red]✗ {exc}[/red]"

    # --- Passo 2 ---
    console.print(
        Rule("[bold]Passo 2/4: Séries desativadas[/bold]")
    )
    with _make_progress() as progress:
        task = progress.add_task(
            "[cyan]Séries desativadas[/cyan]", total=None
        )

        def on_page(page: int, n_pages: int) -> None:
            progress.update(task, completed=page, total=n_pages)

        with ScraperClient() as scraper:
            try:
                bulk.fetch_series_desativadas(
                    scraper,
                    data_dir / "series-desativadas",
                    sleeptime=sleeptime,
                    on_page=on_page,
                )
                results["Séries desativadas"] = "[green]✓[/green]"
            except Exception as exc:
                results["Séries desativadas"] = f"[red]✗ {exc}[/red]"

    # --- Passo 3 ---
    console.print(Rule("[bold]Passo 3/4: Extração de IDs[/bold]"))
    with console.status("[cyan]Extraindo IDs...[/cyan]"):
        ids = bulk.extract_ids_from_data_dir(data_dir)
    if not ids:
        console.print(
            "[red]Erro:[/red] Nenhum ID extraído — verifique erros acima."
        )
        raise typer.Exit(code=1)
    ids_file = data_dir / "ids.txt"
    ids_file.parent.mkdir(parents=True, exist_ok=True)
    ids_file.write_text("\n".join(str(i) for i in ids) + "\n")
    results["Extração de IDs"] = f"[bold]{len(ids)}[/bold] IDs"
    console.print(
        f"[green]✓[/green] [bold]{len(ids)}[/bold] IDs únicos"
        f" salvos em [dim]{ids_file}[/dim]"
    )

    # --- Passo 4 ---
    console.print(Rule("[bold]Passo 4/4: Metadados[/bold]"))
    with _make_progress() as progress:
        task = progress.add_task(
            "[cyan]0✓  0✗  0 skip[/cyan]", total=len(ids)
        )

        def on_progress(
            processed: int,
            total: int,
            ok: int,
            failed: int,
            skipped: int,
        ) -> None:
            progress.update(
                task,
                completed=processed,
                description=(
                    f"[green]{ok}✓[/green]"
                    f"  [red]{failed}✗[/red]"
                    f"  [dim]{skipped} skip[/dim]"
                ),
            )

        scraper = ScraperClient()
        try:
            successful, failed_count = bulk.fetch_metadata_bulk(
                ids,
                scraper,
                data_dir / "metadata",
                sleeptime=sleeptime,
                skip_existing=skip_existing,
                on_progress=on_progress,
            )
        finally:
            scraper.close()

    if failed_count:
        results["Metadados"] = (
            f"[green]{successful}✓[/green]"
            f"  [red]{failed_count}✗[/red]"
        )
    else:
        results["Metadados"] = f"[green]{successful}✓[/green]"

    # --- Resumo final ---
    console.print(Rule("[bold]Resumo do pipeline[/bold]"))
    summary = Table(show_header=True, header_style="bold")
    summary.add_column("Passo", style="cyan")
    summary.add_column("Resultado")
    for step, result in results.items():
        summary.add_row(step, result)
    console.print(summary)


@app.command("search")
def search(
    text: Annotated[str, typer.Argument(help="Texto de busca")],
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Logs detalhados")
    ] = False,
) -> None:
    """Buscar séries no SGS/BCB por texto."""
    from bs4 import BeautifulSoup

    _setup_logging(verbose)
    with console.status(f'[cyan]Buscando "{text}"...[/cyan]'):
        with ScraperClient() as scraper:
            html = scraper.search_series_by_text(text)
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        console.print("[yellow]Nenhum resultado encontrado.[/yellow]")
        return
    rows = extract_table_data(table)
    if not rows:
        console.print("[yellow]Nenhum resultado encontrado.[/yellow]")
        return

    result_table = Table(
        title=f'Resultados para "{text}"',
        show_header=True,
        header_style="bold",
    )
    result_table.add_column("ID", style="cyan", justify="right")
    result_table.add_column("Nome", style="green")
    for row in rows:
        result_table.add_row(str(row.series_id), row.name_index or "")
    console.print(result_table)
