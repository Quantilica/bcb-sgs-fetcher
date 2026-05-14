"""Parser for the search-results metadata page."""

import datetime as dt

from bs4 import BeautifulSoup

from ..constants import END, FREQUENCY, SERIES_ID, START
from .cleaning import clean_cell_text, translate_keys


def _convert_metadata_search(data: dict) -> dict:
    """Convert search metadata data to typed values."""
    data = data.copy()
    data[SERIES_ID] = int(data[SERIES_ID])
    frequencies_names = {"M": "mensal", "A": "anual"}
    data[FREQUENCY] = frequencies_names.get(data[FREQUENCY], data[FREQUENCY])
    frequency = data[FREQUENCY]
    if frequency == "mensal":
        last_format = "%b/%Y"
    elif frequency == "anual":
        last_format = "%Y"
    else:
        last_format = "%d/%m/%Y"
    data[START] = dt.datetime.strptime(data[START], "%d/%m/%Y").date()
    data[END] = dt.datetime.strptime(data[END], last_format).date()
    return data


def parse_metadata_search(html: str | bytes) -> dict[str, str]:
    """Parse the metadata search results table (single series)."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="tabelaSeries")
    thead_row = table.select_one("thead > tr")
    columns = [clean_cell_text(th.text) for th in thead_row.find_all("th")]
    tbody_row = table.find("tr", recursive=False)
    values = [clean_cell_text(td.text) for td in tbody_row.find_all("td")]
    data = dict(zip(columns, values))

    for unused_col in ("Sel.", "Met.", "Esp."):
        data.pop(unused_col, None)

    data = translate_keys(data)
    data = _convert_metadata_search(data)

    return data
