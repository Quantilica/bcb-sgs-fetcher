#!/usr/bin/env python3

"""Standalone CLI para bcb-sgs-fetcher (fina; comandos vivem em plugin.py)."""

from __future__ import annotations

import sys

_HOST_MODULES = {"typer", "rich", "quantilica"}

try:
    from .plugin import app
except ImportError as exc:  # host (typer/rich/quantilica-cli) ausente
    if (exc.name or "").split(".")[0] not in _HOST_MODULES:
        raise
    app = None
    _PLUGIN_ERROR = exc
else:
    _PLUGIN_ERROR = None


def main(argv: list[str] | None = None) -> None:
    """Executar a CLI do bcb-sgs-fetcher.

    Args:
        argv (list[str]): Argumentos a parsear; None usa sys.argv.

    Raises:
        SystemExit: Sempre — 0 em sucesso (via Typer), 1 se o host
            (typer/rich/quantilica-cli) não estiver instalado.
    """
    if app is None:
        print(
            "Erro: CLI requer 'typer' e 'rich' (via quantilica-cli). "
            'Instale via "quantilica install bcb-sgs". '
            f"Detalhe: {_PLUGIN_ERROR}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if argv is None:
        argv = sys.argv[1:]
    app(argv)


if __name__ == "__main__":
    main()
