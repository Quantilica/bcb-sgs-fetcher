"""Garantir largura determinística para os testes de help Rich/Typer.

Sem tty, Rich/Typer derivam a largura de ``COLUMNS`` (fallback 80) e
TRUNCAM conteúdo com "…" quando estreito — o texto some, nenhuma
normalização recupera. Fixar 200 torna o rendering idêntico no CI e local.
"""

from __future__ import annotations

import os

os.environ["COLUMNS"] = "200"
