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
extração+schema por LLM, extratores determinísticos por tipo,
`schema_driven_extract`) existe **apenas** para produzir `structured_data`.
Nenhum desses caminhos gera mais o texto usado para embedding.

Motivação: extratores heurísticos por tipo de página e respostas de LLM são
frágeis o suficiente para falhar silenciosamente em produzir texto completo
(ex.: um artigo classificado corretamente como `artigo`, mas cujo LLM de
primeira captura devolveu um resumo truncado ou incompleto). trafilatura é uma
biblioteca madura, testada especificamente para extração de prosa a partir de
HTML — usá-la como fonte única do texto de busca dá resiliência ao pipeline:
mesmo que a extração estruturada falhe completamente, o documento continua
pesquisável.

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
    │
    ├─ 1. [SE cache_obj.extractor_config presente] → schema_driven_extract(html, config)   [capturas 2+]
    │       ├─ formato 2.0 (generated_by: dom2parser) → executor lxml do dom2parser
    │       ├─ formato 1.0 (LLM) → seletores CSS interpretados via BeautifulSoup
    │       └─ não extraiu nada → _update_schema_health (2 falhas → zera config) e segue
    │
    ├─ 2. [SE ainda sem dados E .parser tem registros verificados] → dom_parser.extract_with_spec
    │       ├─ structured_data = {"registros": {nome: [linhas]}}  (sem LLM, sem rede)
    │       └─ cache sem extractor_config → grava ParserSpec serializado (formato 2.0)
    │
    ├─ 3. [SE ainda sem dados E allow_external_llm E cache sem extractor_config] → 1ª captura com LLM
    │       ├─ (reaproveita dom2parser.compress(html).text acima)  [Estratégia A]
    │       ├─ llm_extract_and_schema(skeleton, url, hint)  [Estratégia C — Sonnet]
    │       │       └─ retorna: categoria + page_type + structured_data + schema (sem texto)
    │       ├─ usa structured_data como resultado da extração (imediato)
    │       └─ grava schema em URLPatternCache.extractor_config (formato 1.0)
    │
    ├─ 4. [ELSE] extrator determinístico por tipo (fallback)
    │               ├─ tabular_financeiro  → extract_financial_table
    │               ├─ tabular_generico    → extract_generic_table
    │               ├─ processo_judicial   → extract_judicial_process
    │               ├─ perfil_pessoa_juridica → extract_company_profile
    │               ├─ documento_juridico  → extract_legal_document
    │               ├─ misto               → extract_mixed
    │               └─ artigo / desconhecido → extract_fallback (sem structured_data)
    │       (todos os caminhos acima só alimentam structured_data + extractor_version —
    │        o texto já foi definido no passo extract_narrative_text, no topo)
    │
    ├─ cria DocumentText com text (trafilatura), page_type, structured_data, extractor_version
    └─ dispara fragment_text.delay(doc_text.id)
```

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

Consequência para o LLM: a Estratégia C (abaixo) passa a rodar **apenas** quando o
`dom2parser` não encontrou nenhuma estrutura repetida verificada — tipicamente páginas
de campos soltos (ficha de CNPJ, cabeçalho de processo). Nesses casos o prompt avisa ao
modelo que as linhas `selector:`/`fields:` da representação já são seletores medidos e
devem ser reaproveitados, e que os caminhos truncados (`div.x > ul > li`) não são.

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

O campo `hints` é opcional — o LLM o inclui quando consegue identificar seletores ou padrões estruturais relevantes para a Estratégia C.

### Cache e custo

- A chamada ao LLM ocorre **uma única vez por padrão de URL por tenant**.
- Após gravar no `URLPatternCache` com `detection_source="llm_classification"`, capturas seguintes do mesmo padrão usam o cache diretamente.
- Custo por chamada: ~1.500–4.000 tokens (representação comprimida típica + prompt + resposta).
- Modelo recomendado: modelo de menor custo da família disponível (ex: `claude-haiku-4-5`).

---

## Estratégia C — Extração + Schema Unificados (primeira captura)

Na **primeira captura** de um padrão URL novo com `allow_external_llm=True`, **e somente se o parser verificado do `dom2parser` (Estratégia A′) não produziu registros**, o LLM faz tudo em uma única chamada: categoriza a página, extrai os dados estruturados e produz o schema de seletores CSS para reuso.

Isso é fundamental: o extrator determinístico não roda na primeira captura quando LLM está habilitado e retorna `structured_data` — o LLM substitui completamente a extração estruturada, não só complementa. Mas o LLM nunca substitui um parser verificado: se o `dom2parser` já alcançou os registros da página com seletores medidos, não há chamada externa. **O LLM não gera texto de busca** — esse campo é sempre produzido separadamente por `extract_narrative_text()` (trafilatura), como descrito em "Princípio: texto de busca sempre via trafilatura" no topo deste documento. Isso limita o dano de uma resposta de LLM malformada ou incompleta: mesmo que `structured_data` saia vazio ou o schema seja inválido, o texto pesquisável do documento nunca depende do LLM.

### Modelo usado

`LLM_EXTRACTOR_MODEL` (padrão: `claude-sonnet-5`) — modelo de maior qualidade, justificado pelo fato de que o custo é amortizado em todas as capturas seguintes do mesmo padrão.

### Input

```
URL da página
Dica de page_type da análise estrutural (não vinculante)
Representação estrutural comprimida via dom2parser (Estratégia A)
```

### Output esperado

```json
{
  "categoria": "Extrato de conta corrente do Banco do Brasil, março 2025",
  "page_type": "tabular_financeiro",
  "structured_data": {
    "conta": "12345-6",
    "periodo": {"inicio": "2025-03-01", "fim": "2025-03-31"},
    "transacoes": [
      {"data": "2025-03-01", "descricao": "PIX recebido João Silva", "valor": 500.00, "saldo": 1500.00}
    ]
  },
  "schema": {
    "version": "1.0",
    "generated_by": "llm",
    "model": "claude-sonnet-5",
    "categoria": "Extrato de conta corrente do Banco do Brasil, março 2025",
    "generated_at": "2025-06-03T14:00:00Z",
    "fields": {
      "conta": {"selector": ".numero-conta", "transform": "text"}
    },
    "tables": [
      {
        "selector": "table.lancamentos",
        "columns": {
          "data":      {"index": 0, "transform": "date_br"},
          "descricao": {"index": 1, "transform": "text"},
          "valor":     {"index": 2, "transform": "brl_float"},
          "saldo":     {"index": 3, "transform": "brl_float"}
        }
      }
    ]
  }
}
```

### Transforms disponíveis

| Transform | Comportamento |
|-----------|---------------|
| `text` | `element.get_text(strip=True)` |
| `brl_float` | Parse de valor monetário BR → float (ex: `R$ 1.234,56` → `1234.56`) |
| `date_br` | Normaliza `dd/mm/yyyy` → `yyyy-mm-dd` |
| `attr:href` | Extrai atributo `href` do elemento |

### Extrator genérico (`schema_driven_extract`)

Interpreta o schema usando BeautifulSoup em capturas subsequentes. **Não usa `exec()` nem `eval()`** — o schema é dados, não código. Se um seletor falhar, o campo é omitido com warning; a extração continua.

### Ciclo de vida

```
1ª captura (LLM habilitado, sem schema)
    → llm_extract_and_schema() extrai structured_data + gera schema (sem texto)
    → structured_data usado imediatamente; texto já veio de extract_narrative_text
    → schema VALIDADO contra o próprio HTML da captura
        → válido   → gravado em URLPatternCache.extractor_config
                     (schema_failure_count=0, needs_review=False)
        → inválido → NÃO gravado; próxima captura tenta regenerar

2ª+ capturas (LLM habilitado ou não)
    → cache HIT → schema_driven_extract() — zero chamadas ao LLM
    → sucesso (extractor_version schema_driven:*) → schema_failure_count zerado
    → falha (caiu no fallback) → schema_failure_count incrementado

≥ 2 falhas consecutivas do schema
    → extractor_config zerado; needs_review=True
    → próxima captura com LLM regenera o schema (custo pago uma vez)
    → schema regenerado e validado limpa needs_review (ciclo autônomo)

divergência estrutural (page_type) → divergence_count incrementado
≥ 3 divergências → needs_review=True; extractor_config zerado; volta à 1ª captura

1ª captura (LLM desabilitado ou falha do LLM)
    → extrator determinístico como fallback
```

O contador de falhas do schema (`schema_failure_count`) é o detector mais fiel de
mudança de estrutura: o `structure_fingerprint` mede o conteúdo visível (título,
headings, headers de tabela), mas os seletores dependem de classes e IDs — que
mudam de forma independente (ex: rebuild do frontend com CSS hasheado). Testar se
os seletores ainda extraem dados é testar exatamente a pergunta que importa.

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

## Extração bruta por biblioteca (comparação, 2026-09-11)

Além de `structured_data` (a vencedora da cascata acima, usada pelo resto do app),
`DocumentText` guarda o resultado **bruto** de cada biblioteca de extração
estrutural que roda sobre o HTML, sem passar pela cascata de decisão. Servem para
comparar cobertura/qualidade entre estratégias e depurar sem reprocessar o MHTML —
não alimentam busca, embeddings nem nenhum consumidor além do visualizador.

| Campo | Biblioteca | O que captura |
|---|---|---|
| `dados_estruturados_dom2parser` | [`dom2parser`](https://bonafe.github.io/dom2parser/) (Estratégia A′) | Estruturas repetidas inferidas por padrão do DOM (tabelas, listas) — `{"registros": {...}}`, igual ao formato de `structured_data` quando essa é a fonte vencedora |
| `dados_estruturados_extruct` | [`extruct`](https://github.com/scrapinghub/extruct) | Metadados que o próprio site declara: JSON-LD, Microdata, OpenGraph e Microformats (schema.org Organization/Person/Article/Product, meta tags sociais). RDFa fica de fora — nesta lib ele confunde `role=` de acessibilidade com metadado de conteúdo (medido: 262 itens de ruído puro numa única página) |

Ambas rodam de forma determinística, sem LLM e sem rede, em **toda captura**,
independente de `page_type`, `allow_external_llm` ou de qual estratégia venceu a
cascata (`apps/artifacts/tasks.py`, logo após `dom2parser.compress(html)`):

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

Complementares por natureza: `dom2parser` infere estrutura por repetição no DOM
mesmo sem marcação alguma; `extruct` só lê o que o publicador anotou
explicitamente, mas quando presente costuma ser mais confiável que qualquer
heurística. Nenhuma delas substitui a outra nem a cascata de `structured_data`.

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
| Miss | Análise estrutural → se confidence < 0.75, aciona B+C → cria registro |
| Divergência (tipo diferente do cache) | Incrementa `divergence_count` |
| `divergence_count ≥ 3` | `needs_review=True`; schema invalidado (`extractor_config={}`) |
| Schema não extraiu dados (caiu no fallback) | Incrementa `schema_failure_count` |
| Schema extraiu dados | Zera `schema_failure_count` |
| `schema_failure_count ≥ 2` | `needs_review=True`; schema invalidado (`extractor_config={}`); próxima captura com LLM regenera |
| Schema regenerado e validado com sucesso | Zera contadores; limpa `needs_review` (ciclo autônomo) |

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
    # vazio → usa extrator determinístico ou LLM direto; preenchido → schema_driven_extract
    # inclui: version, fields, tables, categoria, generated_by, model, generated_at
    hit_count            = models.PositiveIntegerField(default=1)
    divergence_count     = models.PositiveIntegerField(default=0)
    schema_failure_count = models.PositiveIntegerField(default=0)
    # capturas consecutivas em que os seletores do schema não extraíram nada;
    # ≥ 2 → extractor_config zerado + needs_review (regenera via LLM na próxima)
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
  "extractor_version": "schema_driven:1.0 | financial_table:1.0 | ...",
  "structured_data": { ... },
  "dados_estruturados_dom2parser": { ... },
  "dados_estruturados_extruct": { ... },
  "char_count": 1234,
  "word_count": 234
}
```

---

## Extratores por Tipo (determinísticos)

Usados quando `extractor_config` está vazio — ou seja, nas primeiras capturas de padrões novos e em tipos com alta confiança estrutural. **Produzem apenas `structured_data`** — o campo `text` do `DocumentText` nunca vem daqui; é sempre `extract_narrative_text()` (trafilatura), calculado uma única vez antes da detecção de tipo (ver "Princípio: texto de busca sempre via trafilatura").

### `artigo`
- **structured_data:** `null` (não há extração estruturada específica para prosa)

### `tabular_financeiro`
- **Lib:** BeautifulSoup + heurística de mapeamento de colunas por header
- **structured_data:**
  ```json
  {"tipo": "tabular_financeiro", "transacoes": [{"data": "...", "descricao": "...", "valor": -150.0, "saldo": 2340.5}]}
  ```

### `tabular_generico`
- **Lib:** BeautifulSoup
- **structured_data:** `{"tipo": "tabular_generico", "tabelas": [{"cabecalho": [...], "linhas": [...]}]}`

### `processo_judicial`
- **Lib:** BeautifulSoup + regex CNJ
- **structured_data:**
  ```json
  {"tipo": "processo_judicial", "numero_cnj": "...", "classe": "...", "assunto": "...", "partes": {...}, "movimentacoes": [...]}
  ```

### `perfil_pessoa_juridica`
- **Lib:** BeautifulSoup — varre `<dl>` e tabelas 2-colunas
- **structured_data:** `{"tipo": "perfil_pessoa_juridica", "cnpj": "...", "campos": {...}}`

### `documento_juridico`
- **structured_data:** `{"tipo": "documento_juridico"}` (marcador de classificação — sem extração de campos)

### `misto`
- **Lib:** BeautifulSoup (tabelas)
- **structured_data:** `{"tipo": "misto", "tabelas": [...]}`

### `desconhecido`
- **structured_data:** `null`

---

## Observabilidade

Cada item abaixo é hoje um **evento persistido** no log do pipeline, não apenas uma linha de log de contêiner — ver [`../observabilidade.md`](../observabilidade.md). Em particular, `extracao.dom2parser` distingue `falhou` (a biblioteca quebrou) de `vazio` (rodou e nenhum registro atingiu `precision/recall == 1.0`), com o motivo e as contagens no payload. Antes disso, os dois casos chegavam ao banco como o mesmo `NULL`.

Campos registrados a cada extração:

- `page_type` detectado
- `detection_source` (`cache`, `structural_analysis`, `llm_classification`)
- `detection_confidence`
- `extractor_version`
- `dom_representation` gerado/persistido (bool), sua redução percentual e o número de registros verificados / falhas do parser (`dom2parser`)
- registros e linhas extraídos pelo parser verificado (Estratégia A′) e se o spec foi gravado no cache
- `llm_model` (quando Estratégia B é ativada)
- `schema_driven` (bool — se Estratégia C foi usada)
- `divergence` (se houve divergência com cache)
- `structured_data_keys`
- Tempo de extração em ms
- Nó e processo que executaram (`hostname`/`process_id`) — relevante com mais de um worker

---

## Evolução Futura

- **Confirmação humana:** interface no admin Django para revisar padrões marcados como `needs_review`, corrigir `page_type` e editar `extractor_config` manualmente.
- **Extratores por tribunal específico:** TJSP, TJRJ, STJ, TRF têm layouts diferentes — extratores dedicados por `domain` quando `page_type == processo_judicial`, complementando o schema da Estratégia C.
- **Extrator bancário por banco:** cada banco tem estrutura de tabela diferente — `extractor_config` no cache armazena seletores CSS por `domain`, gerados pela Estratégia C.

---

## Critérios de Aceitação

- [ ] Extrato bancário detectado como `tabular_financeiro` na primeira captura sem configuração manual.
- [ ] `structured_data` com lista de transações gerado corretamente para página financeira.
- [ ] Processo judicial do TJSP detectado como `processo_judicial` com `numero_cnj` extraído.
- [ ] Artigo de notícia detectado como `artigo`, comportamento atual preservado sem regressão.
- [ ] Segunda captura do mesmo padrão de URL usa `detection_source: cache` e não roda análise estrutural.
- [x] Página com `confidence < 0.75` aciona compressão via `dom2parser` (A) e classificação por LLM (B).
- [x] Página classificada como `restrito` ou `confidencial` **não** aciona LLM externo.
- [x] `DocumentText.dom_representation` é persistido a cada extração, independente do caminho de extração seguido.
- [x] Página com estruturas repetidas (extrato, listagem) tem `structured_data.registros` extraído pelo parser verificado do `dom2parser`, sem chamada a LLM (A′).
- [x] O `ParserSpec` é gravado em `extractor_config` (formato 2.0) na primeira captura e reexecutado nas seguintes via `schema_driven_extract`.
- [x] LLM (C) só é acionado quando o parser verificado não produziu registros.
- [ ] `extractor_config` é gerado e gravado na primeira extração pós-classificação por LLM (C).
- [ ] Segunda captura usa `schema_driven_extract` sem nova chamada ao LLM.
- [ ] Seletor inválido no schema produz warning sem interromper extração dos demais campos.
- [ ] Mudança de layout (divergência) registrada em `divergence_count`; após 3 divergências, `needs_review=True` e `extractor_config` é zerado.
- [ ] Cache isolado por tenant: organização A não acessa registros da organização B.
- [ ] Página não reconhecida usa `desconhecido` sem lançar exceção.
- [ ] `ArtifactLineage.processor` identifica o extrator e sua versão.
- [x] Todos os campos de observabilidade registrados a cada extração, como eventos persistidos e consultáveis.
- [ ] `DocumentText.text` é sempre produzido por `extract_narrative_text()` (trafilatura), inclusive quando `page_type` é `tabular_financeiro`, `processo_judicial` ou quando a extração roda via LLM (Estratégia C).
- [ ] `llm_extract_and_schema()` não retorna mais campo `text`; resposta do LLM sem `structured_data` não impede a criação do `DocumentText` (o texto já foi extraído antes).
- [ ] Falha total do extrator estruturado (LLM indisponível, schema inválido, extrator determinístico sem dados) resulta em `structured_data=null`, mas nunca em `DocumentText.text` vazio se a página tiver conteúdo extraível por trafilatura.
