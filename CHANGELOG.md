# Changelog

## [0.9.1] - 2026-10-02
### Corrigido
- Renovação transparente de sessão HTTP (`JSESSIONID`) no `ScraperClient` (`fetch_validated`) com detecção de redirecionamento, status 401/403/419/440 e marcadores de sessão expirada do SGS, com retries automáticos.
- Headers HTTP atualizados com User-Agent de navegador moderno, `Accept`, `Referer` e `Accept-Language: pt-BR` seguro contra HTTP 406.
- Validação de conteúdo HTML antes de persistir em cache em `bulk.py` (`_parse_validated_html`), descartando respostas inválidas e expurgando cache corrompido.
- Extração resiliente de IDs em `extract_ids_from_data_dir` com fallback linha a linha e mensagens diagnósticas detalhadas no Passo 3/4 do `catalogo sync`.
- Correção de bugs latentes no `ScraperClient` (`scraper.session` inexistente em `fetch_metadata_bulk` e `_transport` em `get_scraper`).

## [0.9.0] - 2026-08-30
### Alterado
- Migração de `httpx` para `httpx2` (fork mantido pelo Pydantic, API idêntica) em `ScraperClient`, `data.py` e testes; `ScraperClient` agora aceita transportes `httpx2`.
- Dependência `quantilica-core` elevada para `>=0.6.0` (versão que migra para `httpx2`).

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
