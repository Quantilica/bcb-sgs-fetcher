"""Tests for the BCB SGS HTML parsers (basic metadata + tables)."""

import datetime as dt

from bs4 import BeautifulSoup

from bcb_sgs_fetcher import (
    SeriesMetadataBasic,
    extract_table_data,
    get_n_pages,
    parse_metadata_basic,
)
from bcb_sgs_fetcher.reader.table_utils import parse_date_ultimo_valor

BASIC_HTML = """
<html><body>
<table>
  <tr><td>Dados básicos da série 1</td></tr>
  <tr>
    <td>Nome completo</td><td>Taxa de câmbio - Livre - Dólar americano (venda) - diário</td>
    <td>Nome abreviado</td><td>Dólar venda</td>
  </tr>
  <tr>
    <td>Cadeia de temas</td>
    <td><span class="textoPequeno">Setor externo<br/>Taxas de câmbio<br/><b>Diário</b></span></td>
    <td>Periodicidade</td><td>diária</td>
  </tr>
  <tr>
    <td>Unidade padrão</td><td>u.m.c./US$</td>
    <td>Data início</td><td>28/11/1984</td>
  </tr>
  <tr>
    <td>Data fim</td><td>-</td>
    <td>Casas decimais de divulgação</td><td>4</td>
  </tr>
  <tr>
    <td>Série especial?</td><td>não</td>
    <td>Valor máximo (formato europeu)</td><td>5,9038</td>
  </tr>
  <tr>
    <td>Valor mínimo (formato europeu)</td><td>0,8440</td>
    <td>Gestor proprietário</td><td>Depec</td>
  </tr>
</table>
</body></html>
"""


def test_parse_metadata_basic_returns_dataclass():
    result = parse_metadata_basic(BASIC_HTML)
    assert isinstance(result, SeriesMetadataBasic)
    assert result.series_id == 1
    assert result.name.startswith("Taxa de câmbio")
    assert result.name_abbreviated == "Dólar venda"
    assert result.theme_hierarchy == [
        "Setor externo",
        "Taxas de câmbio",
        "Diário",
    ]
    assert result.frequency == "diária"
    assert result.unit == "u.m.c./US$"
    assert result.start_date == dt.date(1984, 11, 28)
    assert result.end_date is None
    assert result.precision == 4
    assert result.special is False
    assert result.max_value == 5.9038
    assert result.min_value == 0.8440
    assert result.manager_owner == "Depec"


PAGINATION_HTML = """
<html><body>
<a href="javascript:getPagina(1)">1</a>
<a href="javascript:getPagina(2)">2</a>
<a href="javascript:getPagina(5)">5</a>
<a href="#">other</a>
</body></html>
"""


def test_get_n_pages():
    soup = BeautifulSoup(PAGINATION_HTML, "lxml")
    assert get_n_pages(soup) == 5


def test_get_n_pages_no_links_defaults_to_one():
    soup = BeautifulSoup("<html><body><p>none</p></body></html>", "lxml")
    assert get_n_pages(soup) == 1


TABLE_HTML = """
<table>
  <thead>
    <tr>
      <th>Sel.</th>
      <th>Cód.</th>
      <th>Nome completo</th>
      <th>Unid.</th>
      <th>Per.</th>
      <th>Início dd/MM/aaaa</th>
      <th>Últ. valor</th>
      <th>Fonte</th>
      <th>Esp.</th>
      <th>Met.</th>
    </tr>
  </thead>
  <tr title="some warning">
    <td>x</td><td>1</td><td>Dólar venda</td><td>u.m.c./US$</td><td>D</td>
    <td>28/11/1984</td><td>05/03/2024</td><td>BCB</td><td>N</td><td>met</td>
  </tr>
  <tr>
    <td>x</td><td>4189</td><td>Selic mensal</td><td>% a.m.</td><td>M</td>
    <td>01/04/1986</td><td>mar/2024</td><td>BCB</td><td>S</td><td>met</td>
  </tr>
</table>
"""


def test_extract_table_data_returns_grupo_series_rows():
    soup = BeautifulSoup(TABLE_HTML, "lxml")
    rows = extract_table_data(soup.find("table"))
    assert len(rows) == 2

    r1, r2 = rows
    assert r1.series_id == 1
    assert r1.name_index == "Dólar venda"
    assert r1.frequency_acronym == "D"
    assert r1.start_date == dt.date(1984, 11, 28)
    assert r1.end_date == dt.date(2024, 3, 5)
    assert r1.source == "BCB"
    assert r1.special is False
    assert r1.message == "some warning"

    assert r2.series_id == 4189
    assert r2.frequency_acronym == "M"
    assert r2.end_date == dt.date(2024, 3, 1)
    assert r2.special is True


def test_parse_date_ultimo_valor_handles_all_frequencies():
    assert parse_date_ultimo_valor(
        {"fim": "2023", "frequencia_acronimo": "A"}
    ) == dt.date(2023, 1, 1)
    assert parse_date_ultimo_valor(
        {"fim": "2º Quad. 2014", "frequencia_acronimo": "Qd"}
    ) == dt.date(2014, 8, 1)
    assert parse_date_ultimo_valor(
        {"fim": "3º Trim. 2009", "frequencia_acronimo": "T"}
    ) == dt.date(2009, 9, 1)
    assert parse_date_ultimo_valor(
        {"fim": "fev/2020", "frequencia_acronimo": "M"}
    ) == dt.date(2020, 2, 1)
    assert parse_date_ultimo_valor(
        {"fim": "01/02/2020", "frequencia_acronimo": "D"}
    ) == dt.date(2020, 2, 1)
    assert parse_date_ultimo_valor({"fim": "-", "frequencia_acronimo": "D"}) is None
