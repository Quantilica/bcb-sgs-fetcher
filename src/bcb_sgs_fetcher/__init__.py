"""Public API for ``bcb_sgs_fetcher``.

The package exposes a logger configured with a ``NullHandler`` so
consumers can opt-in to logging configuration.
"""

import logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

from . import storage  # noqa: E402  re-exported for convenience
from .data import (  # noqa: E402
    API_BASE_URL,
    SgsDataClient,
    fetch_series_data,
    get_daily_series,
    get_url,
)
from .models import (  # noqa: E402
    ArvoreGrupoLink,
    DescriptionField,
    DisseminationField,
    GrupoLink,
    GrupoSeriesRow,
    MethodologyField,
    ProviderField,
    SeriesMetadataBasic,
    SeriesMetadataFull,
    SeriesPoint,
    ThemeNode,
)
from .reader.arvore_grupos import (  # noqa: E402
    extract_arvore_grupos,
    extract_grupo_links,
    extract_theme_nodes,
)
from .reader.metadata import (  # noqa: E402
    parse_metadata_basic,
    parse_metadata_full,
)
from .reader.search import parse_metadata_search  # noqa: E402
from .reader.table_utils import (  # noqa: E402
    extract_table_data,
    get_n_pages,
)
from .scraper import (  # noqa: E402
    BASE_URL,
    LOCALIZAR_SERIES_URL,
    METADADOS_BASICOS_URL,
    METADADOS_FULL_URL,
    ScraperClient,
)

__all__ = [
    "API_BASE_URL",
    "ArvoreGrupoLink",
    "BASE_URL",
    "DescriptionField",
    "DisseminationField",
    "GrupoLink",
    "GrupoSeriesRow",
    "LOCALIZAR_SERIES_URL",
    "METADADOS_BASICOS_URL",
    "METADADOS_FULL_URL",
    "MethodologyField",
    "ProviderField",
    "ScraperClient",
    "SeriesMetadataBasic",
    "SeriesMetadataFull",
    "SeriesPoint",
    "SgsDataClient",
    "ThemeNode",
    "extract_arvore_grupos",
    "extract_grupo_links",
    "extract_table_data",
    "extract_theme_nodes",
    "fetch_series_data",
    "get_daily_series",
    "get_n_pages",
    "get_url",
    "logger",
    "parse_metadata_basic",
    "parse_metadata_full",
    "parse_metadata_search",
    "storage",
]
