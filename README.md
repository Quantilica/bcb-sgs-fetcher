# bcb-sgs-fetcher

A Python package for scraping metadata and fetching time-series data from
the Brazilian Central Bank's **SGS** (Sistema Gerenciador de Séries
Temporais).

Two data sources are exposed:

- **`api.bcb.gov.br/dados/serie/bcdata.sgs.{id}/dados`** — public JSON API
  for time-series values.
- **`www3.bcb.gov.br/sgspub`** — public website holding rich metadata
  (themes, units, descriptions, methodology). Accessed via HTML scraping.

The package returns plain Python `@dataclass` objects, has no database,
no Flask/Celery dependencies, and ships with retry logic powered by
`tenacity`.

## Install

```bash
uv add bcb-sgs-fetcher
# or
pip install bcb-sgs-fetcher
```

## Usage

### Fetch time-series data (JSON API)

```python
from bcb_sgs_fetcher import SgsDataClient

with SgsDataClient() as client:
    points = client.fetch_series_data(
        series_id=1,                # USD/BRL
        frequency_acronym="D",      # daily — uses retroactive year-by-year strategy
    )

for p in points[:3]:
    print(p.date, p.value)
```

`points` is a `list[SeriesPoint]` where each item has `series_id`, `date`,
`date_end` and `value` (`Decimal | None`).

### Scrape metadata (HTML)

```python
from bcb_sgs_fetcher import (
    ScraperClient,
    parse_metadata_basic,
    parse_metadata_full,
)

with ScraperClient() as scraper:
    htmls = scraper.request_metadata_html(series_id=1)
    basic = parse_metadata_basic(htmls["basic"])
    full = parse_metadata_full(htmls["full"])

print(basic.name, basic.frequency_acronym)
print(full.last_update, len(full.provider_data))
```

### Walk the group tree

```python
from bcb_sgs_fetcher import (
    ScraperClient,
    extract_arvore_grupos,
    extract_table_data,
    get_n_pages,
)
from bs4 import BeautifulSoup

with ScraperClient() as scraper:
    html = scraper.get_grupos_principais()
    soup = BeautifulSoup(html, "lxml")
    grupos = extract_arvore_grupos(soup.find("table"))
```

### Optional disk cache

`bcb_sgs_fetcher.storage` provides the same monthly-partitioned cache
layout used by the upstream `bcb-sgs-metadata-db`:

```python
from pathlib import Path
import datetime as dt
from bcb_sgs_fetcher import storage

month_dir = storage.get_data_dir(Path("/data/bcb-sgs"), dt.date.today())
storage.save_bytes(html_bytes, month_dir / "metadata" / "000001_basic.html")
storage.save_json(parsed_dict, month_dir / "metadata" / "000001.json")
```

## License

MIT — see [LICENSE](LICENSE).
