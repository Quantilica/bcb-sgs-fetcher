# Changelog

Todas as mudanças notáveis deste projeto serão documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/),
e este projeto adere ao [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [0.15.0] - 2026-11-19

### Alterado
- CI e infraestrutura alinhados ao template (fetcher-template v0.2.1).
- `catalogo sync`: o handshake com o portal (`init_session`, que também roda em cada renovação de sessão) passa a tentar 6 rodadas com espera de 15 s a 240 s em erro 5xx ou de transporte, além das tentativas rápidas da camada HTTP; erro 4xx continua falhando de imediato.
- `catalogo sync --skip-existing` considera a série pronta quando o JSON combinado existe e é válido ou quando só resta o sidecar `.manifest.json` (o arquivo de dados pode já ter sido removido depois de guardado em outro lugar). Um JSON válido sem sidecar (rodada morta entre as duas gravações) recebe o sidecar de volta; um JSON truncado é removido e a série é baixada de novo.
- `catalogo sync` encerra com código `3` (sucesso parcial) quando é interrompido por SIGTERM ou por `--max-runtime` depois de baixar ao menos uma série, e com `1` se nada novo foi baixado. Sem interrupção o código de saída não muda.

### Adicionado
- Testes: identidade do registro conferida.
- `catalogo sync --max-runtime SEGUNDOS`: ao esgotar o tempo, o passo 4 para de iniciar séries, deixa as em andamento terminarem e sai com `3`; uma nova rodada com `--skip-existing` continua de onde parou (no mesmo mês, pois o diretório de dados é particionado por mês).
- `fetch_metadata_bulk(stop_event=...)` e `metadata_is_done()`: parada cooperativa dos workers e critério único de "série já baixada".

### Corrigido
- `catalogo sync`: quando a renovação da sessão falha (por exemplo, o portal responde 5xx no `index.jsp` logo depois de um erro 500 em `cmiMetadados.jsp`), a série conta como falha e o worker segue, em vez de a exceção derrubar o pipeline inteiro. Antes da próxima série o worker espera (30 s, dobrando a cada falha seguida, no máximo 300 s) e tenta renovar de novo, para não sobrecarregar o portal. A falha ao abrir a sessão de um worker recebe o mesmo tratamento, e a falha da sessão inicial do passo 4 vira mensagem clara com saída `1`.
- SIGTERM durante o passo 4 do `catalogo sync` passa a ser tratado: os workers terminam a série em andamento e a execução sai de forma ordenada. As gravações já eram atômicas, então nenhuma interrupção deixa JSON ou HTML parcial que o `--skip-existing` tomaria por pronto.
- CI: o `uv sync` do workflow de testes deixa de passar `--index` e `--index-strategy` do índice Quantilica. O pacote não depende de nada que só exista no índice, e o GitHub Pages responde com 503 intermitente quando o caminho não existe, o que derrubava o job.

## [0.14.0] - 2026-11-14

### Alterado
- `FetcherInfo.licenca` passa a declarar a licença da fonte: ODbL (evidência: https://dadosabertos.bcb.gov.br/dataset/11-taxa-de-juros---selic).

## [0.13.0] - 2026-11-11

### Adicionado
- O fetcher declara os fatos da fonte (`FetcherInfo`: id canônico, apelidos, órgão, licença, página e cadência) para o registro de fetchers da Quantilica.

### Alterado
- Requer `quantilica-core>=0.19.0` (e `quantilica-cli>=0.17.0` como host).

### Corrigido
- `series sync` não sai mais com código 0 quando alguma série falha: sai com `3` se parte das séries baixou e parte falhou, com `1` se nenhuma baixou e com `0` sem falhas, o mesmo contrato do `sync` dos demais fetchers. Série pulada (`--skip-existing`, ou sem dados) conta como sucesso. `catalogo sync` (coleta do catálogo) continua tolerando falhas de metadados e saindo com `0`.

## [0.12.0] - 2026-11-05

### Adicionado
- `DataRepository.path_for_entry`: caminho de destino de uma série no layout de snapshots (`data/series_<id>@<AAAAMMDDTHHMMSS>.json`).
- Download de séries desativadas com disjuntor: após 3 falhas consecutivas de scraping ou de resposta inválida, o download é encerrado com aviso, em vez de insistir em todas as séries restantes.

### Alterado
- Exige `quantilica-core>=0.16.0` (antes `>=0.8.1`), que traz `path_for_entry` e `FreshnessPolicy`.
- O plugin declara a política de frescor `REMOTE_ONLY`: a API JSON não tem validadores HEAD/ETag confiáveis, então a frescura é decidida pelo conteúdo, sem sondagem remota.

## [0.11.0] - 2026-10-20

### Adicionado
- `ScraperClient.renew()`: reset completo de sessão (fecha o cliente, limpa cookies e refaz o handshake `/index.jsp`).
- `validate_metadata_iframe()`: valida os iframes de metadados (`cmiDadosBasicos`/`cmiMetadados`) antes de persistir — rejeita página vazia, de sessão expirada ou sem o marcador estrutural ("Dados básicos da série") via `SessionExpiredError`.
- `ScraperClient(shared_limiter=...)`: permite compartilhar um `RateLimiter` entre workers.
- Escrita de manifest sidecars (`.manifest.json`) para os HTMLs crus de metadados (`{id:06d}_basic.html` / `{id:06d}_full.html`) via novo `storage.save_bytes_with_manifest`, tornando-os visíveis a quem lista os arquivos pelo manifest.

### Corrigido
- **Sessão SGS ("Sessão expirada" em todo POST):** o `ScraperClient` agora mantém um cliente HTTP **persistente** (`_ensure_client`), preservando os `Set-Cookie` (`JSESSIONID`, `TS`, `SGS`) que o `/index.jsp` envia no redirect 302. Antes, o cliente transitório descartava esses cookies e todo POST a `localizarSeries.do` era tratado como sessão expirada.
- **Validação dos iframes de metadados:** `request_metadata_html` passa a rodar `validate_metadata_iframe` em cada GET, levantando `SessionExpiredError` (que aciona o retry de sessão) em vez de salvar a página de erro e só falhar no parse.
- **Layout do metadado combinado no `catalogo sync`:** o `{id:06d}.json` passa a ser escrito ao lado dos HTMLs em `bcb-sgs_{YYYY-MM}/metadata/` (`storage.write_combined_metadata`), em vez do caminho aninhado incorreto `metadata/bcb-sgs_{YYYY-MM}/metadata/` que nenhum consumidor encontrava.
- **`Series ID mismatch` (off-by-N):** quando a SGS devolve metadados de uma série diferente da solicitada, o erro agora é retentável (`bulk.SeriesMismatchError`) — renova a sessão e refaz a requisição — em vez de descartar a série definitivamente.
- Remoção de sidecars junto ao descarte de HTML inválido (`_unlink_artifact`), evitando sidecars órfãos no disco.
- Padronização de `source_id='bcb-sgs'` nos manifests de armazenamento.

### Alterado
- **Pacing global:** `fetch_metadata_bulk` usa um único `RateLimiter` compartilhado por todos os workers (cadência global, não por cliente), preservando a taxa agregada histórica (`intervalo do worker ÷ nº de workers`), com **backoff** automático na primeira expiração de sessão (freio anti-WAF).
- Renovação de sessão no worker passa a usar `renew()` (recria o cliente) em vez de re-bater em `/index.jsp`.
- Consolidação de `cli.py` em thin wrapper canônico delegando para o plugin Typer/Rich (redução de 515 para 24 LOC).
- Saneamento de pacing: substituição de 11 chamadas imperativas a `time.sleep` em `bulk.py` por taxa de cadência centralizada via `HttpClient(min_interval=...)`.

## [0.9.2] - 2026-10-02
### Adicionado
- Escrita de manifest sidecars (`.manifest.json`) em `storage.write_series_data` e `storage.write_metadata` utilizando `write_manifest_sidecar` e `DownloadManifest` de `quantilica-core>=0.8.1`, garantindo rastreabilidade de proveniência com SHA-256 para todas as séries e metadados baixados.
### Alterado
- Dependência `quantilica-core` elevada para `>=0.8.1`.

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
