"""Helpers to clean text and translate Portuguese labels to internal keys."""

import re
from typing import Any

from ..constants import (
    END,
    FORMULA,
    FREQUENCY,
    MANAGER_OWNER,
    MAX_VALUE,
    MIN_VALUE,
    PRECISION,
    PRIMITIVE_SERIES,
    SERIES_ID,
    SERIES_NAME,
    SERIES_NAME_ABBR,
    SERIES_NAME_EN,
    SERIES_NAME_EN_ABBR,
    SERIES_TYPE,
    SOURCE,
    SPECIAL,
    START,
    THEME_HIERARCHY,
    UNIT_NAME,
    WARNING_MESSAGE,
)


def clean_cell_text(text: str) -> str:
    """Clean text in a cell of an HTML table."""
    text = (
        text.replace("\n", " ")
        .replace("\t", " ")
        .replace("\xa0", " ")
        .strip(" \n\t-\r")
    )
    text = re.sub(" +", " ", text)
    return text


def translate_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Translate Portuguese labels of metadata to internal keys."""
    new_keys = {
        # METADATA SEARCH
        "Cód.": SERIES_ID,
        "Nome completo": SERIES_NAME,
        "Unid.": UNIT_NAME,
        "Per.": FREQUENCY,
        "Início dd/MM/aaaa": START,
        "Últ. valor": END,
        "Fonte": SOURCE,
        # METADATA BASIC
        "Nome abreviado": SERIES_NAME_ABBR,
        "Full name": SERIES_NAME_EN,
        "Short name": SERIES_NAME_EN_ABBR,
        "Cadeia de temas": THEME_HIERARCHY,
        "Periodicidade": FREQUENCY,
        "Unidade padrão": UNIT_NAME,
        "Data início": START,
        "Data fim": END,
        "Tipo da série": SERIES_TYPE,
        "Casas decimais de divulgação": PRECISION,
        "Valor máximo (formato europeu)": MAX_VALUE,
        "Valor mínimo (formato europeu)": MIN_VALUE,
        "Gestor proprietário": MANAGER_OWNER,
        "Série especial?": SPECIAL,
        "Fórmula": FORMULA,
        "Séries primitivas": PRIMITIVE_SERIES,
        "Mensagem de aviso": WARNING_MESSAGE,
    }
    new_dict = {}
    for key in data.keys():
        value = data[key]
        if key in new_keys:
            new_key = new_keys[key]
            new_dict[new_key] = value
        else:
            new_dict[key] = value
    return new_dict


def remove_invalid_chars(text: str) -> str:
    """Remove invalid characters from text."""
    invalid_chars = ("\xa0",)
    for char in invalid_chars:
        text = text.replace(char, "")
    # \x96 -> en dash
    text = text.replace("\x96", "–")
    text = text.strip()
    return text


def remove_empty_tags(text: str) -> str:
    """Remove empty tags from text."""
    text = re.sub(r"<p>[\s&nbsp;]*</p>", "", text)
    text = re.sub(r"<o:p>[\s&nbsp;]*</o:p>", "", text)
    return text


def clean_html_garbage(text: str) -> str:
    """Remove HTML comments and XML namespace markers from text."""
    pattern = re.compile(r"<!--.*?-->", re.DOTALL)
    text = pattern.sub("", text)
    text = re.sub(r"<\?xml:namespace.*?/>", "", text, flags=re.IGNORECASE)
    return text
