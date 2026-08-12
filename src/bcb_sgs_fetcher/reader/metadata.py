"""Parsers for the two metadata iframes of the BCB SGS website.

Both parsers return typed :mod:`bcb_sgs_fetcher.models` dataclasses
instead of plain dictionaries. Intermediate parsing uses key constants
from :mod:`bcb_sgs_fetcher.constants` for stability against label
changes on the source page.
"""

import datetime as dt
import re
from collections.abc import Generator
from typing import Any

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md

from .. import logger
from ..constants import (
    END,
    MAX_VALUE,
    MIN_VALUE,
    PRECISION,
    SERIES_ID,
    START,
    THEME_HIERARCHY,
)
from ..models import (
    DescriptionField,
    DisseminationField,
    MethodologyField,
    ProviderField,
    SeriesMetadataBasic,
    SeriesMetadataFull,
)
from .cleaning import (
    clean_cell_text,
    clean_html_garbage,
    remove_empty_tags,
    remove_invalid_chars,
    translate_keys,
)


# BASIC METADATA PARSER =======================================================
def extract_themes(td: Tag) -> list[str]:
    """Extract themes from HTML content.

    Args:
        td: The BeautifulSoup ``<td>`` tag containing the themes.

    Returns:
        list[str]: Extracted themes.
    """
    value = td.select_one("span.textoPequeno").decode_contents()
    themes = value.split("<br/>")
    themes = [theme.strip().strip(" -") for theme in themes if theme.strip()]
    if themes and themes[-1].startswith("<b>"):
        themes[-1] = themes[-1][3:-4].strip()  # Remove <b> and </b> tags
    return themes


def _coerce_basic_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Convert raw HTML cell values to typed Python values."""
    data = data.copy()
    for key, value in data.items():
        if key == THEME_HIERARCHY:
            data[key] = extract_themes(value)
        elif key == PRECISION:
            data[key] = int(clean_cell_text(value.text))
        elif key == START or key == END:
            try:
                data[key] = dt.datetime.strptime(
                    clean_cell_text(value.text), "%d/%m/%Y"
                ).date()
            except ValueError:
                data[key] = None
        elif key == SERIES_ID:
            data[key] = int(clean_cell_text(value))
        elif key == MIN_VALUE or key == MAX_VALUE:
            try:
                data[key] = float(clean_cell_text(value.text).replace(",", "."))
            except ValueError:
                data[key] = None

    for key, value in data.items():
        if isinstance(value, str):
            data[key] = value.strip()
        elif isinstance(value, Tag):
            data[key] = clean_cell_text(value.text)

        if data[key] == "":
            data[key] = None
        elif data[key] == "sim":
            data[key] = True
        elif data[key] == "não":
            data[key] = False

    return data


def parse_metadata_basic(html: str | bytes) -> SeriesMetadataBasic:
    """Parse the "Dados básicos da série" iframe HTML.

    Args:
        html: The HTML string or bytes of the iframe.

    Returns:
        SeriesMetadataBasic: The parsed basic metadata.

    Raises:
        ValueError: If the table or rows are not found in the HTML.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        raise ValueError(
            "metadata table not found — server may have returned an error page"
        )
    rows = table.find_all("tr")
    if not rows:
        raise ValueError(
            "metadata table has no rows — server may have returned an error page"
        )
    first_row = rows[0]
    series_id = first_row.text.replace("Dados básicos da série ", "").strip()

    def parse_tr(tr: Tag) -> Generator[tuple[str, Any], None, None]:
        cells = tr.find_all("td")
        for i in range(0, len(cells), 2):
            key = clean_cell_text(cells[i].text)
            if key == "" or i + 1 >= len(cells):
                continue
            value = cells[i + 1]
            yield key, value

    data: dict[str, Any] = {SERIES_ID: series_id}
    data |= {k: v for tr in rows[1:] for k, v in parse_tr(tr)}

    data = translate_keys(data)
    data = _coerce_basic_dict(data)

    return SeriesMetadataBasic(
        series_id=data["series_id"],
        name=data.get("serie"),
        name_abbreviated=data.get("serie_abrev"),
        name_english=data.get("serie_ingles"),
        name_english_abbreviated=data.get("serie_abrev_ingles"),
        theme_hierarchy=data.get("hierarquia_tema") or [],
        frequency=data.get("frequencia"),
        unit=data.get("unidade"),
        source=data.get("fonte"),
        start_date=data.get("inicio"),
        end_date=data.get("fim"),
        series_type=data.get("tipo_serie"),
        precision=data.get("precisao"),
        min_value=data.get("min_value"),
        max_value=data.get("max_value"),
        manager_owner=data.get("gestor_proprietario"),
        special=data.get("especial"),
        formula=data.get("formula"),
        primitive_series=data.get("serie_primitiva"),
        warning_message=data.get("mensagem_aviso"),
    )


# FULL METADATA PARSER ========================================================
def _get_last_update(table: Tag) -> dt.date | None:
    """Get last update date from the header table of the full iframe."""
    table_row = table.find("tr", recursive=False)
    text = table_row.find_all("td", recursive=False)[-1].text
    match = re.search(r"\d{2}\/\d{2}\/\d{4}", text)
    if not match:
        return None
    return dt.datetime.strptime(match.group(), "%d/%m/%Y").date()


def _get_provider_data(table: Tag) -> list[ProviderField]:
    """Get provider data."""
    data: list[ProviderField] = []
    table_rows = table.find_all("tr", recursive=False)
    for tr in table_rows[1:]:
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 2:
            logger.debug("Unexpected provider-data row: %s", cells)
            continue
        key = clean_cell_text(cells[0].text)
        value: str | None = clean_cell_text(cells[1].text)
        if not value:
            value = None
        data.append(ProviderField(field=key, value=value))
    return data


def _markdownify_cell(raw_value: str) -> str | None:
    """Clean HTML content of a description/methodology cell."""
    value = remove_invalid_chars(raw_value)
    value = remove_empty_tags(value)
    value = clean_html_garbage(value)
    value = value.strip()
    try:
        if value.startswith("<"):
            value = md(value)
        value = value.replace("\n·", "- ")
    except Exception as e:
        logger.warning("Error parsing markdown: %s", e)
        return None
    return value


def _get_description(table: Tag) -> list[DescriptionField]:
    """Get description data."""
    section: str | None = None
    data: list[DescriptionField] = []
    table_rows = table.find_all("tr", recursive=False)
    for tr in table_rows[1:]:
        cells = tr.find_all("td", recursive=False)
        if len(cells) == 1:
            section = cells[0].text.strip()
            continue
        key, raw_value = cells[0].text.strip(), cells[1].decode_contents()
        value = _markdownify_cell(raw_value)
        data.append(DescriptionField(section=section, field=key, value=value))
    return data


def _get_methodology(table: Tag) -> list[MethodologyField]:
    """Get methodology data."""
    data: list[MethodologyField] = []
    table_rows = table.find_all("tr", recursive=False)[1:]
    for i in range(0, len(table_rows), 2):
        key = clean_cell_text(table_rows[i].text)
        raw_value = table_rows[i + 1].find("td", recursive=False).decode_contents()
        value = _markdownify_cell(raw_value)
        data.append(MethodologyField(field=key, value=value))
    return data


def _get_dissemination_formats(table: Tag) -> list[DisseminationField]:
    """Get dissemination-formats data."""
    section: str | None = None
    data: list[DisseminationField] = []
    table_rows = table.find_all("tr", recursive=False)
    for tr in table_rows[1:]:
        cells = tr.find_all("td", recursive=False)
        if len(cells) == 1:
            section = cells[0].text.strip()
            continue
        key, value = cells[0].text.strip(), cells[1].text.strip()
        data.append(
            DisseminationField(
                format_type=section,
                field=key,
                value=value or None,
            )
        )
    return data


def parse_metadata_full(html: str | bytes) -> SeriesMetadataFull:
    """Parse the "Metadados" iframe HTML.

    Args:
        html: The HTML string or bytes of the iframe.

    Returns:
        SeriesMetadataFull: The parsed full metadata.

    Raises:
        ValueError: If the expected metadata tables are not found.
    """
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("center > table")
    if not tables or len(tables) < 5:
        raise ValueError(
            "metadata full table not found — server may have returned an error page"
        )
    return SeriesMetadataFull(
        last_update=_get_last_update(tables[0]),
        provider_data=_get_provider_data(tables[1]),
        description=_get_description(tables[2]),
        methodology=_get_methodology(tables[3]),
        dissemination_formats=_get_dissemination_formats(tables[4]),
    )
