"""Scraper for the BCB SGS website (www3.bcb.gov.br/sgspub).

:class:`ScraperClient` is a thin context manager around ``httpx2.Client``.
It keeps a JSESSION cookie alive for the duration of the context and
exposes methods that POST/GET against the SGS web pages. Each public
method is wrapped with ``quantilica-core`` retries (exponential
backoff, ``httpx2`` transport errors only).
"""

from collections.abc import Callable
from types import TracebackType

import httpx2
from quantilica.core.http import HttpClient
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
    ) -> None:
        super().__init__(timeout=timeout, transport=transport)
        self.language = language
        self.init_session(language=language)

    def init_session(self, language: str = "pt") -> None:
        """Start a fresh session against SGS and seed cookies.

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
        self.get(search_url, params=params)

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
        self.request("POST", LOCALIZAR_SERIES_URL, params=params, data=req_data)

        def _get_with_progress(url: str, current_downloaded: int) -> tuple[bytes, int]:
            downloaded = 0
            chunks = []
            with self.stream("GET", url) as stream_resp:
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
        response = self.request(
            "POST", LOCALIZAR_SERIES_URL, params=params, data=req_data
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
        response = self.request(
            "POST", LOCALIZAR_SERIES_URL, params=params, data=req_data
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
        r = self.request("POST", LOCALIZAR_SERIES_URL, data=req_data, params=params)
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
        r = self.request("POST", LOCALIZAR_SERIES_URL, data=req_data, params=params)
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
        r = self.request("POST", LOCALIZAR_SERIES_URL, data=req_data, params=params)
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
        r = self.request("POST", LOCALIZAR_SERIES_URL, data=req_data, params=params)
        return r.content

    def close(self) -> None:
        """Close the underlying HTTP session."""
        pass

    def __enter__(self) -> "ScraperClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
