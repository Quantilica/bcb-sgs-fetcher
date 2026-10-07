"""Testes da CLI standalone (fina) do bcb-sgs-fetcher."""

from __future__ import annotations

import re

import pytest

from bcb_sgs_fetcher.cli import main

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(text: str) -> str:
    """Normalizar saída Rich para asserções imunes a layout.

    Versões de typer/rich resolvem diferente no CI (latest) e no workspace,
    mudando largura/quebra do painel de help. Remover ANSI e TODO o
    whitespace torna `--opt` localizável mesmo quebrado em duas linhas.
    """
    return "".join(_ANSI.sub("", text).split())


def _run_cli(argv: list[str]) -> int:
    """Executar a CLI fina (main) e retornar o código de saída.

    O modo standalone do Typer encerra com ``SystemExit`` mesmo em caso de
    sucesso (código 0).

    Args:
        argv (list[str]): Argumentos da linha de comando.

    Returns:
        int: Código de saída do processo.
    """
    with pytest.raises(SystemExit) as exc_info:
        main(argv)
    return exc_info.value.code


def test_help(capsys) -> None:
    """--help imprime os grupos de comandos (series e catalogo)."""
    assert _run_cli(["--help"]) == 0
    out = capsys.readouterr().out
    assert "series" in out
    assert "catalogo" in out


def test_series_help(capsys) -> None:
    """series --help lista os subcomandos por série."""
    assert _run_cli(["series", "--help"]) == 0
    out = capsys.readouterr().out
    assert "sync" in out
    assert "metadata" in out
    assert "search" in out


def test_catalogo_help(capsys) -> None:
    """catalogo --help lista os subcomandos do catálogo."""
    assert _run_cli(["catalogo", "--help"]) == 0
    out = capsys.readouterr().out
    assert "sync" in out
    assert "arvore-grupos" in out
    assert "extract-ids" in out


def test_series_metadata_help(capsys) -> None:
    """series metadata --help documenta o argumento series_id e a opção -o."""
    assert _run_cli(["series", "metadata", "--help"]) == 0
    out = capsys.readouterr().out
    assert "series_id" in out
    assert "-o" in out


def test_series_sync_help(capsys) -> None:
    """series sync --help documenta as principais opções."""
    assert _run_cli(["series", "sync", "--help"]) == 0
    out = _flat(capsys.readouterr().out)
    assert "--period" in out
    assert "--ids-file" in out
    assert "--skip-existing" in out
