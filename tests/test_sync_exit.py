"""Código de saída do `series sync` com falhas (0 / 3 parcial / 1 nenhuma baixou)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from bcb_sgs_fetcher import plugin

runner = CliRunner()


class _FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch(monkeypatch, successful: int, failed: int) -> None:
    monkeypatch.setattr(
        plugin.bulk, "build_series_freqs", lambda **kw: [(1, "D"), (2, "D")]
    )
    monkeypatch.setattr(plugin, "SgsDataClient", _FakeClient)
    monkeypatch.setattr(
        plugin.bulk, "fetch_data_bulk", lambda *a, **kw: (successful, failed)
    )


def test_sync_exit_code_helper() -> None:
    assert plugin.sync_exit_code(2, 0) == 0
    assert plugin.sync_exit_code(1, 1) == plugin.EXIT_SYNC_PARTIAL == 3
    assert plugin.sync_exit_code(0, 2) == 1


@pytest.mark.parametrize(("ok", "failed", "code"), [(2, 0, 0), (1, 1, 3), (0, 2, 1)])
def test_series_sync_sai_conforme_as_falhas(
    monkeypatch, tmp_path, ok, failed, code
) -> None:
    _patch(monkeypatch, ok, failed)
    result = runner.invoke(plugin.app, ["series", "sync", "-o", str(tmp_path)])
    assert result.exit_code == code, result.output


def test_serie_pulada_conta_como_sucesso(monkeypatch, tmp_path) -> None:
    """--skip-existing: pulada conta como sucesso (1 falha + 1 pulada = parcial, 3)."""
    monkeypatch.setattr(
        plugin.bulk, "build_series_freqs", lambda **kw: [(1, "D"), (2, "D")]
    )
    monkeypatch.setattr(plugin, "SgsDataClient", _FakeClient)

    def fake_bulk(series_freqs, client, output, **kw):
        # 0 baixadas, 1 falha, 1 pulada (snapshot do dia já existia)
        kw["on_progress"](2, 2, 0, 1, 1)
        return 0, 1

    monkeypatch.setattr(plugin.bulk, "fetch_data_bulk", fake_bulk)
    result = runner.invoke(
        plugin.app, ["series", "sync", "--skip-existing", "-o", str(tmp_path)]
    )
    assert result.exit_code == 3, result.output

    def fake_bulk_so_puladas(series_freqs, client, output, **kw):
        kw["on_progress"](2, 2, 0, 0, 2)
        return 0, 0

    monkeypatch.setattr(plugin.bulk, "fetch_data_bulk", fake_bulk_so_puladas)
    result = runner.invoke(
        plugin.app, ["series", "sync", "--skip-existing", "-o", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
