# Componente: Extração Adaptativa de HTML

## Responsabilidade

Detectar automaticamente o tipo de conteúdo de uma página capturada como MHTML e aplicar a estratégia de extração mais adequada — preservando a estrutura semântica dos dados em vez de converter tudo em texto plano.

Aprende com capturas anteriores: padrões de URL já classificados são armazenados por tenant e reutilizados nas capturas seguintes, evitando reprocessamento e acelerando o pipeline.

### Princípio: texto de busca sempre via trafilatura (2026-07-12)

O campo `text` do `DocumentText` — usado para fragmentação e busca semântica — é
**sempre** produzido por `extract_narrative_text()` (trafilatura), rodando uma
única vez sobre o HTML bruto, **independente** de `page_type`, de
`allow_external_llm` ou de qual caminho de extração estruturada rodou.

Toda a classificação adaptativa (análise estrutural, classificação por LLM,
parser verificado do dom2parser, extruct, extratores determinísticos por tipo)
existe **apenas** para produzir os campos `dados_estruturados_*` (ver "Mudança
de estratégia" abaixo). Nenhum desses caminhos gera mais o texto usado para
embedding.

Motivação: extratores heurísticos por tipo de página e respostas de LLM são
frágeis o suficiente para falhar silenciosamente em produzir texto completo
(ex.: um artigo classificado corretamente como `artigo`, mas cujo LLM de
primeira captura devolveu um resumo truncado ou incompleto). trafilatura é uma
biblioteca madura, testada especificamente para extração de prosa a partir de
HTML — usá-la como fonte única do texto de busca dá resiliência ao pipeline:
mesmo que a extração estruturada falhe completamente, o documento continua
pesquisável.

### `full_text`: a página inteira, sem curadoria (2026-09-17)

`extract_narrative_text()` descarta boilerplate de propósito (nav, rodapé,
menus, sidebars) para produzir prosa limpa em `text`. Esse descarte às vezes
leva junto algo que a busca precisava. `extract_full_text()` (BeautifulSoup,
sem heurística de prosa) roda em paralelo e grava a página inteira em
`DocumentText.full_text` — rede de segurança, não substituto: `text` continua
sendo o único campo usado para fragmentação/embedding. Falha em `full_text`
nunca bloqueia o pipeline (`text` vazio ainda ignora o artefato; `full_text`
vazio só fica `None`).

---

## Tipos de Página Suportados

| Tipo | Descrição | Exemplo |
|------|-----------|---------|
| `artigo` | Texto narrativo: notícias, reportagens, artigos | Folha, G1, The Intercept |
| `tabular_financeiro` | Extratos, faturas, histórico de transações | BB, Itaú, Nubank |
| `tabular_generico` | Tabelas estruturadas sem padrão financeiro | Licitações, rankings |
| `processo_judicial` | Páginas de sistemas de tribunais (CNJ) | TJSP, STJ, TRF |
| `perfil_pessoa_juridica` | Fichas cadastrais de CNPJ, sócios, atos | Receita Federal, Jucesp |
| `documento_juridico` | Contratos, petições, decisões em prosa formal | Qualquer |
| `misto` | Combinação significativa de prosa e tabelas | Relatórios anuais |
| `desconhecido` | Fallback — trafilatura com recall máximo | Qualquer |

---

## Fluxo Geral

```
extract_text_from_mhtml(artifact_id)
    │
    ├─ busca MHTML no MinIO                                 [existente]
    ├─ extrai HTML do MHTML                                 [existente]
    │
    ├─ extract_narrative_text(html)                          [SEMPRE — trafilatura]
    │       └─ campo "text" do DocumentText — independente de page_type/LLM
    │       └─ sem texto → ignora artefato (nada abaixo roda)
    │
    ├─ extract_full_text(html)                                [SEMPRE — BeautifulSoup, sem curadoria]
    │       └─ campo "full_text" do DocumentText — página inteira; falha não bloqueia o pipeline
    │
    ├─ detect_page_type(html, url, tenant, allow_external_llm)
    │       │
    │       ├─ 1. compute_structure_fingerprint(html)       [Estratégia SPA]
    │       │       └─ hash(título + headings + table headers) → 12-char hex
    │       │
    │       ├─ 2. lookup URLPatternCache
    │       │       └─ chave: (tenant, domain, path_pattern, structure_fingerprint)
    │       │       ├─ hit (confiança ≥ 0.9) → usa tipo cacheado, pula análise
    │       │       └─ hit (confiança < 0.9 ou needs_review) → segue para análise
    │       │
    │       ├─ 3. Análise estrutural determinística
    │       │       └─ classify(metrics) → (page_type, confidence)
    │       │
    │       └─ 4. [SE allow_external_llm E confidence < 0.75 OU desconhecido]
    │               ├─ dom2parser.compress(html).text       [Estratégia A]
    │               └─ llm_classify(skeleton, url)          [Estratégia B — Haiku]
    │                       └─ sobrepõe page_type + confidence se melhor
    │
    ├─ dom2parser.compress(html) roda uma única vez (reaproveitado pelas etapas acima)
    │       ├─ .text   → sempre gravado em DocumentText.dom_representation
    │       └─ .parser → ParserSpec verificado desta página          [Estratégia A′]
    │               └─ dom_parser.extract_with_spec(parser, html)
    │                       → DocumentText.dados_estruturados_dom2parser
    │
    ├─ extruct_extractor.extract(html, url)                          [sempre, sem LLM]
    │       └─ DocumentText.dados_estruturados_extruct (JSON-LD/Microdata/OpenGraph)
    │
    ├─ route(page_type, html, url, title)                            [sempre, sem LLM]
    │       ├─ tabular_financeiro  → extract_financial_table
    │       ├─ tabular_generico    → extract_generic_table
    │       ├─ processo_judicial   → extract_judicial_process
    │       ├─ perfil_pessoa_juridica → extract_company_profile
    │       ├─ documento_juridico  → extract_legal_document
    │       ├─ misto               → extract_mixed
    │       └─ artigo / desconhecido → extract_fallback (sem structured_data)
    │       └─ DocumentText.dados_estruturados_deterministico + extractor_version
    │
    ├─ [FORA deste fluxo, sob demanda] estruturar_manual(provider, model, skeleton, url)
    │       disparado manualmente pela aba "Execuções LLM" da galeria
    │       (task estruturar_llm_manual → EstruturacaoLLM), Claude ou Ollama;
    │       nunca roda dentro de extract_text_from_mhtml (2026-09-17)
    │
    ├─ DocumentText.structured_data fica None — nenhum processo decide "a vencedora"
    │       entre dom2parser/extruct/determinístico por enquanto; a lógica que
    │       atribui esse campo é um processo futuro, ainda não implementado
    │
    ├─ cria DocumentText com text (trafilatura), full_text, page_type,
    │       dados_estruturados_dom2parser, dados_estruturados_extruct,
    │       dados_estruturados_deterministico, structured_data=None
    └─ dispara fragment_text.delay(doc_text.id)
```

### Mudança de estratégia (2026-09-17): sem cascata, tudo vira campo

Até 2026-09-17, `structured_data` era decidido por uma cascata que rodava só uma
estratégia por vez: parser verificado do dom2parser → LLM (`llm_extract`, ex-
Estratégia C, se `allow_external_llm`) → extrator determinístico por
`page_type` como último recurso. Isso escondia o resultado das estratégias que
perdiam a cascata — só a vencedora ficava persistida.

Agora dom2parser (A′), extruct e o extrator determinístico rodam **sempre**,
incondicionalmente, cada um gravando o seu próprio campo
(`dados_estruturados_dom2parser`, `dados_estruturados_extruct`,
`dados_estruturados_deterministico`) — dá para comparar os três lado a lado no
visualizador para qualquer captura, não só quando um deles "ganhou". A extração
por LLM (ex-Estratégia C) **não roda mais automaticamente** dentro de
`extract_text_from_mhtml`; a função `llm_extract` que a implementava foi
removida junto com o cache de schema que a acompanhava (`extractor_config`,
`schema_driven_extract` — já haviam sido descontinuados antes, ver "Extratores
por Tipo" abaixo). Extração por LLM continua existindo, mas só sob demanda via
`estruturar_manual()` (aba "Execuções LLM" da galeria), fora do pipeline de
captura. `structured_data` continua existindo em `DocumentText`, mas fica sem
valor por enquanto: decidir qual estratégia (ou combinação) o alimenta é um
processo à parte, deixado para depois.

---

## Estratégia A — Compressão via `dom2parser`

Antes de qualquer chamada ao LLM, o HTML é comprimido para uma **representação estrutural compacta**, gerada pela biblioteca externa [`dom2parser`](https://bonafe.github.io/dom2parser/) (Python, MIT, sem dependência de LLM). A compressão reduz tipicamente 90%+ do tamanho, preservando hierarquia, cardinalidade de estruturas repetidas (ex.: linhas de tabela), assinatura de conteúdo (tipos de campo) e amostras representativas — sem perder as informações relevantes para classificação e definição de seletores.

Essa lógica antes vivia embutida no projeto (`compress_html_skeleton`, truncamento ad-hoc a 20 KB via BeautifulSoup); foi substituída pela lib externa para manter o repositório enxuto — a compressão de HTML para LLM não é lógica de domínio do OSINT, é uma ferramenta de propósito geral mantida separadamente.

### O que a compressão preserva

- Hierarquia estrutural e agrupamento de nós repetidos (ex.: `table > tr` com `count: N`)
- Assinatura de conteúdo por cluster (`DATE | TEXT | CURRENCY`, etc.)
- Amostras representativas de cada estrutura (não o conteúdo completo — ex.: algumas linhas de uma tabela de 500)

### Resultado esperado

```
Entrada : 450 KB de HTML (página de extrato bancário)
Saída   : dezenas de KB de representação estrutural (~90%+ de redução)
```

A representação comprimida (`dom2parser.compress(html).text`) é o único input enviado ao LLM. O HTML original nunca é transmitido a serviços externos. Ela também é persistida em `DocumentText.dom_representation` (Postgres) — não no MinIO, que fica reservado ao MHTML bruto — para auditoria/depuração e para reprocessar sem precisar reler o MHTML.

---

## Estratégia A′ — Parser verificado do `dom2parser` (sem LLM)

Desde a versão `8af008b` do `dom2parser` (pinado hoje em `519fd31`), `compress(html)` devolve também `.parser`: um
`ParserSpec` com, para cada estrutura repetida relevante, o **seletor de registro**, o
**locator por campo** e o **nome do campo** (lido de cabeçalhos/rótulos da própria página,
com `name_source` dizendo de onde veio). A biblioteca sintetiza o seletor por busca
medida — gera candidatos, roda contra o documento original, mantém só o que alcança
exatamente todos os registros (`verified.precision == recall == 1.0`). Seletores que
casam demais vão para `failures`, nunca viram registro.

O portal usa isso como **extrator primário de `structured_data`**
(`apps/artifacts/extractors/dom_parser.py`):

- O parser é **dado, não código**: executado por lxml via `dom2parser.parser.executor`,
  nunca por `exec()`. Roda contra o HTML **original**, nunca contra a forma compacta.
- Só registros com verificação exata são executados; linhas puladas pelo spec
  (cabeçalho) e linhas totalmente vazias são descartadas; teto de 2 000 linhas por
  registro. Um registro pode abranger vários irmãos (`span`, ex.: item do Hacker News
  = 3 `tr`; glossário = `dt` + `dd`) e cada campo indica o irmão a que é relativo
  (`sibling`). Registro sem campo descoberto rende `{"_text": "..."}`.
- Resultado: `structured_data = {"registros": {"<nome>": [{campo: valor, ...}, ...]}}`,
  `extractor_version = "dom2parser:<schema_version>"`.
- Na primeira captura de um padrão de URL sem schema, o spec é serializado em
  `URLPatternCache.extractor_config` (formato 2.0, abaixo). Capturas seguintes
  reexecutam esse mesmo parser (nomes estáveis, editáveis à mão) sem recomprimir.

```json
{
  "version": "2.0",
  "generated_by": "dom2parser",
  "generated_at": "2026-09-10T12:00:00+00:00",
  "parser": {
    "schema_version": 1,
    "records": [
      {
        "name": "tr",
        "selector": "div.lancamentos tr",
        "count": 154,
        "span": 1,
        "fields": [
          {"name": "data", "locator": "td:nth-of-type(1)", "type": "DATE", "capture": "text",
           "attribute": null, "required": false, "present": 125, "total": 154,
           "name_source": "header", "sibling": 0}
        ],
        "skip_when": {"header_values": ["Data", "Transações", "Moeda", "Valor"]},
        "verified": {"matched": 154, "expected": 154, "precision": 1.0, "recall": 1.0}
      }
    ],
    "failures": []
  }
}
```

Consequência para o LLM: quando alguém dispara extração manual (`estruturar_manual`,
abaixo) numa página onde o `dom2parser` já achou estrutura repetida verificada, o
prompt avisa ao modelo que as linhas `selector:`/`fields:` da representação já são
seletores medidos e devem ser reaproveitados, e que os caminhos truncados
(`div.x > ul > li`) não são.

---

## Estratégia B — Classificação por LLM

Ativada quando a análise estrutural determinística produz `confidence < 0.75` ou `page_type == "desconhecido"`.

### Restrições de política

A chamada ao LLM externo **só ocorre** se:
- `policy_engine.check()` retornar `allow_external_llm: true` para o tenant e o nível de classificação do artefato.
- Páginas classificadas como `restrito` ou `confidencial` nunca acionam LLM externo — usam `desconhecido` + fallback.

### Input

```
URL completa (sem query string)
Domínio normalizado
Esqueleto HTML comprimido (Estratégia A)
Lista dos tipos de página suportados + descrições
```

### Output esperado do LLM

```json
{
  "page_type": "perfil_pessoa_juridica",
  "confidence": 0.88,
  "reasoning": "Página contém CNPJ no título, tabela de duas colunas com label/valor típica de ficha cadastral e seção de sócios.",
  "hints": {
    "primary_selector": "table.dados-cadastrais",
    "key_labels": ["CNPJ", "Razão Social", "Situação"]
  }
}
```

O campo `hints` é opcional — o LLM o inclui quando consegue identificar seletores ou padrões estruturais relevantes. Hoje não alimenta nenhuma extração automática (ver "Estruturação manual" abaixo); fica disponível para quem for estruturar a página manualmente.

### Cache e custo

- A chamada ao LLM ocorre **uma única vez por padrão de URL por tenant**.
- Após gravar no `URLPatternCache` com `detection_source="llm_classification"`, capturas seguintes do mesmo padrão usam o cache diretamente.
- Custo por chamada: ~1.500–4.000 tokens (representação comprimida típica + prompt + resposta).
- Modelo recomendado: modelo de menor custo da família disponível (ex: `claude-haiku-4-5`).

---

## Estratégia C (histórico) — Extração por LLM, hoje manual

Até 2026-09-17 esta estratégia rodava automaticamente na primeira captura de um
padrão de URL (com `allow_external_llm=True` e sem registros do parser
verificado): o LLM categorizava a página, extraía `structured_data` e gerava um
schema de seletores CSS (`URLPatternCache.extractor_config`) para reuso nas
capturas seguintes via `schema_driven_extract` — já removido antes desta
mudança (ver commit "remove reuso de schema em cache na extração de
structured_data"). A função que fazia a chamada (`llm_extract`) foi removida
junto com a troca de estratégia deste documento; `URLPatternCache.extractor_config`
permanece no modelo só como campo zerado em divergências (ver "Cache de
Padrões de URL" abaixo), sem uso na extração.

Hoje a extração por LLM só existe sob demanda: `estruturar_manual()`
(`apps/artifacts/extractors/estruturacao_manual.py`), disparada pela aba
"Execuções LLM" da galeria (task `estruturar_llm_manual`, modelo
`EstruturacaoLLM`). Aceita Claude ou Ollama, usa o mesmo prompt de extração
(`_EXTRACT_SYSTEM` em `llm_common.py`) e o mesmo esqueleto comprimido via
dom2parser (Estratégia A) como input — mas nunca grava schema nem é chamada
automaticamente pelo pipeline de captura. `LLM_EXTRACTOR_MODEL` continua sendo
o modelo padrão pré-selecionado nessa aba.

### Limitações resolvidas (2026-07-02)

Duas limitações registradas em 2026-06-03 foram corrigidas:

1. **Truncamento do esqueleto**: o corte de 20 KB era um slice cego de bytes que
   podia cortar a tabela de lançamentos no meio de uma linha. Tabelas com mais de
   40 linhas passaram a ser amostradas (20 do início + 15 do fim + marcador de
   omissão) antes da serialização, e o corte final recuava até a última tag completa.
   **Superado em 2026-09-08**: todo esse mecanismo caseiro (`compress_html_skeleton`,
   truncamento a 20 KB, amostragem de linhas) foi substituído pela biblioteca externa
   [`dom2parser`](https://bonafe.github.io/dom2parser/), que resolve o mesmo problema
   de forma mais geral (clusterização + amostragem representativa) sem cap fixo de
   tamanho — ver Estratégia A acima.
2. **Schema sem validação**: o schema gerado pelo LLM era gravado sem teste.
   Agora é validado contra o próprio HTML da captura antes de ser gravado.

---

## Extração bruta por biblioteca — dom2parser, extruct, determinístico (2026-09-17)

`DocumentText` guarda o resultado de cada estratégia de extração estrutural que
roda sobre o HTML, cada uma no seu próprio campo, **sem** uma cascata de decisão
escolhendo uma vencedora entre elas (ver "Mudança de estratégia" no topo deste
documento). Servem para comparar cobertura/qualidade entre estratégias e
depurar sem reprocessar o MHTML — não alimentam busca, embeddings nem nenhum
consumidor além do visualizador.

| Campo | Fonte | O que captura |
|---|---|---|
| `dados_estruturados_dom2parser` | [`dom2parser`](https://bonafe.github.io/dom2parser/) (Estratégia A′) | Estruturas repetidas inferidas por padrão do DOM (tabelas, listas) — `{"registros": {...}}` |
| `dados_estruturados_extruct` | [`extruct`](https://github.com/scrapinghub/extruct) | Metadados que o próprio site declara: JSON-LD, Microdata, OpenGraph e Microformats (schema.org Organization/Person/Article/Product, meta tags sociais). RDFa fica de fora — nesta lib ele confunde `role=` de acessibilidade com metadado de conteúdo (medido: 262 itens de ruído puro numa única página) |
| `dados_estruturados_deterministico` | Extrator hardcoded por `page_type` (`route()`, ver "Extratores por Tipo" abaixo) | Formato específico por tipo — ex.: `{"tipo": "tabular_financeiro", "transacoes": [...]}` |
| `structured_data` | Nenhuma por enquanto | Campo final que o resto do app consumiria; decisão de qual estratégia (ou combinação) o alimenta é um processo à parte, ainda não implementado |

Todas rodam de forma determinística, sem LLM e sem rede, em **toda captura**,
incondicionalmente — independente de `page_type` ou `allow_external_llm`
(`apps/artifacts/tasks.py`, logo após `dom2parser.compress(html)`):

- `dados_estruturados_dom2parser` reaproveita a mesma execução de
  `dom_parser.extract_with_spec()` usada na Estratégia A′ — não há segunda passada
  pelo HTML só para preencher este campo.
- `dados_estruturados_extruct` (`apps/artifacts/extractors/extruct_extractor.py`)
  é uma extração independente; como muitos sites não declaram nenhum dos formatos
  suportados, o campo fica `null` na maioria das capturas — isso é esperado, não
  uma falha. Quando não é `null`, tem a forma `{"resumo": {...}, "raw": {...}}`:
  `resumo` são os campos comuns (title/description/image/url/site_name/type/
  locale) já achatados, do primeiro formato disponível na ordem json-ld >
  microdata > opengraph; `raw` é a saída crua de cada formato encontrado, para
  quem quiser inspecionar o que o `resumo` deixou de fora.
- `dados_estruturados_deterministico` vem do mesmo `route()` que antes só rodava
  como último recurso da cascata — agora roda sempre, e seu `extractor_version`
  (ex.: `financial_table:1.0`, `fallback:1.0`) é o que fica em
  `DocumentText.extractor_version`.

Complementares por natureza: `dom2parser` infere estrutura por repetição no DOM
mesmo sem marcação alguma; `extruct` só lê o que o publicador anotou
explicitamente; o determinístico aplica regras específicas por `page_type`
(colunas de extrato, número CNJ, etc.) que nenhuma das outras duas conhece.
Nenhuma substitui a outra.

---

## Algoritmo de Detecção Estrutural (existente)

### Métricas coletadas do HTML (via BeautifulSoup)

| Métrica | Descrição |
|---------|-----------|
| `table_count` | Número de `<table>` no documento |
| `table_row_count` | Total de `<tr>` em todas as tabelas |
| `table_char_count` | Caracteres dentro de células de tabela |
| `text_char_count` | Caracteres em `<p>`, `<li>`, `<article>`, `<section>` fora de tabelas |
| `table_ratio` | `table_char_count / (table_char_count + text_char_count)` |
| `monetary_count` | Matches de `R\$\s*[\d.,]+` |
| `date_count` | Matches de `\d{2}/\d{2}/\d{4}` ou variantes ISO |
| `process_number_count` | Matches do padrão CNJ: `\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}` |
| `cnpj_count` | Matches de `\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}` |
| `has_article_tag` | Presença de `<article>` |
| `has_main_tag` | Presença de `<main>` |
| `paragraph_count` | Número de `<p>` com mais de 50 caracteres |

### Regras de classificação (em ordem de prioridade)

```
1. process_number_count ≥ 1
       → processo_judicial (confiança: 0.95)

2. table_ratio > 0.55 AND monetary_count > 5 AND date_count > 5
       → tabular_financeiro (confiança: 0.90)

3. cnpj_count ≥ 1 AND table_ratio < 0.5 AND paragraph_count < 10
       → perfil_pessoa_juridica (confiança: 0.85)

4. table_ratio > 0.50 AND table_row_count > 8
       → tabular_generico (confiança: 0.80)

5. (has_article_tag OR has_main_tag) AND paragraph_count > 5 AND table_ratio < 0.25
       → artigo (confiança: 0.85)

6. paragraph_count > 20 AND table_ratio < 0.15
       → documento_juridico (confiança: 0.75)

7. table_ratio > 0.20 AND paragraph_count > 10
       → misto (confiança: 0.65)

8. fallback
       → desconhecido (confiança: 0.50)
```

---

## Cache de Padrões de URL

### Normalização de URL

1. Strip de protocolo, query string e fragment
2. Segmentos do path que parecem IDs são substituídos por `*`:
   - UUIDs, números puros (≥3 dígitos), datas ISO
3. Resultado: `domínio/path/normalizado/*`

**Exemplos:**

| URL original | Padrão normalizado |
|---|---|
| `https://bb.com.br/extrato/12345678/2024-03` | `bb.com.br/extrato/*/*` |
| `https://esaj.tjsp.jus.br/cpopg/show.do?processo.codigo=AB0001` | `esaj.tjsp.jus.br/cpopg/show.do` |
| `https://servicos.receita.fazenda.gov.br/Servicos/cnpjreva/Cnpjreva_Solicitacao.asp` | `servicos.receita.fazenda.gov.br/Servicos/cnpjreva/Cnpjreva_Solicitacao.asp` |

### Comportamento do cache

| Situação | Ação |
|----------|------|
| Hit, confidence ≥ 0.9, `needs_review=False` | Retorna tipo cacheado; incrementa `hit_count`; pula análise |
| Hit, confidence < 0.9 ou `needs_review=True` | Roda análise estrutural para confirmar; compara |
| Miss | Análise estrutural → se confidence < 0.75, aciona classificação por LLM (Estratégia B) → cria registro |
| Divergência (tipo diferente do cache) | Incrementa `divergence_count` |
| `divergence_count ≥ 3` | `needs_review=True`; `extractor_config` zerado |

Este cache decide só `page_type` — não decide mais extração de `structured_data`
(ver "Estratégia C (histórico)" acima): `extractor_config`, `schema_failure_count`
seguem no modelo por compatibilidade de dados existentes, mas nada os lê ou
escreve fora da linha de `divergence_count` acima.

O cache é **isolado por tenant**: organização A nunca acessa o cache da organização B.

---

## Modelo de Dados

### `URLPatternCache`

```python
class URLPatternCache(models.Model):
    id                   = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant               = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE,
                                             related_name="url_pattern_caches")
    domain               = models.CharField(max_length=255, db_index=True)
    path_pattern         = models.CharField(max_length=1024)
    structure_fingerprint = models.CharField(max_length=32, default="")
    # hash(título + headings + table headers com números removidos)
    # distingue telas de SPAs que compartilham a mesma URL (ex: internet banking)
    page_type            = models.CharField(max_length=50)
    confidence           = models.FloatField()
    detection_source     = models.CharField(max_length=30, default="structural_analysis")
    # "structural_analysis" | "llm_classification"
    extractor_config     = models.JSONField(default=dict)
    # Não alimenta mais extração de structured_data (ver "Estratégia C —
    # histórico" acima) — só existe hoje para ser zerado quando
    # divergence_count estoura o limite abaixo.
    hit_count            = models.PositiveIntegerField(default=1)
    divergence_count     = models.PositiveIntegerField(default=0)
    schema_failure_count = models.PositiveIntegerField(default=0)
    # Não atualizado por nenhum processo hoje — sobra do schema-caching removido.
    needs_review         = models.BooleanField(default=False)
    last_seen_at         = models.DateTimeField(auto_now=True)
    created_at           = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("tenant", "domain", "path_pattern", "structure_fingerprint")]
        indexes = [Index(fields=["tenant", "domain"])]
```

### Campos do `content` do Artifact TEXT filho

```json
{
  "text": "...",
  "page_type": "tabular_financeiro",
  "detection_confidence": 0.90,
  "detection_source": "cache | structural_analysis | llm_classification",
  "url_pattern_cache_id": "uuid-do-cache",
  "extractor_version": "financial_table:1.0 | fallback:1.0 | ...",
  "structured_data": null,
  "dados_estruturados_dom2parser": { ... },
  "dados_estruturados_extruct": { ... },
  "dados_estruturados_deterministico": { ... },
  "char_count": 1234,
  "word_count": 234
}
```

---

## Extratores por Tipo (determinísticos)

Rodam **sempre**, em toda captura, independente de cache ou de qualquer outra
estratégia (ver "Mudança de estratégia" no topo deste documento) — não são mais
um fallback de último recurso. **Produzem apenas `dados_estruturados_deterministico`
e `extractor_version`** — o campo `text` do `DocumentText` nunca vem daqui; é sempre
`extract_narrative_text()` (trafilatura), calculado uma única vez antes da detecção
de tipo (ver "Princípio: texto de busca sempre via trafilatura").

### `artigo`
- **dados_estruturados_deterministico:** `null` (não há extração estruturada específica para prosa)

### `tabular_financeiro`
- **Lib:** BeautifulSoup + heurística de mapeamento de colunas por header
- **dados_estruturados_deterministico:**
  ```json
  {"tipo": "tabular_financeiro", "transacoes": [{"data": "...", "descricao": "...", "valor": -150.0, "saldo": 2340.5}]}
  ```

### `tabular_generico`
- **Lib:** BeautifulSoup
- **dados_estruturados_deterministico:** `{"tipo": "tabular_generico", "tabelas": [{"cabecalho": [...], "linhas": [...]}]}`

### `processo_judicial`
- **Lib:** BeautifulSoup + regex CNJ
- **dados_estruturados_deterministico:**
  ```json
  {"tipo": "processo_judicial", "numero_cnj": "...", "classe": "...", "assunto": "...", "partes": {...}, "movimentacoes": [...]}
  ```

### `perfil_pessoa_juridica`
- **Lib:** BeautifulSoup — varre `<dl>` e tabelas 2-colunas
- **dados_estruturados_deterministico:** `{"tipo": "perfil_pessoa_juridica", "cnpj": "...", "campos": {...}}`

### `documento_juridico`
- **dados_estruturados_deterministico:** `{"tipo": "documento_juridico"}` (marcador de classificação — sem extração de campos)

### `misto`
- **Lib:** BeautifulSoup (tabelas)
- **dados_estruturados_deterministico:** `{"tipo": "misto", "tabelas": [...]}`

### `desconhecido`
- **dados_estruturados_deterministico:** `null`

---

## Observabilidade

Cada item abaixo é hoje um **evento persistido** no log do pipeline, não apenas uma linha de log de contêiner — ver [`../observabilidade.md`](../observabilidade.md). Em particular, `extracao.dom2parser` distingue `falhou` (a biblioteca quebrou) de `vazio` (rodou e nenhum registro atingiu `precision/recall == 1.0`), com o motivo e as contagens no payload. Antes disso, os dois casos chegavam ao banco como o mesmo `NULL`.

Campos registrados a cada extração:

- `page_type` detectado
- `detection_source` (`cache`, `structural_analysis`, `llm_classification`)
- `detection_confidence`
- `extractor_version` (do extrator determinístico — a única estratégia que ainda versiona assim)
- `dom_representation` gerado/persistido (bool), sua redução percentual e o número de registros verificados / falhas do parser (`dom2parser`)
- registros e linhas extraídos pelo parser verificado (Estratégia A′) e se produziu dados (`extracao.dom2parser`)
- se `dados_estruturados_extruct` produziu algo (`extracao.extruct`)
- se `dados_estruturados_deterministico` produziu algo (`extracao.deterministico`)
- `llm_model` (quando Estratégia B, classificação de `page_type`, é ativada)
- `divergence` (se houve divergência com cache)
- Tempo de extração em ms
- Nó e processo que executaram (`hostname`/`process_id`) — relevante com mais de um worker

---

## Evolução Futura

- **Confirmação humana:** interface no admin Django para revisar padrões marcados como `needs_review` e corrigir `page_type`.
- **Extratores por tribunal específico:** TJSP, TJRJ, STJ, TRF têm layouts diferentes — extratores dedicados por `domain` quando `page_type == processo_judicial`, complementando `route()`.
- **Decisão de `structured_data`:** processo que escolhe (ou combina) entre `dados_estruturados_dom2parser`, `dados_estruturados_extruct`, `dados_estruturados_deterministico` e a estruturação manual por LLM — deixado para depois, ver "Mudança de estratégia" no topo deste documento.

---

## Critérios de Aceitação

- [ ] Extrato bancário detectado como `tabular_financeiro` na primeira captura sem configuração manual.
- [x] `dados_estruturados_deterministico` com lista de transações gerado corretamente para página financeira (`route()` roda sempre).
- [ ] Processo judicial do TJSP detectado como `processo_judicial` com `numero_cnj` extraído.
- [ ] Artigo de notícia detectado como `artigo`, comportamento atual preservado sem regressão.
- [ ] Segunda captura do mesmo padrão de URL usa `detection_source: cache` e não roda análise estrutural.
- [x] Página com `confidence < 0.75` aciona compressão via `dom2parser` (A) e classificação por LLM (B).
- [x] Página classificada como `restrito` ou `confidencial` **não** aciona LLM externo.
- [x] `DocumentText.dom_representation` é persistido a cada extração, independente do caminho de extração seguido.
- [x] Página com estruturas repetidas (extrato, listagem) tem `dados_estruturados_dom2parser.registros` extraído pelo parser verificado do `dom2parser`, sem chamada a LLM (A′).
- [x] `dom2parser`, `extruct` e o extrator determinístico rodam sempre, cada um no seu próprio campo `dados_estruturados_*`, sem cascata escolhendo uma vencedora (2026-09-17).
- [x] Extração por LLM (ex-Estratégia C) não roda mais automaticamente dentro de `extract_text_from_mhtml`; só existe sob demanda via `estruturar_manual()` (2026-09-17).
- [ ] Processo que decide `DocumentText.structured_data` a partir das estratégias acima (deixado para depois — ver "Mudança de estratégia").
- [x] ~~O `ParserSpec` é gravado em `extractor_config`... e reexecutado via `schema_driven_extract`~~ — **obsoleto**: schema-caching de `structured_data` foi removido (commit "remove reuso de schema em cache na extração de structured_data"); `dom_parser.extract_with_spec` roda a cada captura, sem cache.
- [x] ~~LLM (C) só é acionado quando o parser verificado não produziu registros~~ — **obsoleto**: LLM não é mais acionado automaticamente em nenhuma condição (2026-09-17).
- [ ] ~~`extractor_config` é gerado e gravado na primeira extração pós-classificação por LLM (C)~~ — **obsoleto**, ver acima.
- [ ] ~~Segunda captura usa `schema_driven_extract` sem nova chamada ao LLM~~ — **obsoleto**, ver acima.
- [ ] Seletor inválido no parser do `dom2parser` produz warning sem interromper extração dos demais campos.
- [x] Mudança de layout (divergência) registrada em `divergence_count`; após 3 divergências, `needs_review=True` e `extractor_config` é zerado (afeta só o cache de `page_type`, não mais extração de dados).
- [ ] Cache isolado por tenant: organização A não acessa registros da organização B.
- [ ] Página não reconhecida usa `desconhecido` sem lançar exceção.
- [ ] `ArtifactLineage.processor` identifica o extrator e sua versão.
- [x] Todos os campos de observabilidade registrados a cada extração, como eventos persistidos e consultáveis.
- [x] `DocumentText.text` é sempre produzido por `extract_narrative_text()` (trafilatura), inclusive quando `page_type` é `tabular_financeiro`, `processo_judicial` ou quando alguém estrutura a página manualmente com LLM.
- [x] ~~`llm_extract()` não retorna mais campo `text`~~ — **obsoleto**: `llm_extract()` foi removido (2026-09-17); a extração manual (`estruturar_manual()`) nunca teve esse campo.
- [x] Falha total de qualquer estratégia estruturada resulta em campo `null`, mas nunca em `DocumentText.text` vazio se a página tiver conteúdo extraível por trafilatura.
- [x] `DocumentText.full_text` é persistido a cada extração com o texto integral da página (sem curadoria do trafilatura); falha em `extract_full_text()` não interrompe o pipeline.
