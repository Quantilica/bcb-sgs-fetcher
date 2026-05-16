"""Public API for ``bcb_sgs_fetcher``."""

from importlib.metadata import PackageNotFoundError, version

from quantilica_core.logging import get_logger

logger = get_logger(__name__)

try:
    __version__ = version("bcb-sgs-fetcher")
except PackageNotFoundError:
    __version__ = "0.0.0"

from . import bulk, storage  # noqa: E402  re-exported for convenience
from .bulk import (  # noqa: E402
    fetch_arvore_grupos,
    fetch_metadata_bulk,
    fetch_series_desativadas,
)
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
from .schema import SGS_CONTRACT  # noqa: E402
from .scraper import (  # noqa: E402
    BASE_URL,
    LOCALIZAR_SERIES_URL,
    METADADOS_BASICOS_URL,
    METADADOS_FULL_URL,
    ScraperClient,
)
from .writer import points_to_dataframe, save_parquet  # noqa: E402

__all__ = [
    "__version__",
    "API_BASE_URL",
    "ArvoreGrupoLink",
    "bulk",
    "fetch_arvore_grupos",
    "fetch_metadata_bulk",
    "fetch_series_desativadas",
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
    "SGS_CONTRACT",
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
    "points_to_dataframe",
    "save_parquet",
    "storage",
]
