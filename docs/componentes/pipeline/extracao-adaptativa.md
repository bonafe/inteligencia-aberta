# Componente: Extração Adaptativa de HTML

## Responsabilidade

Detectar automaticamente o tipo de conteúdo de uma página capturada como MHTML e aplicar a estratégia de extração mais adequada — preservando a estrutura semântica dos dados em vez de converter tudo em texto plano.

Aprende com capturas anteriores: padrões de URL já classificados são armazenados por tenant e reutilizados nas capturas seguintes, evitando reprocessamento e acelerando o pipeline.

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
    │               ├─ compress_html_skeleton(html)         [Estratégia A]
    │               └─ llm_classify(skeleton, url)          [Estratégia B — Haiku]
    │                       └─ sobrepõe page_type + confidence se melhor
    │
    ├─ [SE allow_external_llm E cache sem extractor_config] → 1ª captura com LLM
    │       ├─ compress_html_skeleton(html)                 [Estratégia A]
    │       ├─ llm_extract_and_schema(skeleton, url, hint)  [Estratégia C — Sonnet]
    │       │       └─ retorna: categoria + page_type + text + structured_data + schema
    │       ├─ usa text + structured_data como resultado da extração (imediato)
    │       └─ grava schema em URLPatternCache.extractor_config
    │
    ├─ [ELSE] route_to_extractor(page_type, cache_obj)
    │       ├─ cache_obj.extractor_config presente?
    │       │       └─ SIM → schema_driven_extract(html, config)   [capturas 2+]
    │       └─ NÃO → extrator determinístico por tipo (fallback)
    │               ├─ tabular_financeiro  → extract_financial_table
    │               ├─ tabular_generico    → extract_generic_table
    │               ├─ processo_judicial   → extract_judicial_process
    │               ├─ perfil_pessoa_juridica → extract_company_profile
    │               ├─ documento_juridico  → extract_legal_document
    │               ├─ misto               → extract_mixed
    │               └─ artigo / desconhecido → extract_fallback (trafilatura)
    │
    ├─ cria DocumentText com page_type, structured_data, extractor_version
    └─ dispara fragment_text.delay(doc_text.id)
```

---

## Estratégia A — Compressão de Esqueleto HTML

Antes de qualquer chamada ao LLM, o HTML é comprimido para um **esqueleto estrutural**. Isso reduz tipicamente 60–90% do tamanho sem perder as informações relevantes para classificação e definição de seletores.

### O que é removido

| Elemento | Motivo |
|----------|--------|
| `<script>`, `<style>`, `<link>`, `<meta>` | Não contribuem para estrutura semântica |
| `<svg>`, `<canvas>`, `<noscript>`, `<iframe>` | Ruído sem valor para classificação |
| Comentários HTML | Sem valor semântico |
| Atributos de estilo (`style=`, `onclick=`, `data-v-*`) | Ruído de framework |

### O que é preservado

- Hierarquia de tags intacta
- Atributos estruturais: `class`, `id`, `name`, `data-campo`, `aria-label`, `href` (apenas domínio)
- Texto dos nós: truncado a **80 caracteres** — o suficiente para reconhecer labels, headers e valores de exemplo

### Resultado esperado

```
Entrada : 450 KB de HTML (página de extrato bancário)
Saída   : ~12 KB de esqueleto estrutural
```

O esqueleto é o único input enviado ao LLM. O HTML original nunca é transmitido a serviços externos.

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
- Custo por chamada: ~1.500–4.000 tokens (esqueleto típico + prompt + resposta).
- Modelo recomendado: modelo de menor custo da família disponível (ex: `claude-haiku-4-5`).

---

## Estratégia C — Extração + Schema Unificados (primeira captura)

Na **primeira captura** de um padrão URL novo com `allow_external_llm=True`, o LLM faz tudo em uma única chamada: categoriza a página, extrai os dados estruturados, gera o texto para embedding e produz o schema de seletores CSS para reuso.

Isso é fundamental: o extrator determinístico não roda na primeira captura quando LLM está habilitado — o LLM substitui completamente a extração, não só complementa.

### Modelo usado

`LLM_EXTRACTOR_MODEL` (padrão: `claude-sonnet-4-6`) — modelo de maior qualidade, justificado pelo fato de que o custo é amortizado em todas as capturas seguintes do mesmo padrão.

### Input

```
URL da página
Dica de page_type da análise estrutural (não vinculante)
Esqueleto HTML comprimido (Estratégia A, máx. 20 KB)
```

### Output esperado

```json
{
  "categoria": "Extrato de conta corrente do Banco do Brasil, março 2025",
  "page_type": "tabular_financeiro",
  "text": "Extrato Banco do Brasil — Conta 12345-6. Período 01/03/2025 a 31/03/2025. Transação em 01/03/2025: PIX recebido João Silva — crédito R$ 500,00. Saldo R$ 1.500,00. ...",
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
    "model": "claude-sonnet-4-6",
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
    → llm_extract_and_schema() extrai dados + gera schema
    → resultado usado imediatamente (text + structured_data)
    → schema gravado em URLPatternCache.extractor_config

2ª+ capturas (LLM habilitado ou não)
    → cache HIT → schema_driven_extract() — zero chamadas ao LLM

divergência estrutural → divergence_count incrementado
≥ 3 divergências      → needs_review=True; extractor_config zerado; volta à 1ª captura

1ª captura (LLM desabilitado ou falha do LLM)
    → extrator determinístico como fallback
```

### Limitação conhecida (2026-06-03)

O esqueleto HTML é truncado em 20 KB. Extratos bancários com muitos lançamentos têm as transações espalhadas pelo HTML — o corte pode deixar de fora a maioria dos dados da tabela, fazendo o LLM gerar um schema correto mas sem conseguir extrair os dados da captura atual (que é baseada no conteúdo visível no esqueleto).

**Próximos passos para investigar:**
- Verificar se o problema é de truncamento (aumentar limite ou usar outra estratégia de compressão que preserve linhas de tabela)
- Verificar se o LLM está ignorando dados presentes no esqueleto (problema de prompt)
- Considerar enviar o texto plano das tabelas em vez do esqueleto HTML para páginas com `tabular_*`

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
  "char_count": 1234,
  "word_count": 234
}
```

---

## Extratores por Tipo (determinísticos)

Usados quando `extractor_config` está vazio — ou seja, nas primeiras capturas de padrões novos e em tipos com alta confiança estrutural.

### `artigo`
- **Lib:** trafilatura — comportamento atual preservado
- **structured_data:** `null`

### `tabular_financeiro`
- **Lib:** BeautifulSoup + heurística de mapeamento de colunas por header
- **Output text:** narrativa de transações para embedding
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
- **Lib:** trafilatura com `favor_recall=True`
- **structured_data:** `{"tipo": "documento_juridico"}`

### `misto`
- **Lib:** trafilatura (prosa) + BeautifulSoup (tabelas)
- **structured_data:** `{"tipo": "misto", "tabelas": [...]}`

### `desconhecido`
- **Lib:** trafilatura com `favor_recall=True`
- **structured_data:** `null`

---

## Observabilidade

Campos logados a cada extração:

- `page_type` detectado
- `detection_source` (`cache`, `structural_analysis`, `llm_classification`)
- `detection_confidence`
- `extractor_version`
- `skeleton_size_kb` (quando Estratégia A é ativada)
- `llm_model` (quando Estratégia B é ativada)
- `schema_driven` (bool — se Estratégia C foi usada)
- `divergence` (se houve divergência com cache)
- `structured_data_keys`
- Tempo de extração em ms

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
- [ ] Página com `confidence < 0.75` aciona compressão de esqueleto (A) e classificação por LLM (B).
- [ ] Página classificada como `restrito` ou `confidencial` **não** aciona LLM externo.
- [ ] Esqueleto HTML enviado ao LLM tem no máximo 20 KB (independente do tamanho original).
- [ ] `extractor_config` é gerado e gravado na primeira extração pós-classificação por LLM (C).
- [ ] Segunda captura usa `schema_driven_extract` sem nova chamada ao LLM.
- [ ] Seletor inválido no schema produz warning sem interromper extração dos demais campos.
- [ ] Mudança de layout (divergência) registrada em `divergence_count`; após 3 divergências, `needs_review=True` e `extractor_config` é zerado.
- [ ] Cache isolado por tenant: organização A não acessa registros da organização B.
- [ ] Página não reconhecida usa `desconhecido` sem lançar exceção.
- [ ] `ArtifactLineage.processor` identifica o extrator e sua versão.
- [ ] Todos os campos de observabilidade logados a cada extração.
