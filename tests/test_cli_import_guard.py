"""Tests for bcb_sgs_fetcher.cli import guard (host absent)."""

import importlib
import importlib.abc
import importlib.machinery
import sys

import pytest

import bcb_sgs_fetcher.cli as cli_module

PLUGIN_MODULE = "bcb_sgs_fetcher.plugin"


@pytest.fixture
def plugin_bloqueado():
    """Simular host ausente (typer) e recarregar cli; restaura ao final."""
    salvo_typer = sys.modules.get("typer")
    salvo_plugin = sys.modules.pop(PLUGIN_MODULE, None)
    sys.modules["typer"] = None
    importlib.reload(cli_module)
    try:
        yield cli_module
    finally:
        sys.modules.pop("typer", None)
        if salvo_typer is not None:
            sys.modules["typer"] = salvo_typer
        sys.modules.pop(PLUGIN_MODULE, None)
        if salvo_plugin is not None:
            sys.modules[PLUGIN_MODULE] = salvo_plugin
        importlib.reload(cli_module)


def test_importar_cli_sem_host_nao_levanta_module_not_found(plugin_bloqueado):
    assert plugin_bloqueado.app is None
    assert plugin_bloqueado._PLUGIN_ERROR is not None


def test_main_sem_host_sai_com_codigo_1_e_mensagem(plugin_bloqueado, capsys):
    with pytest.raises(SystemExit) as excinfo:
        plugin_bloqueado.main(["sync"])
    assert excinfo.value.code == 1
    saida = capsys.readouterr().err
    assert "quantilica install bcb-sgs" in saida
    assert "Detalhe:" in saida


def test_erro_interno_do_plugin_propaga():
    """ImportError de modulo alheio nao e mascarado como host ausente."""
    salvo_plugin = sys.modules.pop(PLUGIN_MODULE, None)

    class _FalhaLoader(importlib.abc.Loader):
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            raise ModuleNotFoundError(
                "No module named 'outro_modulo'", name="outro_modulo"
            )

    class _FalhaFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname == PLUGIN_MODULE:
                return importlib.machinery.ModuleSpec(fullname, _FalhaLoader())
            return None

    finder = _FalhaFinder()
    sys.meta_path.insert(0, finder)
    try:
        with pytest.raises(ModuleNotFoundError):
            importlib.reload(cli_module)
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop(PLUGIN_MODULE, None)
        if salvo_plugin is not None:
            sys.modules[PLUGIN_MODULE] = salvo_plugin
        importlib.reload(cli_module)
