"""Typed data structures returned by the package.

Every public function in :mod:`bcb_sgs_fetcher` returns one of the
dataclasses defined in this module. The field set is a strict superset of
what the upstream BCB endpoints provide, so unknown values are kept as
``None`` instead of raising.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(slots=True)
class SeriesPoint:
    """A single observation of a BCB SGS time series."""

    series_id: int
    date: date
    value: Decimal | None = None
    date_end: date | None = None


@dataclass(slots=True)
class ProviderField:
    """One row of the "Dados do divulgador" table."""

    field: str
    value: str | None


@dataclass(slots=True)
class DescriptionField:
    """One row of the description table (grouped by section)."""

    section: str | None
    field: str
    value: str | None


@dataclass(slots=True)
class MethodologyField:
    """One row of the methodology table."""

    field: str
    value: str | None


@dataclass(slots=True)
class DisseminationField:
    """One row of the dissemination-formats table."""

    format_type: str | None
    field: str
    value: str | None


@dataclass(slots=True)
class SeriesMetadataBasic:
    """Output of :func:`bcb_sgs_fetcher.reader.metadata.parse_metadata_basic`.

    Maps to the "Dados básicos da série" iframe in the BCB SGS site.
    """

    series_id: int
    name: str | None = None
    name_abbreviated: str | None = None
    name_english: str | None = None
    name_english_abbreviated: str | None = None
    theme_hierarchy: list[str] = field(default_factory=list)
    frequency: str | None = None
    unit: str | None = None
    source: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    series_type: str | None = None
    precision: int | None = None
    min_value: float | None = None
    max_value: float | None = None
    manager_owner: str | None = None
    special: bool | None = None
    formula: str | None = None
    primitive_series: str | None = None
    warning_message: str | None = None


@dataclass(slots=True)
class SeriesMetadataFull:
    """Output of :func:`bcb_sgs_fetcher.reader.metadata.parse_metadata_full`.

    Maps to the "Metadados" iframe in the BCB SGS site.
    """

    last_update: date | None = None
    provider_data: list[ProviderField] = field(default_factory=list)
    description: list[DescriptionField] = field(default_factory=list)
    methodology: list[MethodologyField] = field(default_factory=list)
    dissemination_formats: list[DisseminationField] = field(
        default_factory=list
    )


@dataclass(slots=True)
class GrupoSeriesRow:
    """One row of a "Localizar séries" results table.

    Used both for active groups (``arvore-grupos``) and deactivated series
    (``series-desativadas``).
    """

    series_id: int
    name_index: str | None = None
    frequency_acronym: str | None = None
    unit: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    source: str | None = None
    special: bool | None = None
    message: str | None = None


@dataclass(slots=True)
class GrupoLink:
    """A link to a group page extracted from the group tree (árvore)."""

    grupo_id: str
    grupo_nome: str


@dataclass(slots=True)
class ArvoreGrupoLink:
    """A top-level group link from the group tree page.

    The ``hd_oid_grupo_selecionado`` / ``hd_seq_grupo_selecionado`` fields
    are the POST parameters expected by
    :meth:`bcb_sgs_fetcher.scraper.ScraperClient.get_arvore_grupo`.
    """

    hd_oid_grupo_selecionado: str
    hd_seq_grupo_selecionado: str
    nome: str
    subtext: str | None = None


@dataclass(slots=True)
class ThemeNode:
    """One node of the theme tree exposed by an árvore-grupos page."""

    theme_chain: list[str]
    link_id: str | None
    link_name: str | None
    level: int
