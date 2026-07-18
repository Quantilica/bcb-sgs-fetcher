# bcb-sgs-fetcher: Coletor de séries temporais do BCB SGS

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square) ![Python](https://img.shields.io/badge/python-3.12+-blue.svg?style=flat-square)

Biblioteca Python para download de dados e metadados do **SGS** (Sistema Gerenciador de Séries Temporais) do Banco Central do Brasil. Expõe dois clientes independentes: um para a API JSON pública e outro para raspagem HTML do portal SGS.

**Fonte dos dados:** [BCB SGS — Sistema Gerenciador de Séries Temporais](https://www.bcb.gov.br/estatisticas/tabelaestatistica)

## Instalação

```bash
pip install bcb-sgs-fetcher
```

Com [uv](https://github.com/astral-sh/uv):

```bash
uv add bcb-sgs-fetcher
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

print(basic.name, basic.frequency)
print(full.last_update, len(full.provider_data))
```

## CLI

Os comandos são agrupados em dois eixos: `series` (operações por série) e
`catalogo` (catálogo de metadados).

### Via quantilica-cli

```bash
# Baixar dados de uma série
quantilica bcb-sgs series sync 1 -f D -o ./dados

# Baixar metadados de uma série
quantilica bcb-sgs series metadata 1 -o ./dados

# Buscar séries por texto
quantilica bcb-sgs series search "câmbio"

# Sincronizar o catálogo completo de metadados
quantilica bcb-sgs catalogo sync
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

### Baixar dados das séries

Por padrão, `series sync` baixa os **dados** (a série temporal) de **todas** as
séries — enumeradas a partir das listagens já baixadas pelo `catalogo sync`. A
periodicidade de cada série é lida das listagens, então séries diárias já usam a
estratégia retroativa automaticamente. O download é **concorrente**
(`--workers`) e **retomável** (`--skip-existing`).

```bash
# Todas as séries (padrão) — requer um catálogo já sincronizado
bcb-sgs-fetcher series sync --skip-existing --workers 5 -o /data/bcb-sgs

# Estreitando: apenas uma série
bcb-sgs-fetcher series sync 1 --frequency D -o /data/bcb-sgs

# Estreitando: apenas os IDs de um arquivo
bcb-sgs-fetcher series sync \
    --ids-file /data/bcb-sgs/bcb-sgs_YYYY-MM/ids.txt \
    --skip-existing --workers 5 -o /data/bcb-sgs
```

Por padrão as listagens são procuradas em `<output>/bcb-sgs_YYYY-MM`; use
`--catalog-dir` para apontar outro mês. Os dados são gravados em
`<output>/data/series_{id}@YYYYMMDDTHHMMSS.json` (nome **versionado** por
data-hora — cada coleta gera um snapshot novo, então re-baixar a mesma série no
mesmo dia **não sobrescreve** a anterior). `--skip-existing` pula séries que já
têm um snapshot do dia. Use `--period latest` para baixar só as últimas 20
observações.

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

`bcb_sgs_fetcher.storage` é a fonte única do layout em disco do ecossistema
bcb-sgs (o `bcb-sgs-sql` consome este mesmo módulo). É construído sobre
`quantilica-core` (escrita atômica, `stamp_filename`, `StampedDataRepository`).

```python
from pathlib import Path
from bcb_sgs_fetcher import storage

root = Path("/data/bcb-sgs")

# Observações: snapshot versionado por data-hora (não sobrescreve)
storage.write_series_data(root, 1, rows)          # data/series_1@...T....json
latest = storage.latest_series_file(root, 1)      # snapshot mais recente
rows = storage.read_series_data(latest)

# Metadados particionados por mês (combinado + HTML bruto)
storage.write_metadata(root, 1, basic=b, full=f, html_basic=hb, html_full=hf)
combined = storage.read_combined_metadata(root, 1)  # {"basic": ..., "full": ...}
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
