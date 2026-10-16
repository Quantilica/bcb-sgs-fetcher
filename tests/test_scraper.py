"""Tests for the BCB SGS HTML scraper."""

import httpx2
import pytest
from quantilica.core.http import HttpStatusError

from bcb_sgs_fetcher import (
    BASE_URL,
    LOCALIZAR_SERIES_URL,
    METADADOS_BASICOS_URL,
    METADADOS_FULL_URL,
    ScraperClient,
    SessionExpiredError,
    looks_like_session_expired,
    validate_metadata_iframe,
)
from bcb_sgs_fetcher.constants import BASIC, FULL


def _make_transport(calls: list[tuple[str, str, bytes]]):
    """Build a MockTransport that captures (method, url, body) per call."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append((request.method, str(request.url), bytes(request.content)))
        return httpx2.Response(200, content=b"<html><body>ok</body></html>")

    return httpx2.MockTransport(handler)


def test_init_session_seeds_pt_cookie():
    calls: list[tuple[str, str, bytes]] = []
    transport = _make_transport(calls)
    with ScraperClient(transport=transport) as client:
        assert client.language == "pt"
    # First call is the session-seed GET to /index.jsp with idIdioma=P.
    method, url, _ = calls[0]
    assert method == "GET"
    assert url.startswith(BASE_URL + "/index.jsp")
    assert "idIdioma=P" in url


def test_request_metadata_html_hits_three_endpoints():
    calls: list[tuple[str, str, bytes]] = []
    basic_body = b"<html><body>Dados b\xe1sicos da s\xe9rie</body></html>"
    full_body = b"<html>full</html>"

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        calls.append((request.method, url, bytes(request.content)))
        body = basic_body
        if "cmiMetadados" in url:
            body = full_body
        return httpx2.Response(200, content=body)

    transport = httpx2.MockTransport(handler)
    with ScraperClient(transport=transport) as client:
        result = client.request_metadata_html(series_id=42)

    assert result[BASIC] == basic_body
    assert result[FULL] == full_body

    methods_urls = [(m, u.split("?")[0]) for m, u, _ in calls]
    # session init GET + POST localizarSeries + GET basic + GET full
    assert ("POST", LOCALIZAR_SERIES_URL) in methods_urls
    assert ("GET", METADADOS_BASICOS_URL) in methods_urls
    assert ("GET", METADADOS_FULL_URL) in methods_urls


_BASIC_IFRAME = b"<html><body>Dados b\xe1sicos da s\xe9rie</body></html>"
_FULL_IFRAME = b"<html>full</html>"


def test_validate_metadata_iframe_accepts_valid_basic():
    validate_metadata_iframe(
        _BASIC_IFRAME, "cmiDadosBasicos", require_basic_marker=True
    )


def test_validate_metadata_iframe_rejects_empty():
    with pytest.raises(SessionExpiredError):
        validate_metadata_iframe(b"   ", "cmiDadosBasicos")


def test_validate_metadata_iframe_rejects_expired_page():
    expired = b"<script>alert('Session expired. Please login again.')</script>"
    with pytest.raises(SessionExpiredError):
        validate_metadata_iframe(expired, "cmiDadosBasicos", require_basic_marker=True)


def test_validate_metadata_iframe_requires_basic_marker():
    plain = b"<html><body>unexpected</body></html>"
    with pytest.raises(SessionExpiredError):
        validate_metadata_iframe(plain, "cmiDadosBasicos", require_basic_marker=True)
    # The full iframe does not carry the basic marker.
    validate_metadata_iframe(plain, "cmiMetadados", require_basic_marker=False)


def test_request_metadata_html_raises_on_expired_iframe():
    expired = b"<script>alert('Session expired. Please login again.')</script>"

    def handler(request: httpx2.Request) -> httpx2.Response:
        if "cmiDadosBasicos" in str(request.url):
            return httpx2.Response(200, content=expired)
        return httpx2.Response(200, content=_FULL_IFRAME)

    with ScraperClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(SessionExpiredError):
            client.request_metadata_html(series_id=42)


def test_renew_reestablishes_handshake():
    calls: list[tuple[str, str, bytes]] = []
    with ScraperClient(transport=_make_transport(calls)) as client:
        before = sum(1 for _, u, _ in calls if "/index.jsp" in u)
        client.renew()
        after = sum(1 for _, u, _ in calls if "/index.jsp" in u)
    assert after == before + 1


def test_scraper_client_uses_persistent_http_client():
    # Regression: a persistent client is required so cookies set on the
    # /index.jsp 302 redirect (JSESSIONID/TS/SGS) survive into later
    # localizarSeries.do POSTs; a transient client drops them and the
    # portal answers with its session-expired page.
    with ScraperClient(transport=_make_transport([])) as client:
        assert client._client is not None
        first = client._client
        client.renew()
        assert client._client is not None
        assert client._client is not first


def test_renew_clears_cookies():
    with ScraperClient(transport=_make_transport([])) as client:
        client.cookies.set("JSESSIONID", "x")
        client.renew()
        assert not any(c.name == "JSESSIONID" for c in client.cookies.jar)


def test_scraper_client_accepts_shared_limiter():
    from quantilica.core.http import RateLimiter

    limiter = RateLimiter(2.5)
    with ScraperClient(transport=_make_transport([]), shared_limiter=limiter) as c:
        assert c.min_interval == 2.5
        limiter.min_interval = 5.0
        assert c.min_interval == 5.0  # state is shared, not copied


def test_get_grupos_principais_posts_recuperarGruposPrincipais():
    calls: list[tuple[str, str, bytes]] = []
    transport = _make_transport(calls)
    with ScraperClient(transport=transport) as client:
        content = client.get_grupos_principais()
    assert content == b"<html><body>ok</body></html>"
    posts = [(m, u) for m, u, _ in calls if m == "POST"]
    assert any("method=recuperarGruposPrincipais" in u for _, u in posts)


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


def test_retry_exhaustion_raises_retryerror(monkeypatch):
    import pytest
    from quantilica.core import retry as core_retry
    from quantilica.core.retry import RetryError

    # The retry decorator uses retry_call's default ``sleep``, bound to
    # ``time.sleep`` at import time, so patching ``time.sleep`` globally
    # has no effect. Replace the default to skip the real backoff waits.
    monkeypatch.setitem(
        core_retry.retry_call.__kwdefaults__,
        "sleep",
        lambda _delay: None,
    )

    posts: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(200, content=b"ok")
        if request.method == "POST":
            posts.append(str(request.url))
        return httpx2.Response(500, content=b"boom")

    transport = httpx2.MockTransport(handler)
    with ScraperClient(transport=transport) as client:
        with pytest.raises(RetryError):
            client.get_grupos_principais()

    # Three attempts (HttpClient default), each issuing one POST to localizarSeries.
    assert len(posts) == 3


# --- browser-like headers -------------------------------------------------


def test_sgs_headers_are_browser_like():
    transport = httpx2.MockTransport(lambda r: httpx2.Response(200, content=b"ok"))
    with ScraperClient(transport=transport) as client:
        headers = client.sgs_headers()
    assert headers["User-Agent"].startswith("Mozilla/5.0")
    assert "text/html" in headers["Accept"]
    assert "pt-BR" in headers["Accept-Language"]
    assert headers["Accept-Encoding"] == "gzip, deflate"
    assert headers["Referer"].startswith(BASE_URL)


def test_requests_carry_browser_headers():
    headers_seen: dict[str, str] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        headers_seen.update(request.headers)
        return httpx2.Response(200, content=b"<html><body>ok</body></html>")

    transport = httpx2.MockTransport(handler)
    with ScraperClient(transport=transport) as client:
        client.get_grupos_principais()

    assert headers_seen["user-agent"].startswith("Mozilla/5.0")
    assert headers_seen["referer"] == LOCALIZAR_SERIES_URL
    assert "text/html" in headers_seen["accept"]


# --- session renewal -------------------------------------------------------


def _session_handler(posts: list[int], index_gets: list[int], script):
    """Build a handler driven by *script*: a list of POST outcomes.

    Each outcome is either an int status or a bytes body (200). Redirect
    statuses carry a ``Location`` header pointing at the SGS index page.
    GET /index.jsp always answers 200 and is counted in *index_gets*.
    """

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            index_gets.append(str(request.url))
            return httpx2.Response(200, content=b"<html>index</html>")
        posts.append(str(request.url))
        outcome = script[min(len(posts) - 1, len(script) - 1)]
        if isinstance(outcome, int):
            headers = None
            if 300 <= outcome < 400:
                headers = {"Location": f"{BASE_URL}/index.jsp"}
            return httpx2.Response(outcome, content=b"denied", headers=headers)
        return httpx2.Response(200, content=outcome)

    return handler


def test_session_renews_after_401():
    posts: list[int] = []
    index_gets: list[int] = []
    script: list[int | bytes] = [401, b"<html><body>ok</body></html>"]
    handler = _session_handler(posts, index_gets, script)
    transport = httpx2.MockTransport(handler)
    with ScraperClient(transport=transport) as client:
        content = client.get_grupos_principais()

    assert content == b"<html><body>ok</body></html>"
    # 1 constructor seed + 1 renewal.
    assert len(index_gets) == 2
    assert len(posts) == 2


def test_session_renews_after_expired_session_page():
    posts: list[int] = []
    index_gets: list[int] = []
    script: list[int | bytes] = [
        b"<html><body>Sess\xe3o expirada. Efetue novo login.</body></html>",
        b"<html><body>ok</body></html>",
    ]
    handler = _session_handler(posts, index_gets, script)
    transport = httpx2.MockTransport(handler)
    with ScraperClient(transport=transport) as client:
        content = client.get_grupos_principais()

    assert content == b"<html><body>ok</body></html>"
    assert len(index_gets) == 2
    assert len(posts) == 2


def test_session_renews_after_redirect_to_index():
    posts: list[int] = []
    index_gets: list[int] = []
    script: list[int | bytes] = [302, b"<html><body>ok</body></html>"]
    handler = _session_handler(posts, index_gets, script)
    transport = httpx2.MockTransport(handler)
    with ScraperClient(transport=transport) as client:
        content = client.get_grupos_principais()

    assert content == b"<html><body>ok</body></html>"
    # 1 constructor seed + 1 redirect landing (POST->GET on index.jsp)
    # + 1 session renewal.
    assert len(index_gets) == 3
    assert len(posts) == 2


def test_session_renewal_exhaustion_raises_session_expired_error():
    posts: list[int] = []
    index_gets: list[int] = []
    script: list[int | bytes] = [403]
    handler = _session_handler(posts, index_gets, script)
    transport = httpx2.MockTransport(handler)
    with ScraperClient(transport=transport) as client:
        with pytest.raises(SessionExpiredError):
            client.get_grupos_principais()

    # 1 constructor seed + _MAX_SESSION_RENEWALS_PER_CALL renewals.
    assert len(index_gets) == 4
    assert len(posts) == 4


def test_non_session_http_error_propagates_without_renewal():
    posts: list[int] = []
    index_gets: list[int] = []
    script: list[int | bytes] = [404]
    handler = _session_handler(posts, index_gets, script)
    transport = httpx2.MockTransport(handler)
    with ScraperClient(transport=transport) as client:
        with pytest.raises(HttpStatusError):
            client.get_grupos_principais()

    # No session renewal attempted for non-session status codes.
    assert len(index_gets) == 1
    assert len(posts) == 1


def test_looks_like_session_expired_heuristics():
    assert looks_like_session_expired(
        content=b"<html><body>Sessao expirada</body></html>"
    )
    assert looks_like_session_expired(status_code=401, content=b"")
    assert looks_like_session_expired(status_code=403, content=b"x")
    assert looks_like_session_expired(
        url=f"{BASE_URL}/index.jsp", content=b"<html>x</html>"
    )
    # A normal listing page (real endpoint URL, table content) is valid.
    assert not looks_like_session_expired(
        status_code=200,
        url=LOCALIZAR_SERIES_URL,
        content=b"<html><body><table id='tabelaSeries'></table></body></html>",
    )
    assert not looks_like_session_expired(content=b"<html><body>ok</body></html>")
