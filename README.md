# bcb-sgs-fetcher: Coletor de séries temporais do BCB SGS

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square) ![Python](https://img.shields.io/badge/python-3.12+-blue.svg?style=flat-square)

Biblioteca Python para download de dados e metadados do **SGS** (Sistema Gerenciador de Séries Temporais) do Banco Central do Brasil. Expõe dois clientes independentes: um para a API JSON pública e outro para raspagem HTML do portal SGS.

**Fonte dos dados:** [BCB SGS — Sistema Gerenciador de Séries Temporais](https://www.bcb.gov.br/estatisticas/tabelaestatistica)

## Instalação

```bash
pip install git+https://github.com/Quantilica/bcb-sgs-fetcher.git
```

## Uso Rápido

### Buscar dados de uma série temporal

```python
from bcb_sgs_fetcher import SgsDataClient

with SgsDataClient() as client:
    points = client.fetch_series_data(
        series_id=1,            # Dólar/Real (USD/BRL)
        frequency_acronym="D",  # Diária — usa estratégia retroativa ano a ano
    )

for p in points[:3]:
    print(p.date, p.value)
```

`points` é uma `list[SeriesPoint]` onde cada item contém `series_id`, `date`,
`date_end` e `value` (`Decimal | None`).

### Buscar metadados de uma série

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

## CLI

Os comandos são agrupados em dois eixos: `series` (operações por série) e
`catalogo` (catálogo de metadados).

### Via quantilica-cli

```bash
# Baixar dados de uma série
quantilica fetch bcb-sgs series sync 1 -f D -o ./dados

# Baixar metadados de uma série
quantilica fetch bcb-sgs series metadata 1 -o ./dados

# Buscar séries por texto
quantilica fetch bcb-sgs series search "câmbio"

# Sincronizar o catálogo completo de metadados
quantilica fetch bcb-sgs catalogo sync
```

### CLI standalone

```bash
# Baixar dados de uma série
bcb-sgs-fetcher series sync 1 --frequency D --output ./dados

# Baixar metadados de uma série específica
bcb-sgs-fetcher series metadata 1 --output ./dados

# Buscar séries por texto
bcb-sgs-fetcher series search "taxa selic"
```

### Sincronização completa do catálogo de metadados

Para baixar e processar metadados de **todas** as séries do SGS, use o
comando `catalogo sync`:

```bash
bcb-sgs-fetcher catalogo sync -o /data/bcb-sgs
```

Ele executa automaticamente os quatro passos em sequência, cada um com sua
própria sessão HTTP:

1. Baixa a árvore de grupos e as listagens de séries por grupo
2. Baixa as páginas de séries desativadas
3. Extrai todos os IDs dos HTMLs baixados
4. Baixa e parseia os metadados de cada série

Todos os dados são gravados em `<output>/bcb-sgs_YYYY-MM/`. O pipeline é
**retomável**: arquivos já existentes no disco são ignorados em todos os
passos, então basta reexecutar o mesmo comando após uma interrupção.

Para ajustar o intervalo entre requisições (padrão: 10 segundos):

```bash
bcb-sgs-fetcher catalogo sync -o /data/bcb-sgs --sleeptime 5
```

#### Passos individuais

Se precisar executar um passo isoladamente (ex.: após corrigir falhas
parciais), os subcomandos individuais aceitam os mesmos parâmetros:

```bash
# Apenas a árvore de grupos
bcb-sgs-fetcher catalogo arvore-grupos -o /data/bcb-sgs

# Apenas séries desativadas
bcb-sgs-fetcher catalogo series-desativadas -o /data/bcb-sgs

# Apenas extração de IDs (grava em <output>/bcb-sgs_YYYY-MM/ids.txt)
bcb-sgs-fetcher catalogo extract-ids -o /data/bcb-sgs

# Apenas metadados, a partir de um arquivo de IDs
bcb-sgs-fetcher catalogo metadata-bulk \
    --ids-file /data/bcb-sgs/bcb-sgs_YYYY-MM/ids.txt \
    -o /data/bcb-sgs
```

## API Python

### Navegar a árvore de grupos

```python
from bcb_sgs_fetcher import ScraperClient, extract_arvore_grupos, extract_table_data
from bs4 import BeautifulSoup

with ScraperClient() as scraper:
    html = scraper.get_grupos_principais()
    soup = BeautifulSoup(html, "lxml")
    grupos = extract_arvore_grupos(soup.find("table"))
```

### Cache em disco

`bcb_sgs_fetcher.storage` oferece layout de cache particionado por mês:

```python
from pathlib import Path
import datetime as dt
from bcb_sgs_fetcher import storage

month_dir = storage.get_data_dir(Path("/data/bcb-sgs"), dt.date.today())
storage.save_bytes(html_bytes, month_dir / "metadata" / "000001_basic.html")
storage.save_json(parsed_dict, month_dir / "metadata" / "000001.json")
```

## Fontes de Dados

| Cliente | URL | Tipo |
| :--- | :--- | :--- |
| `SgsDataClient` | `api.bcb.gov.br/dados/serie/bcdata.sgs.{id}/dados` | API JSON pública |
| `ScraperClient` | `www3.bcb.gov.br/sgspub` | Raspagem HTML |

## Desenvolvimento

```bash
git clone https://github.com/Quantilica/bcb-sgs-fetcher.git
cd bcb-sgs-fetcher
uv sync --dev
uv run pytest
```

## Licença

MIT — veja [LICENSE](LICENSE).
