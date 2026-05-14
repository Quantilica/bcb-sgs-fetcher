# Copyright (c) 2026 Komesu, D.K.
# Licensed under the MIT License.

"""Scraper for the BCB SGS website (www3.bcb.gov.br/sgspub).

:class:`ScraperClient` is a thin context manager around ``httpx.Client``.
It keeps a JSESSION cookie alive for the duration of the context and
exposes methods that POST/GET against the SGS web pages. Each public
method is wrapped with ``tenacity`` retries (exponential backoff,
``httpx`` transport errors only).
"""

from types import TracebackType

import httpx
from tenacity import retry
from tenacity.retry import retry_if_exception_type
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_exponential

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

_RETRY_ON = retry_if_exception_type(
    (
        httpx.RequestError,
        httpx.HTTPStatusError,
        httpx.TimeoutException,
    )
)


class ScraperClient:
    """Maintains an ``httpx.Client`` session against the BCB SGS website.

    Args:
        timeout: HTTP timeout in seconds. Defaults to 600 (the default
            timeout used by ``bcb-sgs-metadata-db`` historically — the
            metadata iframes can be slow).
        language: ``"pt"`` (default) or ``"en"`` — selects the locale of
            the resulting HTML pages.
        transport: Optional ``httpx`` transport override (useful for
            testing with ``httpx.MockTransport``).
    """

    def __init__(
        self,
        timeout: float = 600,
        language: str = "pt",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self.language = language
        self._transport = transport
        self.session: httpx.Client | None = None
        self.init_session(language=language)

    def init_session(self, language: str = "pt") -> None:
        """Start a fresh session against SGS and seed cookies.

        Args:
            language: ``"pt"`` or ``"en"``.
        """
        if language not in ("pt", "en"):
            raise ValueError(f"Language unknown {language}")
        self.language = language
        kwargs: dict = {"timeout": self.timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        session = httpx.Client(**kwargs)
        search_url = BASE_URL + "/index.jsp"
        params: dict[str, str] = {}
        if language == "pt":
            params["idIdioma"] = "P"
        session.get(search_url, params=params)
        self.session = session

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=5, max=300),
        retry=_RETRY_ON,
        reraise=True,
    )
    def request_metadata_html(self, series_id: int) -> dict[str, bytes]:
        """Fetch the two metadata iframes for a series.

        Returns a dict keyed by ``"basic"`` and ``"full"`` with raw HTML
        bytes for each iframe.
        """
        logger.info("Requesting metadata html for series %s", series_id)
        # POST to land on the metadata frameset.
        req_data = {"hdOidSerieMetadados": series_id}
        params = {"method": "recuperarMetadadosPorDocn"}
        response = self.session.post(
            LOCALIZAR_SERIES_URL, params=params, data=req_data
        )
        response.raise_for_status()

        data: dict[str, bytes] = {}
        r_basic = self.session.get(METADADOS_BASICOS_URL)
        r_basic.raise_for_status()
        data[BASIC] = r_basic.content

        r_full = self.session.get(METADADOS_FULL_URL)
        r_full.raise_for_status()
        data[FULL] = r_full.content
        return data

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=5, max=300),
        retry=_RETRY_ON,
        reraise=True,
    )
    def get_series_desativadas(self) -> bytes:
        """Get the HTML of the deactivated-series listing."""
        logger.info("Getting series desativadas")
        req_data = {
            "hdTipoOrdenacao": 0,
            "hdTipoPesquisa": 3,
            "periodicidade": 0,
        }
        params = {"method": "localizarSeriesDesativadas"}
        response = self.session.post(
            LOCALIZAR_SERIES_URL, params=params, data=req_data
        )
        response.raise_for_status()
        return response.content

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=5, max=300),
        retry=_RETRY_ON,
        reraise=True,
    )
    def change_page(self, page: int) -> bytes:
        """Navigate the paginated series list to ``page``."""
        logger.info("Changing page to %s", page)
        req_data = {
            "hdNumPagina": page,
            "hdTipoOrdenacao": 0,
            "hdTipoPesquisa": 0,
            "periodicidade": 0,
        }
        params = {"method": "getPagina"}
        response = self.session.post(
            LOCALIZAR_SERIES_URL, params=params, data=req_data
        )
        response.raise_for_status()
        return response.content

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=5, max=300),
        retry=_RETRY_ON,
        reraise=True,
    )
    def get_grupos_principais(self) -> bytes:
        """Get the HTML of the root group list."""
        logger.info("Getting grupos principais")
        req_data = {
            "periodicidade": 0,
            "hdTipoOrdenacao": 0,
            "hdTipoPesquisa": 3,
        }
        params = {"method": "recuperarGruposPrincipais"}
        r = self.session.post(
            LOCALIZAR_SERIES_URL, data=req_data, params=params
        )
        r.raise_for_status()
        return r.content

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=5, max=300),
        retry=_RETRY_ON,
        reraise=True,
    )
    def get_arvore_grupo(self, id_grupo: int, seq_grupo: int) -> bytes:
        """Get the tree of series of a group."""
        logger.info("Getting arvore grupo %s %s", id_grupo, seq_grupo)
        req_data = {
            "hdOidGrupoSelecionado": id_grupo,
            "hdSeqGrupoSelecionado": seq_grupo,
        }
        params = {"method": "prepararTelaLcsArvore"}
        r = self.session.post(
            LOCALIZAR_SERIES_URL, data=req_data, params=params
        )
        r.raise_for_status()
        return r.content

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=5, max=300),
        retry=_RETRY_ON,
        reraise=True,
    )
    def get_grupo_series(self, id_grupo: int) -> bytes:
        """Get the series of a group."""
        logger.info("Getting grupo series %s", id_grupo)
        req_data = {
            "hdOidGrupoSelecionado": id_grupo,
            "periodicidade": 0,
            "hdTipoPesquisa": 1,
            "hdTipoOrdenacao": 0,
        }
        params = {"method": "localizarSeriesPorGrupo"}
        r = self.session.post(
            LOCALIZAR_SERIES_URL, data=req_data, params=params
        )
        r.raise_for_status()
        return r.content

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=5, max=300),
        retry=_RETRY_ON,
        reraise=True,
    )
    def get_series_by_fonte(self, fonte_id: int) -> bytes:
        """Get series listed for a "fonte" (source)."""
        logger.info("Getting series by fonte: %s", fonte_id)
        params = {"method": "localizarSeriesPorFonte"}
        req_data = {
            "fonte": fonte_id,
            "periodicidade": 0,
            "hdTipoPesquisa": 0,
            "hdTipoOrdenacao": 0,
        }
        r = self.session.post(
            LOCALIZAR_SERIES_URL, data=req_data, params=params
        )
        r.raise_for_status()
        return r.content

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=5, max=300),
        retry=_RETRY_ON,
        reraise=True,
    )
    def search_series_by_text(self, text: str) -> bytes:
        """Search series by free text."""
        logger.info("Getting series by text %s", text)
        params = {"method": "localizarSeriesPorTexto"}
        req_data = {
            "texto": text,
            "periodicidade": 0,
            "hdTipoPesquisa": 0,
            "hdTipoOrdenacao": 0,
        }
        r = self.session.post(
            LOCALIZAR_SERIES_URL, data=req_data, params=params
        )
        r.raise_for_status()
        return r.content

    def close(self) -> None:
        """Close the underlying HTTP session."""
        if self.session is not None:
            self.session.close()
            self.session = None

    def __enter__(self) -> "ScraperClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
