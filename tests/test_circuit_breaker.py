"""Tests for the scraping circuit breaker in bcb-sgs-fetcher."""

from pathlib import Path
from unittest.mock import MagicMock

from bcb_sgs_fetcher.bulk import fetch_series_desativadas

_PAGE_1_HTML = """
<html><body>
<table id="tabelaSeries">
  <thead><tr><th>Cód.</th></tr></thead>
  <tbody><tr><td>1</td></tr></tbody>
</table>
<a href="javascript:getPagina(10)">10</a>
</body></html>
""".encode("latin-1")


def test_circuit_breaker_trips_after_three_failures(tmp_path: Path):
    scraper = MagicMock()
    scraper.min_interval = 0.0
    scraper.get_series_desativadas.return_value = _PAGE_1_HTML
    # Fails repeatedly on change_page
    scraper.change_page.side_effect = RuntimeError("WAF block")

    # Should not hang or loop through 10 pages: trips after 3 consecutive failures
    fetch_series_desativadas(scraper, tmp_path, sleeptime=0)

    assert (tmp_path / "series-desativadas_001.html").is_file()
    assert not (tmp_path / "series-desativadas_002.html").exists()
    assert not (tmp_path / "series-desativadas_005.html").exists()
    # Scraper change_page called at most 3 times before circuit breaker tripped
    assert scraper.change_page.call_count == 3
