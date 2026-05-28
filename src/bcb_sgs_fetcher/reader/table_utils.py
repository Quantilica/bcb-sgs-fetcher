"""Generic helpers to parse the "Localizar séries" HTML tables.

The BCB site uses the same table structure to list active series inside a
group and deactivated series, so :func:`extract_table_data` works for
both. :func:`get_n_pages` returns the highest page number referenced by
``getPagina(N)`` javascript links.
"""

import datetime as dt
import re

from bs4 import BeautifulSoup, Tag

from ..constants import (
    END,
    FREQUENCY_ACRONYM,
    MESSAGE,
    SERIES_ID,
    SERIES_NAME_INDEX,
    SOURCE,
    SPECIAL,
    START,
    UNIT_NAME,
)
from ..models import GrupoSeriesRow

COLUMNS = {
    "Sel.": "selecao",
    "Cód.": SERIES_ID,
    "Nome completo": SERIES_NAME_INDEX,
    "Unid.": UNIT_NAME,
    "Per.": FREQUENCY_ACRONYM,
    "Início dd/MM/aaaa": START,
    "Últ. valor": END,
    "Fonte": SOURCE,
    "Esp.": SPECIAL,
    "Met.": "metadados",
    "message": MESSAGE,
}

# Locale-independent month abbreviation mapping (pt_BR).
_PT_BR_MONTHS = {
    "jan": 1,
    "fev": 2,
    "mar": 3,
    "abr": 4,
    "mai": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "set": 9,
    "out": 10,
    "nov": 11,
    "dez": 12,
}


def _parse_pt_br_month(token: str) -> int:
    """Map a Portuguese 3-letter month abbreviation to a month number."""
    key = token.strip().lower().rstrip(".")[:3]
    if key not in _PT_BR_MONTHS:
        raise ValueError(f"Unknown pt-BR month abbreviation: {token!r}")
    return _PT_BR_MONTHS[key]


def parse_date_ultimo_valor(data: dict[str, str]) -> dt.date | None:
    """Parse the END field according to the row's FREQUENCY_ACRONYM."""
    old_date = data[END]
    if old_date == "-":
        return None
    match data[FREQUENCY_ACRONYM]:
        case "A":
            return dt.date(int(old_date), 1, 1)
        case "Qd":
            # Quadrimestral (eg. 1º Quad. 2014)
            quarter, year = old_date.split("º Quad. ")
            return dt.date(int(year), int(quarter) * 4, 1)
        case "T":
            # Trimestral (eg. 1º Trim. 2009)
            quarter, year = old_date.split("º Trim. ")
            return dt.date(int(year), int(quarter) * 3, 1)
        case "M":
            month_token, year = old_date.split("/")
            return dt.date(int(year), _parse_pt_br_month(month_token), 1)
        case _:  # "S", "D"
            return dt.datetime.strptime(old_date, "%d/%m/%Y").date()


def extract_table_data(table: Tag) -> list[GrupoSeriesRow]:
    """Extract series rows from a "Localizar séries" results table."""
    rows_out: list[GrupoSeriesRow] = []
    columns = table.select("thead th")
    columns_names = [re.sub(r"\s+", " ", c.text.strip()) for c in columns]
    columns_names = [COLUMNS.get(c, c) for c in columns_names]
    rows = table.find_all("tr", recursive=False)
    for row in rows:
        if row.has_attr("title"):
            row_message: str | None = re.sub(r"\s+", " ", row["title"].strip())
        else:
            row_message = None
        row_cells = row.select("td")
        row_values = [re.sub(r"\s+", " ", c.text.strip()) for c in row_cells]
        row_data: dict[str, str | dt.date | None] = dict(
            zip(columns_names, row_values, strict=False)
        )

        # Skip header rows / empty rows that don't carry a series id.
        if SERIES_ID not in row_data:
            continue

        row_data[SERIES_ID] = int(row_data[SERIES_ID])
        row_data[START] = dt.datetime.strptime(row_data[START], "%d/%m/%Y").date()
        row_data[END] = parse_date_ultimo_valor(row_data)
        row_data[SPECIAL] = row_data.get(SPECIAL) == "S"

        rows_out.append(
            GrupoSeriesRow(
                series_id=row_data[SERIES_ID],
                name_index=row_data.get(SERIES_NAME_INDEX),
                frequency_acronym=row_data.get(FREQUENCY_ACRONYM),
                unit=row_data.get(UNIT_NAME),
                start_date=row_data.get(START),
                end_date=row_data.get(END),
                source=row_data.get(SOURCE),
                special=row_data.get(SPECIAL),
                message=row_message,
            )
        )

    return rows_out


def get_n_pages(soup: BeautifulSoup) -> int:
    """Return the highest page index referenced by ``getPagina(N)`` links."""
    n_pages = 1
    for a in soup.find_all("a"):
        href = a.get("href")
        if not href:
            continue
        if m := re.match(r"javascript:getPagina\((\d+)\)", href):
            n_pages = max(n_pages, int(m.group(1)))
    return n_pages
