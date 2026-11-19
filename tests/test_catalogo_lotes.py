"""Retomada em lotes do ``catalogo sync``: renovação protegida, handshake
persistente, ``--skip-existing`` íntegro e saída parcial."""

from __future__ import annotations

import json
import os
import signal
import threading
import time

import httpx2
import pytest
from quantilica.core.http import HttpStatusError
from quantilica.core.manifests import manifest_sidecar_path
from typer.testing import CliRunner

from bcb_sgs_fetcher import ScraperClient, bulk, plugin
from bcb_sgs_fetcher import scraper as scraper_mod
from bcb_sgs_fetcher.models import SeriesMetadataBasic, SeriesMetadataFull

runner = CliRunner()


# --- helpers --------------------------------------------------------------


def _ok_transport() -> httpx2.MockTransport:
    return httpx2.MockTransport(lambda r: httpx2.Response(200, content=b"<html/>"))


class _RecordingEvent(threading.Event):
    """Evento que registra as esperas pedidas e não espera de verdade."""

    def __init__(self) -> None:
        super().__init__()
        self.waits: list[float] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout or 0.0)
        return self.is_set()


@pytest.fixture
def fake_metadata(monkeypatch):
    """Metadados válidos sem rede; devolve o registro das séries pedidas."""
    requested: list[int] = []

    def request(self, series_id, progress=None):
        requested.append(series_id)
        return {"basic": b"<basic/>", "full": b"<full/>"}

    monkeypatch.setattr(ScraperClient, "request_metadata_html", request)
    monkeypatch.setattr(
        bulk,
        "parse_metadata_basic",
        lambda html: SeriesMetadataBasic(series_id=_current_id(requested)),
    )
    monkeypatch.setattr(bulk, "parse_metadata_full", lambda html: SeriesMetadataFull())
    monkeypatch.setattr(bulk.time, "sleep", lambda _s: None)
    return requested


def _current_id(requested: list[int]) -> int:
    return requested[-1]


def _write_done(dest, series_id: int, *, sidecar: bool = True) -> None:
    path = dest / f"{series_id:06d}.json"
    path.write_text(json.dumps({"basic": {}, "full": {}}))
    if sidecar:
        manifest_sidecar_path(path).write_text("{}")


# --- (1a) renovação protegida ---------------------------------------------


def test_renew_que_falha_conta_a_serie_como_falha_e_o_worker_segue(
    tmp_path, monkeypatch, fake_metadata
):
    monkeypatch.setattr(bulk, "_RENEW_BACKOFF_BASE", 7.0)
    renews = {"n": 0}
    first_try = {"done": False}

    def flaky_request(self, series_id, progress=None):
        # A 1ª série derruba a sessão (ex.: HTTP 500 em cmiMetadados.jsp).
        if series_id == 1 and not first_try["done"]:
            first_try["done"] = True
            raise RuntimeError("HTTP 500 em cmiMetadados.jsp")
        fake_metadata.append(series_id)
        return {"basic": b"<basic/>", "full": b"<full/>"}

    def renew(self):
        renews["n"] += 1
        if renews["n"] == 1:
            raise HttpStatusError("https://sgs/index.jsp", 502)

    monkeypatch.setattr(ScraperClient, "request_metadata_html", flaky_request)
    monkeypatch.setattr(ScraperClient, "renew", renew)
    stop = _RecordingEvent()
    scraper = ScraperClient(transport=_ok_transport())
    try:
        ok, failed = bulk.fetch_metadata_bulk(
            [1, 2],
            scraper,
            tmp_path,
            sleeptime=0,
            workers=1,
            stop_event=stop,
        )
    finally:
        scraper.close()

    # Antes: a exceção do renew() escapava do worker e derrubava o pipeline.
    assert (ok, failed) == (1, 1)
    assert (tmp_path / "000002.json").exists()
    assert not (tmp_path / "000001.json").exists()
    # A série 2 só tentou renovar de novo depois da espera de backoff.
    assert renews["n"] == 2
    assert stop.waits == [7.0]


def test_backoff_da_renovacao_dobra_e_tem_teto(tmp_path, monkeypatch, fake_metadata):
    monkeypatch.setattr(bulk, "_RENEW_BACKOFF_BASE", 10.0)
    monkeypatch.setattr(bulk, "_RENEW_BACKOFF_MAX", 25.0)
    monkeypatch.setattr(
        ScraperClient,
        "request_metadata_html",
        lambda self, series_id, progress=None: (_ for _ in ()).throw(
            RuntimeError("sessão caiu")
        ),
    )

    def renew(self):
        raise HttpStatusError("https://sgs/index.jsp", 502)

    monkeypatch.setattr(ScraperClient, "renew", renew)
    stop = _RecordingEvent()
    scraper = ScraperClient(transport=_ok_transport())
    try:
        ok, failed = bulk.fetch_metadata_bulk(
            [1, 2, 3, 4],
            scraper,
            tmp_path,
            sleeptime=0,
            workers=1,
            stop_event=stop,
        )
    finally:
        scraper.close()

    assert (ok, failed) == (0, 4)
    # 1ª falha: sem espera; depois 10, 20 e 25 (teto).
    assert stop.waits == [10.0, 20.0, 25.0]


def test_construcao_do_cliente_do_worker_que_falha_nao_derruba_o_pipeline(
    tmp_path, monkeypatch, fake_metadata
):
    monkeypatch.setattr(bulk, "_RENEW_BACKOFF_BASE", 0.0)
    real = bulk.ScraperClient
    built = {"n": 0}

    def factory(**kwargs):
        built["n"] += 1
        if built["n"] == 1:
            raise HttpStatusError("https://sgs/index.jsp", 502)  # init_session
        return real(**kwargs)

    scraper = ScraperClient(transport=_ok_transport())
    monkeypatch.setattr(bulk, "ScraperClient", factory)
    try:
        ok, failed = bulk.fetch_metadata_bulk(
            [1, 2], scraper, tmp_path, sleeptime=0, workers=1
        )
    finally:
        scraper.close()

    assert (ok, failed) == (1, 1)
    assert (tmp_path / "000002.json").exists()


# --- (1b) init_session ----------------------------------------------------


def _index_handler(statuses: list[int], gets: list[int]):
    def handler(request: httpx2.Request) -> httpx2.Response:
        gets.append(1)
        status = statuses.pop(0) if statuses else 200
        return httpx2.Response(status, content=b"<html/>")

    return handler


def test_init_session_espera_e_tenta_de_novo_em_5xx(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    gets: list[int] = []
    # 3 tentativas internas do HttpClient por rodada: a 1ª rodada inteira falha.
    transport = httpx2.MockTransport(_index_handler([502, 502, 502], gets))

    with ScraperClient(transport=transport) as client:
        assert client.language == "pt"

    assert len(gets) == 4  # 3 da 1ª rodada + 1 que deu certo
    assert [d for d in sleeps if d >= 10] == [scraper_mod._INIT_SESSION_BASE_DELAY]


def test_init_session_desiste_depois_de_todas_as_rodadas(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    transport = httpx2.MockTransport(_index_handler([502] * 100, []))

    with pytest.raises(Exception) as excinfo:
        ScraperClient(transport=transport)

    assert type(excinfo.value).__name__ == "RetryError"
    waits = [d for d in sleeps if d >= 10]
    assert len(waits) == scraper_mod._INIT_SESSION_ATTEMPTS - 1
    assert waits[0] == 15.0
    assert max(waits) == scraper_mod._INIT_SESSION_MAX_DELAY
    assert waits == sorted(waits)


def test_init_session_nao_espera_em_erro_de_cliente(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    gets: list[int] = []
    transport = httpx2.MockTransport(_index_handler([404], gets))

    with pytest.raises(HttpStatusError):
        ScraperClient(transport=transport)

    assert len(gets) == 1
    assert [d for d in sleeps if d >= 10] == []


# --- (1c) --skip-existing retoma e não deixa arquivo parcial ---------------


def test_metadata_is_done_reconhece_json_ou_apenas_o_sidecar(tmp_path):
    _write_done(tmp_path, 1)
    # JSON já enviado e removido: só o sidecar (marcador de frescor) resta.
    _write_done(tmp_path, 2)
    (tmp_path / "000002.json").unlink()

    assert bulk.metadata_is_done(tmp_path, 1)
    assert bulk.metadata_is_done(tmp_path, 2)
    assert not bulk.metadata_is_done(tmp_path, 3)


def test_metadata_is_done_regera_sidecar_de_json_valido_sem_ele(tmp_path):
    # Rodada morta entre a gravação do JSON e a do sidecar.
    _write_done(tmp_path, 5, sidecar=False)
    sidecar = manifest_sidecar_path(tmp_path / "000005.json")
    assert not sidecar.exists()

    assert bulk.metadata_is_done(tmp_path, 5)
    assert sidecar.exists()


def test_metadata_is_done_refaz_json_corrompido(tmp_path):
    path = tmp_path / "000009.json"
    path.write_text('{"basic": {"series_i')  # truncado

    assert not bulk.metadata_is_done(tmp_path, 9)
    assert not path.exists()


def test_skip_existing_retoma_sem_refazer_o_que_ja_foi_baixado(tmp_path, fake_metadata):
    _write_done(tmp_path, 1)  # JSON + sidecar
    _write_done(tmp_path, 2)
    (tmp_path / "000002.json").unlink()  # já enviado: só o sidecar
    skipped: list[int] = []
    scraper = ScraperClient(transport=_ok_transport())
    try:
        ok, failed = bulk.fetch_metadata_bulk(
            [1, 2, 3],
            scraper,
            tmp_path,
            sleeptime=0,
            workers=1,
            skip_existing=True,
            on_progress=lambda p, t, o, f, s: skipped.append(s),
        )
    finally:
        scraper.close()

    assert (ok, failed) == (1, 0)
    assert fake_metadata == [3]  # só a série 3 foi pedida ao SGS
    assert skipped[-1] == 2
    assert not (tmp_path / "000002.json").exists()  # nada recriado


def test_interrupcao_para_limpo_e_a_rodada_seguinte_continua(tmp_path, monkeypatch):
    """Parada pedida no meio (SIGTERM do tempo limite): sem arquivo parcial."""
    requested: list[int] = []
    stop = threading.Event()
    current = {"id": 0}

    def request(self, series_id, progress=None):
        requested.append(series_id)
        current["id"] = series_id
        if series_id == 2:
            stop.set()  # o sinal chega com a série 2 em andamento
        return {"basic": b"<basic/>", "full": b"<full/>"}

    monkeypatch.setattr(ScraperClient, "request_metadata_html", request)
    monkeypatch.setattr(
        bulk,
        "parse_metadata_basic",
        lambda html: SeriesMetadataBasic(series_id=current["id"]),
    )
    monkeypatch.setattr(bulk, "parse_metadata_full", lambda html: SeriesMetadataFull())

    scraper = ScraperClient(transport=_ok_transport())
    finished: list[tuple[int, str]] = []
    try:
        ok, failed = bulk.fetch_metadata_bulk(
            [1, 2, 3, 4],
            scraper,
            tmp_path,
            sleeptime=0,
            workers=1,
            stop_event=stop,
            on_finish=lambda sid, outcome: finished.append((sid, outcome)),
        )
    finally:
        scraper.close()

    # A série em andamento terminou; as seguintes nem começaram.
    assert (ok, failed) == (2, 0)
    assert requested == [1, 2]
    assert all(outcome == "ok" for _, outcome in finished)
    # Nenhum temporário nem JSON truncado; tudo que existe é íntegro e pronto.
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    for series_id in (1, 2):
        data = json.loads((tmp_path / f"{series_id:06d}.json").read_text())
        assert set(data) == {"basic", "full"}
        assert bulk.metadata_is_done(tmp_path, series_id)
    assert not bulk.metadata_is_done(tmp_path, 3)

    # Rodada seguinte com --skip-existing: só o que faltou.
    requested.clear()
    stop2 = threading.Event()
    scraper = ScraperClient(transport=_ok_transport())
    try:
        ok, failed = bulk.fetch_metadata_bulk(
            [1, 2, 3, 4],
            scraper,
            tmp_path,
            sleeptime=0,
            workers=1,
            skip_existing=True,
            stop_event=stop2,
        )
    finally:
        scraper.close()
    assert (ok, failed) == (2, 0)
    assert requested == [3, 4]


def test_gravacao_atomica_nao_deixa_json_se_a_escrita_falha(tmp_path, monkeypatch):
    """O JSON só aparece inteiro: falha na escrita não deixa arquivo parcial."""
    from quantilica.core import files as core_files

    def boom(src, dst):
        raise OSError("disco cheio")

    monkeypatch.setattr(core_files.Path, "replace", boom)
    with pytest.raises(Exception):  # noqa: B017 - StorageError do core
        bulk.storage.write_combined_metadata(tmp_path, 1, basic={"a": 1}, full={"b": 2})

    assert not (tmp_path / "000001.json").exists()
    assert not list(tmp_path.glob(".*.tmp"))
    assert not bulk.metadata_is_done(tmp_path, 1)


# --- (1d) código de saída do catalogo sync --------------------------------


def _patch_pipeline(monkeypatch, fake_bulk):
    class _Client:
        timeout = 30
        language = "pt"
        transport = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def close(self):
            pass

    monkeypatch.setattr(plugin, "ScraperClient", _Client)
    monkeypatch.setattr(plugin.bulk, "fetch_arvore_grupos", lambda *a, **kw: None)
    monkeypatch.setattr(plugin.bulk, "fetch_series_desativadas", lambda *a, **kw: None)
    monkeypatch.setattr(plugin.bulk, "extract_ids_from_data_dir", lambda d: [1, 2, 3])
    monkeypatch.setattr(plugin.bulk, "fetch_metadata_bulk", fake_bulk)


def _sync(tmp_path, *extra):
    return runner.invoke(plugin.app, ["catalogo", "sync", "-o", str(tmp_path), *extra])


def test_catalogo_sync_completo_sai_com_zero(monkeypatch, tmp_path):
    _patch_pipeline(monkeypatch, lambda *a, **kw: (3, 0))
    assert _sync(tmp_path).exit_code == 0


def test_catalogo_sync_tolera_falhas_de_metadados_como_antes(monkeypatch, tmp_path):
    _patch_pipeline(monkeypatch, lambda *a, **kw: (2, 1))
    assert _sync(tmp_path).exit_code == 0


def test_catalogo_sync_interrompido_com_progresso_sai_com_3(monkeypatch, tmp_path):
    def fake(*a, **kw):
        kw["stop_event"].set()
        return 2, 0

    _patch_pipeline(monkeypatch, fake)
    result = _sync(tmp_path, "--skip-existing")
    assert result.exit_code == plugin.EXIT_SYNC_PARTIAL == 3, result.output
    assert "--skip-existing" in result.output


def test_catalogo_sync_interrompido_sem_progresso_sai_com_1(monkeypatch, tmp_path):
    def fake(*a, **kw):
        kw["stop_event"].set()
        return 0, 0

    _patch_pipeline(monkeypatch, fake)
    assert _sync(tmp_path).exit_code == 1


def test_catalogo_sync_max_runtime_para_o_passo_4(monkeypatch, tmp_path):
    seen = {}

    def fake(*a, **kw):
        seen["stopped"] = kw["stop_event"].wait(10)
        return 1, 0

    _patch_pipeline(monkeypatch, fake)
    started = time.monotonic()
    result = _sync(tmp_path, "--max-runtime", "1")
    assert time.monotonic() - started < 8
    assert seen["stopped"] is True
    assert result.exit_code == 3, result.output


def test_catalogo_sync_sigterm_encerra_com_3_e_restaura_o_handler(
    monkeypatch, tmp_path
):
    previous = signal.getsignal(signal.SIGTERM)

    def fake(*a, **kw):
        os.kill(os.getpid(), signal.SIGTERM)  # o `timeout` do agendador
        assert kw["stop_event"].wait(5)
        return 2, 0

    _patch_pipeline(monkeypatch, fake)
    result = _sync(tmp_path)

    assert result.exit_code == 3, result.output
    assert signal.getsignal(signal.SIGTERM) == previous


def test_catalogo_sync_sessao_indisponivel_no_passo_4_sai_com_1(monkeypatch, tmp_path):
    _patch_pipeline(monkeypatch, lambda *a, **kw: (0, 0))
    calls = {"n": 0}
    original = plugin.ScraperClient

    def factory():
        calls["n"] += 1
        if calls["n"] == 3:  # passos 1 e 2 abrem o cliente; o 3º é o do passo 4
            raise HttpStatusError("https://sgs/index.jsp", 502)
        return original()

    monkeypatch.setattr(plugin, "ScraperClient", factory)
    result = _sync(tmp_path)
    assert result.exit_code == 1
    assert "sessão do SGS indisponível" in result.output


def test_help_do_catalogo_sync_anuncia_max_runtime():
    result = runner.invoke(plugin.app, ["catalogo", "sync", "--help"])
    assert "--max-runtime" in result.output
    assert "--skip-existing" in result.output
