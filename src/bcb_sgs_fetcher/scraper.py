"""Scraper for the BCB SGS website (www3.bcb.gov.br/sgspub).

:class:`ScraperClient` is a thin context manager around ``httpx2.Client``.
It keeps a JSESSION cookie alive for the duration of the context and
exposes methods that POST/GET against the SGS web pages. Each public
method is wrapped with ``quantilica-core`` retries (exponential
backoff, ``httpx2`` transport errors only).

Responses are also inspected for session-expiry artifacts (redirects to
the login/index page, ``401``/``403`` statuses, or an expired-session
HTML page). When detected, the session is transparently re-seeded
(:meth:`ScraperClient.init_session`) and the request retried.
"""

from collections.abc import Callable
from types import TracebackType

import httpx2
from quantilica.core.http import BROWSER_HEADERS, HttpClient, HttpStatusError
from quantilica.core.retry import with_retry

from . import logger
from .constants import BASIC, FULL

BASE_URL = "https://www3.bcb.gov.br/sgspub"

# POST localizarSeries
LOCALIZAR_SERIES_URL = f"{BASE_URL}/localizarseries/localizarSeries.do"

# consultarmetadados
CONSULTAR_METADADOS_URL = f"{BASE_URL}/JSP/consultarmetadados"
# GET cmiDadosBasicos
METADADOS_BASICOS_URL = f"{CONSULTAR_METADADOS_URL}/cmiDadosBasicos.jsp"
# GET cmiMetadados
METADADOS_FULL_URL = f"{CONSULTAR_METADADOS_URL}/cmiMetadados.jsp"

# Session-expiry / WAF artifacts that indicate the JSESSIONID is no
# longer valid (or was never established) and the session must be
# re-seeded before the request can succeed.
SESSION_EXPIRED_MARKERS: tuple[str, ...] = (
    "sessão expirada",
    "sessao expirada",
    "session expired",
    "sua sessão",
    "sua sessao",
    "timeout de sessão",
    "timeout de sessao",
    "sessão não é válida",
    "sessao nao e valida",
    "login.jsf",
    "acessonegado",
)

# Status codes that usually mean the session/cookie is no longer accepted.
_SESSION_RENEW_STATUS_CODES = frozenset({401, 403, 419, 440})

# Path fragments that, when the server redirects to them, mean the
# session died (the real endpoint URLs never live under these paths).
_SESSION_REDIRECT_MARKERS: tuple[str, ...] = (
    "/index.jsp",
    "/login",
    "login.jsp",
    "login.jsf",
    "acessonegado",
)

_MAX_SESSION_RENEWALS_PER_CALL = 3

_RETRY_EXCEPTIONS = (
    httpx2.RequestError,
    httpx2.HTTPStatusError,
    httpx2.TimeoutException,
)

_retry_http = with_retry(
    attempts=5,
    base_delay=5.0,
    max_delay=300.0,
    retry_exceptions=_RETRY_EXCEPTIONS,
)


class SessionExpiredError(RuntimeError):
    """Raised when the SGS server rejects the session after renewals."""


def looks_like_session_expired(
    *,
    status_code: int = 200,
    url: str = "",
    content: bytes,
) -> bool:
    """Heuristically detect a session-expiry response from SGS.

    Args:
        status_code: The HTTP status code of the response.
        url: The final URL after redirects.
        content: The raw response body.

    Returns:
        bool: True when the response looks like an expired session page.
    """
    if status_code in _SESSION_RENEW_STATUS_CODES:
        return True
    final_url = (url or "").lower()
    if any(marker in final_url for marker in _SESSION_REDIRECT_MARKERS):
        # Only treat as expired when the body is a small HTML shell
        # (redirects to the index land on a login/frameset page).
        return b"<html" in content[:2048].lower()
    body = content[:8192].decode("latin-1", errors="replace").lower()
    return any(marker in body for marker in SESSION_EXPIRED_MARKERS)


class ScraperClient(HttpClient):
    """Maintains an HTTP session against the BCB SGS website.

    Args:
        timeout: HTTP timeout in seconds. Defaults to 30.
        language: ``"pt"`` (default) or ``"en"`` — selects the locale of
            the resulting HTML pages.
        transport: Optional ``httpx2`` transport override (useful for
            testing with ``httpx2.MockTransport``).
    """

    def __init__(
        self,
        timeout: float = 30,
        language: str = "pt",
        transport: httpx2.BaseTransport | None = None,
        min_interval: float = 0.0,
    ) -> None:
        super().__init__(
            timeout=timeout,
            transport=transport,
            min_interval=min_interval,
        )
        self.language = language
        self.init_session(language=language)

    def init_session(self, language: str = "pt") -> None:
        """Start a fresh session against SGS and seed cookies.

        Renews the JSESSIONID cookie by hitting ``/index.jsp`` with
        modern browser headers (required by the SGS portal's WAF).

        Args:
            language: ``"pt"`` or ``"en"``.
        """
        if language not in ("pt", "en"):
            raise ValueError(f"Language unknown {language}")
        self.language = language
        search_url = BASE_URL + "/index.jsp"
        params: dict[str, str] = {}
        if language == "pt":
            params["idIdioma"] = "P"
        self.get(search_url, params=params, headers=self.sgs_headers())

    def sgs_headers(self, referer: str | None = None) -> dict[str, str]:
        """Build the browser-like headers required by the SGS portal.

        The SGS site is served behind a WAF that rejects non-browser
        clients; every request must carry a modern ``User-Agent``,
        ``Accept`` and ``Accept-Language``, and form POSTs should include
        a ``Referer`` pointing at the SGS base URL.

        Args:
            referer: Optional referer override; defaults to ``BASE_URL``.

        Returns:
            dict[str, str]: The request headers.
        """
        headers = dict(BROWSER_HEADERS)
        headers["Accept-Encoding"] = "gzip, deflate"
        headers["Referer"] = referer or BASE_URL + "/"
        return headers

    @_retry_http
    def request_metadata_html(
        self, series_id: int, progress: Callable[[int, int], None] | None = None
    ) -> dict[str, bytes]:
        """Fetch the two metadata iframes for a series.

        Returns a dict keyed by ``"basic"`` and ``"full"`` with raw HTML
        bytes for each iframe.

        Args:
            series_id: The series ID.
            progress: Optional callback for download progress.

        Returns:
            dict[str, bytes]: A dictionary with 'basic' and 'full' metadata HTML.
        """
        logger.info("Requesting metadata html for series %s", series_id)
        # POST to land on the metadata frameset.
        req_data = {"hdOidSerieMetadados": series_id}
        params = {"method": "recuperarMetadadosPorDocn"}
        self.fetch_validated(
            "POST",
            LOCALIZAR_SERIES_URL,
            params=params,
            data=req_data,
            referer=LOCALIZAR_SERIES_URL,
        )

        def _get_with_progress(url: str, current_downloaded: int) -> tuple[bytes, int]:
            downloaded = 0
            chunks = []
            with self.stream(
                "GET", url, headers=self.sgs_headers(referer=LOCALIZAR_SERIES_URL)
            ) as stream_resp:
                stream_resp.raise_for_status()
                for chunk in stream_resp.iter_bytes():
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if progress is not None:
                        progress(current_downloaded + downloaded, 0)
            return b"".join(chunks), downloaded

        data: dict[str, bytes] = {}
        content_basic, basic_size = _get_with_progress(METADADOS_BASICOS_URL, 0)
        data[BASIC] = content_basic

        content_full, _ = _get_with_progress(METADADOS_FULL_URL, basic_size)
        data[FULL] = content_full
        return data

    @_retry_http
    def get_series_desativadas(self) -> bytes:
        """Get the HTML of the deactivated-series listing.

        Returns:
            bytes: The HTML content.
        """
        logger.info("Getting series desativadas")
        req_data = {
            "hdTipoOrdenacao": 0,
            "hdTipoPesquisa": 3,
            "periodicidade": 0,
        }
        params = {"method": "localizarSeriesDesativadas"}
        response = self.fetch_validated(
            "POST",
            LOCALIZAR_SERIES_URL,
            params=params,
            data=req_data,
            referer=LOCALIZAR_SERIES_URL,
        )
        return response.content

    @_retry_http
    def change_page(self, page: int) -> bytes:
        """Navigate the paginated series list to ``page``.

        Args:
            page: The page number to navigate to.

        Returns:
            bytes: The HTML content of the new page.
        """
        logger.info("Changing page to %s", page)
        req_data = {
            "hdNumPagina": page,
            "hdTipoOrdenacao": 0,
            "hdTipoPesquisa": 0,
            "periodicidade": 0,
        }
        params = {"method": "getPagina"}
        response = self.fetch_validated(
            "POST",
            LOCALIZAR_SERIES_URL,
            params=params,
            data=req_data,
            referer=LOCALIZAR_SERIES_URL,
        )
        return response.content

    @_retry_http
    def get_grupos_principais(self) -> bytes:
        """Get the HTML of the root group list.

        Returns:
            bytes: The HTML content of the root group list.
        """
        logger.info("Getting grupos principais")
        req_data = {
            "periodicidade": 0,
            "hdTipoOrdenacao": 0,
            "hdTipoPesquisa": 3,
        }
        params = {"method": "recuperarGruposPrincipais"}
        r = self.fetch_validated(
            "POST",
            LOCALIZAR_SERIES_URL,
            data=req_data,
            params=params,
            referer=LOCALIZAR_SERIES_URL,
        )
        return r.content

    @_retry_http
    def get_arvore_grupo(self, id_grupo: int, seq_grupo: int) -> bytes:
        """Get the tree of series of a group.

        Args:
            id_grupo: The group ID.
            seq_grupo: The group sequence.

        Returns:
            bytes: The HTML content of the group tree.
        """
        logger.info("Getting arvore grupo %s %s", id_grupo, seq_grupo)
        req_data = {
            "hdOidGrupoSelecionado": id_grupo,
            "hdSeqGrupoSelecionado": seq_grupo,
        }
        params = {"method": "prepararTelaLcsArvore"}
        r = self.fetch_validated(
            "POST",
            LOCALIZAR_SERIES_URL,
            data=req_data,
            params=params,
            referer=LOCALIZAR_SERIES_URL,
        )
        return r.content

    @_retry_http
    def get_grupo_series(self, id_grupo: int) -> bytes:
        """Get the series of a group.

        Args:
            id_grupo: The group ID.

        Returns:
            bytes: The HTML content of the group series.
        """
        logger.info("Getting grupo series %s", id_grupo)
        req_data = {
            "hdOidGrupoSelecionado": id_grupo,
            "periodicidade": 0,
            "hdTipoPesquisa": 1,
            "hdTipoOrdenacao": 0,
        }
        params = {"method": "localizarSeriesPorGrupo"}
        r = self.fetch_validated(
            "POST",
            LOCALIZAR_SERIES_URL,
            data=req_data,
            params=params,
            referer=LOCALIZAR_SERIES_URL,
        )
        return r.content

    @_retry_http
    def search_series_by_text(self, text: str) -> bytes:
        """Search series by free text.

        Args:
            text: The text to search for.

        Returns:
            bytes: The HTML content of the search results.
        """
        logger.info("Getting series by text %s", text)
        params = {"method": "localizarSeriesPorTexto"}
        req_data = {
            "texto": text,
            "periodicidade": 0,
            "hdTipoPesquisa": 0,
            "hdTipoOrdenacao": 0,
        }
        r = self.fetch_validated(
            "POST",
            LOCALIZAR_SERIES_URL,
            data=req_data,
            params=params,
            referer=LOCALIZAR_SERIES_URL,
        )
        return r.content

    def fetch_validated(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str | int] | None = None,
        referer: str | None = None,
    ) -> httpx2.Response:
        """Perform a request with transparent session renewal.

        Sends the request with SGS browser headers and inspects the
        response for session-expiry artifacts (redirect to the index /
        login page, ``401``/``403``, or an expired-session HTML page).
        When detected, the session is re-seeded and the request retried
        up to :data:`_MAX_SESSION_RENEWALS_PER_CALL` times before
        raising :class:`SessionExpiredError`.

        Args:
            method: HTTP method.
            url: The target URL.
            params: Optional query parameters.
            data: Optional form data.
            referer: Optional referer override.

        Returns:
            httpx2.Response: The validated response.

        Raises:
            SessionExpiredError: If the session keeps expiring after
                the maximum number of renewals.
        """
        last_error: Exception | None = None
        last_content = b""
        last_status = 0
        for attempt in range(_MAX_SESSION_RENEWALS_PER_CALL + 1):
            try:
                response = self.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=self.sgs_headers(referer),
                )
            except HttpStatusError as exc:
                if exc.status_code not in _SESSION_RENEW_STATUS_CODES:
                    raise
                # 401/403/etc: server rejected the session outright.
                last_error = exc
                last_status = exc.status_code
                last_content = b""
            else:
                if not looks_like_session_expired(
                    status_code=response.status_code,
                    url=str(response.url),
                    content=response.content,
                ):
                    return response
                last_status = response.status_code
                last_content = response.content
            if attempt < _MAX_SESSION_RENEWALS_PER_CALL:
                logger.warning(
                    "SGS session appears expired (status=%s); renewing "
                    "session and retrying (%d/%d)",
                    last_status,
                    attempt + 1,
                    _MAX_SESSION_RENEWALS_PER_CALL,
                )
                self.init_session(language=self.language)
        snippet = last_content[:1024].decode("latin-1", errors="replace")
        logger.error(
            "SGS session could not be renewed after %d attempts. Response snippet: %s",
            _MAX_SESSION_RENEWALS_PER_CALL,
            snippet,
        )
        raise SessionExpiredError(
            f"SGS session expired and could not be renewed at {url} "
            f"(last status={last_status}): {snippet[:200]!r}"
        ) from last_error

    def close(self) -> None:
        """Close the underlying HTTP session (cookies are preserved)."""
        HttpClient.close(self)

    def __enter__(self) -> "ScraperClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
