#!/usr/bin/env python3

"""Standalone CLI para bcb-sgs-fetcher (fina; comandos vivem em plugin.py)."""

from __future__ import annotations

import sys

from .plugin import app


def main(argv: list[str] | None = None) -> None:
    """Executar a CLI do bcb-sgs-fetcher.

    Args:
        argv (list[str] | None): Argumentos a parsear; None usa sys.argv.
    """
    if argv is None:
        argv = sys.argv[1:]
    app(argv)


if __name__ == "__main__":
    main()
