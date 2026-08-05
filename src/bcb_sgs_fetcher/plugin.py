"""Typer plugin for quantilica-cli integration."""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path
from typing import Annotated

import typer
from quantilica.core.cli import (
    get_console,
    make_batch_progress,
    setup_rich_logging,
)
from rich.panel import Panel
from rich.progress import (
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
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
from bcb_sgs_fetcher.constants import DEFAULT_OUTPUT_DIR

app = typer.Typer(help="Dados do SGS/BCB (séries temporais).")
series_sub = typer.Typer(help="Operações por série (dados, metadados, busca).")
catalogo_sub = typer.Typer(
    help="Catálogo de metadados do SGS/BCB (sync completo e passos)."
)
app.add_typer(series_sub, name="series")
app.add_typer(catalogo_sub, name="catalogo")
console = get_console()

_DEFAULT_OUTPUT = DEFAULT_OUTPUT_DIR


@series_sub.command("sync")
def fetch(
    series_id: Annotated[
        int | None,
        typer.Argument(help="ID de uma série (omita para baixar todas)"),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Diretório de saída"),
    ] = _DEFAULT_OUTPUT,
    frequency: Annotated[
        str | None,
        typer.Option(
            "-f",
            "--frequency",
            help="Periodicidade (D, S, M, T, Qd, A); aplica a todas.",
        ),
    ] = None,
    period: Annotated[
        str,
        typer.Option("--period", help="all (histórico) ou latest (últimas 20)"),
    ] = "all",
    ids_file: Annotated[
        Path | None,
        typer.Option("--ids-file", help="Baixar apenas os IDs deste arquivo"),
    ] = None,
    catalog_dir: Annotated[
        Path | None,
        typer.Option(
            "--catalog-dir",
            help="Dir. com arvore-grupos/series-desativadas (todas)",
        ),
    ] = None,
    skip_existing: Annotated[
        bool,
        typer.Option(
            "--skip-existing",
            help="Pular séries cujo snapshot do dia já existe",
        ),
    ] = False,
    workers: Annotated[
        int,
        typer.Option("--workers", help="Downloads concorrentes (padrão: 5)"),
    ] = 5,
    sleeptime: Annotated[
        float,
        typer.Option("--sleeptime", help="Pausa (s) por worker após cada série"),
    ] = 0.5,
    verbose: Annotated[bool, typer.Option("--verbose", help="Logs detalhados")] = False,
) -> None:
    """Baixar dados de todas as séries (padrão), de uma ou de uma lista."""
    setup_rich_logging(verbose, console=console)

    if series_id is not None and ids_file is not None:
        console.print("[red]Erro:[/red] use series_id OU --ids-file, não ambos.")
        raise typer.Exit(code=1)

    cat_dir = catalog_dir or storage.get_data_dir(output, dt.date.today())
    series_freqs = bulk.build_series_freqs(
        series_id=series_id,
        ids_file=ids_file,
        catalog_dir=cat_dir,
        frequency=frequency,
    )
    if not series_freqs:
        console.print(
            f"[yellow]Nenhuma série em {cat_dir}.[/yellow] Rode"
            " 'catalogo sync' primeiro ou informe series_id/--ids-file."
        )
        return

    try:
        import threading

        from quantilica.core.cli import make_download_progress

        with make_download_progress(console) as progress:
            task = progress.add_task(
                "[cyan]0✓  0✗  0 skip[/cyan]", total=len(series_freqs)
            )
            active_tasks: dict[int, int] = {}
            lock = threading.Lock()

            def on_start(series_id: int) -> None:
                with lock:
                    active_tasks[series_id] = progress.add_task(
                        f"Série {series_id}", total=None
                    )

            def on_progress(
                processed: int, total: int, ok: int, failed: int, skipped: int
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

            def on_finish(series_id: int, outcome: str) -> None:
                with lock:
                    task_id = active_tasks.pop(series_id, None)
                    if task_id is not None:
                        progress.remove_task(task_id)

            with SgsDataClient() as client:
                successful, failed_count = bulk.fetch_data_bulk(
                    series_freqs,
                    client,
                    output,
                    period=period,
                    skip_existing=skip_existing,
                    workers=workers,
                    sleeptime=sleeptime,
                    on_start=on_start,
                    on_progress=on_progress,
                    on_finish=on_finish,
                )
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado pelo usuário.[/yellow]")
        raise typer.Exit(code=130) from None

    if failed_count:
        console.print(
            f"[yellow]⚠[/yellow]  {successful} OK · [red]{failed_count} falha(s)[/red]"
        )
    else:
        console.print(
            f"[green]✓[/green]  [bold]{successful}[/bold]"
            " série(s) baixada(s) com sucesso."
        )


@series_sub.command("metadata")
def metadata(
    series_id: Annotated[
        int,
        typer.Argument(help="ID da série no SGS/BCB"),
    ],
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Diretório de saída"),
    ] = _DEFAULT_OUTPUT,
    verbose: Annotated[bool, typer.Option("--verbose", help="Logs detalhados")] = False,
) -> None:
    """Baixar metadados de uma série temporal do SGS/BCB."""
    setup_rich_logging(verbose, console=console)
    with console.status(f"[cyan]Baixando metadados da série {series_id}...[/cyan]"):
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
        lines.append(f"Período: [dim]{basic.start_date} → {basic.end_date}[/dim]")
    if basic.source:
        lines.append(f"Fonte: [dim]{basic.source}[/dim]")
    if lines:
        console.print(Panel("\n".join(lines), title=f"Série {series_id}"))
    console.print(f"[green]✓[/green] Metadados salvos em [dim]{meta_dir}[/dim]")


@catalogo_sub.command("arvore-grupos")
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
    verbose: Annotated[bool, typer.Option("--verbose", help="Logs detalhados")] = False,
) -> None:
    """Baixar a árvore de grupos e listas de séries do SGS/BCB."""
    setup_rich_logging(verbose, console=console)
    dest_dir = storage.get_data_dir(output, dt.date.today()) / "arvore-grupos"
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
            bulk.fetch_arvore_grupos(
                scraper, dest_dir, sleeptime=sleeptime, on_grupo=on_grupo
            )

    console.print(f"[green]✓[/green] Árvore de grupos salva em [dim]{dest_dir}[/dim]")


@catalogo_sub.command("series-desativadas")
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
    verbose: Annotated[bool, typer.Option("--verbose", help="Logs detalhados")] = False,
) -> None:
    """Baixar todas as séries desativadas do SGS/BCB."""
    setup_rich_logging(verbose, console=console)
    dest_dir = storage.get_data_dir(output, dt.date.today()) / "series-desativadas"
    with make_batch_progress(console) as progress:
        task = progress.add_task("[cyan]Séries desativadas[/cyan]", total=None)

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
        f"[green]✓[/green] Séries desativadas salvas em [dim]{dest_dir}[/dim]"
    )


@catalogo_sub.command("metadata-bulk")
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
    workers: Annotated[
        int,
        typer.Option("--workers", help="Downloads concorrentes (padrão: 4)"),
    ] = 4,
    verbose: Annotated[bool, typer.Option("--verbose", help="Logs detalhados")] = False,
) -> None:
    """Baixar metadados de múltiplas séries do SGS/BCB."""
    setup_rich_logging(verbose, console=console)

    ids: list[int] = list(series_id or [])
    if ids_file is not None:
        ids += [
            int(line.strip())
            for line in ids_file.read_text().splitlines()
            if line.strip()
        ]

    if not ids:
        console.print(
            "[red]Erro:[/red] forneça --ids-file ou pelo menos um --series-id.",
        )
        raise typer.Exit(code=1)

    dest_dir = storage.get_data_dir(output, dt.date.today()) / "metadata"
    import threading

    from quantilica.core.cli import make_download_progress

    with make_download_progress(console) as progress:
        task = progress.add_task("[cyan]0✓  0✗  0 skip[/cyan]", total=len(ids))
        active_tasks: dict[int, int] = {}
        lock = threading.Lock()

        def on_start(series_id: int) -> None:
            with lock:
                active_tasks[series_id] = progress.add_task(
                    f"Metadados {series_id}", total=None
                )

        def on_progress(
            processed: int, total: int, ok: int, failed: int, skipped: int
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

        def on_finish(series_id: int, outcome: str) -> None:
            with lock:
                task_id = active_tasks.pop(series_id, None)
                if task_id is not None:
                    progress.remove_task(task_id)

        scraper = ScraperClient()
        try:
            successful, failed_count = bulk.fetch_metadata_bulk(
                ids,
                scraper,
                dest_dir,
                sleeptime=sleeptime,
                skip_existing=skip_existing,
                workers=workers,
                on_start=on_start,
                on_progress=on_progress,
                on_finish=on_finish,
            )
        finally:
            scraper.close()

    if failed_count:
        console.print(
            f"[yellow]⚠[/yellow]  {successful} OK · [red]{failed_count} falha(s)[/red]"
        )
    else:
        console.print(
            f"[green]✓[/green]  [bold]{successful}[/bold] séries baixadas com sucesso."
        )


@catalogo_sub.command("extract-ids")
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
    verbose: Annotated[bool, typer.Option("--verbose", help="Logs detalhados")] = False,
) -> None:
    """Extrair IDs dos HTMLs baixados (arvore-grupos + series-desativadas)."""
    setup_rich_logging(verbose, console=console)
    data_dir = storage.get_data_dir(output, dt.date.today())
    with console.status("[cyan]Extraindo IDs dos HTMLs...[/cyan]"):
        ids = bulk.extract_ids_from_data_dir(data_dir)
    if not ids:
        console.print(f"[red]Erro:[/red] Nenhum ID encontrado em {data_dir}")
        raise typer.Exit(code=1)
    outfile = ids_file or (data_dir / "ids.txt")
    outfile.parent.mkdir(parents=True, exist_ok=True)
    outfile.write_text("\n".join(str(i) for i in ids) + "\n")
    console.print(
        f"[green]✓[/green] [bold]{len(ids)}[/bold] IDs únicos → [dim]{outfile}[/dim]"
    )


@catalogo_sub.command("sync")
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
    workers: Annotated[
        int,
        typer.Option("--workers", help="Downloads concorrentes (padrão: 4)"),
    ] = 4,
    verbose: Annotated[bool, typer.Option("--verbose", help="Logs detalhados")] = False,
) -> None:
    """Sincronizar o catálogo completo de metadados do SGS/BCB (4 passos)."""
    setup_rich_logging(verbose, console=console)
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
    console.print(Rule("[bold]Passo 2/4: Séries desativadas[/bold]"))
    with make_batch_progress(console) as progress:
        task = progress.add_task("[cyan]Séries desativadas[/cyan]", total=None)

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
        console.print("[red]Erro:[/red] Nenhum ID extraído — verifique erros acima.")
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
    import threading

    from quantilica.core.cli import make_download_progress

    with make_download_progress(console) as progress:
        task = progress.add_task("[cyan]0✓  0✗  0 skip[/cyan]", total=len(ids))
        active_tasks: dict[int, int] = {}
        lock = threading.Lock()

        def on_start(series_id: int) -> None:
            with lock:
                active_tasks[series_id] = progress.add_task(
                    f"Metadados {series_id}", total=None
                )

        def on_progress(
            processed: int, total: int, ok: int, failed: int, skipped: int
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

        def on_finish(series_id: int, outcome: str) -> None:
            with lock:
                task_id = active_tasks.pop(series_id, None)
                if task_id is not None:
                    progress.remove_task(task_id)

        scraper = ScraperClient()
        try:
            successful, failed_count = bulk.fetch_metadata_bulk(
                ids,
                scraper,
                data_dir / "metadata",
                sleeptime=sleeptime,
                skip_existing=skip_existing,
                workers=workers,
                on_start=on_start,
                on_progress=on_progress,
                on_finish=on_finish,
            )
        finally:
            scraper.close()

    if failed_count:
        results["Metadados"] = (
            f"[green]{successful}✓[/green]  [red]{failed_count}✗[/red]"
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


@series_sub.command("search")
def search(
    text: Annotated[str, typer.Argument(help="Texto de busca")],
    verbose: Annotated[bool, typer.Option("--verbose", help="Logs detalhados")] = False,
) -> None:
    """Buscar séries no SGS/BCB por texto."""
    from bs4 import BeautifulSoup

    setup_rich_logging(verbose, console=console)
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
