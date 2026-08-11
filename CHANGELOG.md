# Changelog

## [0.8.1] - 2026-08-10
### Corrigido
- Atualizada dependência `quantilica-core` para `>=0.5.0` devido à exigência do parâmetro `data` no `HttpClient.request`.

## [0.8.0] - 2026-08-10
### Alterado
- Migração completa da CLI para utilização da SDK unificada (`BcbSgsFetcherApp` estendendo `FetcherApp`).
- Remoção do encapsulamento `ScraperClient` proprietário em favor do `HttpClient` do `quantilica-core`.
### Removido
- Removido `DEFAULT_OUTPUT_DIR` disperso em arquivo genérico `constants.py`.

## [0.7.0] - 2026-08-07
### Alterado
- Refatoração arquitetural: Remoção de dependências (`quantilica-cli` e `quantilica-catalog`) e limpeza de imports. Os fetchers agora são pacotes de extração puros, dependendo estritamente do `quantilica-core`.

Todas as mudanças notáveis deste projeto serão documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/),
e este projeto adere ao [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [0.5.0] - 2026-07-18

### Corrigido

- **`series search` (CLI standalone) quebrava com `AttributeError`** — `cli.py` usava
  `row.series_name`, campo inexistente em `GrupoSeriesRow` (o correto é `name_index`).
- Exemplo do README usava `basic.frequency_acronym` (inexistente em
  `SeriesMetadataBasic`) — corrigido para `basic.frequency`.

### Alterado

- Dependência de `quantilica-core` trocada de `git+https://...` para
  `quantilica-core>=0.3.1` (versão publicada no PyPI). `typer`/`rich` (usados pelo
  `plugin.py`) são fornecidos pelo host `quantilica-cli`, não declarados pelo fetcher.
- `httpx>=0.28.1` agora declarado diretamente (é importado em `data.py`/`scraper.py`;
  antes chegava só transitivamente).
- Removido `[tool.hatch.metadata] allow-direct-references` (não há mais dep git).

### Adicionado

- `py.typed` (marcador de pacote tipado) + classifier `Typing :: Typed`.
- Metadados PEP 639 de licença (`license = "MIT"` + `license-files`).
- Configuração de `ruff` (`E/F/I/UP/B`) e `pytest`.
- Workflows de CI (teste com `uv` + `ruff` + `pytest`) e de publicação via
  Trusted Publishing (OIDC).
