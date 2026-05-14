"""Tests for the BCB SGS HTML scraper."""

import httpx

from bcb_sgs_fetcher import (
    BASE_URL,
    LOCALIZAR_SERIES_URL,
    METADADOS_BASICOS_URL,
    METADADOS_FULL_URL,
    ScraperClient,
)
from bcb_sgs_fetcher.constants import BASIC, FULL


def _make_transport(calls: list[tuple[str, str, bytes]]):
    """Build a MockTransport that captures (method, url, body) per call."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (request.method, str(request.url), bytes(request.content))
        )
        return httpx.Response(
            200, content=b"<html><body>ok</body></html>"
        )

    return httpx.MockTransport(handler)


def test_init_session_seeds_pt_cookie():
    calls: list[tuple[str, str, bytes]] = []
    transport = _make_transport(calls)
    with ScraperClient(transport=transport) as client:
        assert client.session is not None
        assert client.language == "pt"
    # First call is the session-seed GET to /index.jsp with idIdioma=P.
    method, url, _ = calls[0]
    assert method == "GET"
    assert url.startswith(BASE_URL + "/index.jsp")
    assert "idIdioma=P" in url


def test_request_metadata_html_hits_three_endpoints():
    calls: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append((request.method, url, bytes(request.content)))
        body = b"<html>basic</html>"
        if "cmiMetadados" in url:
            body = b"<html>full</html>"
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    with ScraperClient(transport=transport) as client:
        result = client.request_metadata_html(series_id=42)

    assert result[BASIC] == b"<html>basic</html>"
    assert result[FULL] == b"<html>full</html>"

    methods_urls = [(m, u.split("?")[0]) for m, u, _ in calls]
    # session init GET + POST localizarSeries + GET basic + GET full
    assert ("POST", LOCALIZAR_SERIES_URL) in methods_urls
    assert ("GET", METADADOS_BASICOS_URL) in methods_urls
    assert ("GET", METADADOS_FULL_URL) in methods_urls


def test_get_grupos_principais_posts_recuperarGruposPrincipais():
    calls: list[tuple[str, str, bytes]] = []
    transport = _make_transport(calls)
    with ScraperClient(transport=transport) as client:
        content = client.get_grupos_principais()
    assert content == b"<html><body>ok</body></html>"
    posts = [(m, u) for m, u, _ in calls if m == "POST"]
    assert any(
        "method=recuperarGruposPrincipais" in u for _, u in posts
    )


def test_change_page_sends_page_in_body():
    calls: list[tuple[str, str, bytes]] = []
    transport = _make_transport(calls)
    with ScraperClient(transport=transport) as client:
        client.change_page(page=3)
    body_blobs = [b for _, _, b in calls if b]
    assert any(b"hdNumPagina=3" in b for b in body_blobs)


def test_invalid_language_raises():
    import pytest

    with pytest.raises(ValueError):
        ScraperClient(language="fr")
