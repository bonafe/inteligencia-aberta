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
    ├─ busca MHTML no MinIO                         [existente]
    ├─ extrai HTML do MHTML                         [existente]
    │
    ├─ detect_page_type(html, url, tenant)          [NOVO]
    │       │
    │       ├─ 1. lookup URLPatternCache
    │       │       └─ hit (confiança ≥ 0.9) → usa tipo cacheado, pula análise
    │       │
    │       └─ 2. miss → analyze_html_structure(html)
    │               └─ classifica → atualiza / cria URLPatternCache
    │
    ├─ route_to_extractor(page_type, html)          [NOVO]
    │       ├─ artigo              → _extract_article()
    │       ├─ tabular_financeiro  → _extract_financial_table()
    │       ├─ tabular_generico    → _extract_generic_table()
    │       ├─ processo_judicial   → _extract_judicial_process()
    │       ├─ perfil_pessoa_juridica → _extract_company_profile()
    │       ├─ documento_juridico  → _extract_legal_document()
    │       ├─ misto               → _extract_mixed()
    │       └─ desconhecido        → _extract_fallback()
    │
    ├─ cria Artifact(tipo=TEXT) com structured_data [existente + campos novos]
    ├─ cria ArtifactLineage(transformation="text_extraction")
    └─ dispara fragment_text.delay(child.id)        [existente]
```

---

## Algoritmo de Detecção Estrutural

### Métricas coletadas do HTML (via BeautifulSoup)

| Métrica | Descrição |
|---------|-----------|
| `table_count` | Número de `<table>` no documento |
| `table_row_count` | Total de `<tr>` em todas as tabelas |
| `table_char_count` | Caracteres dentro de células de tabela |
| `text_char_count` | Caracteres em `<p>`, `<li>`, `<article>`, `<section>` fora de tabelas |
| `table_ratio` | `table_char_count / (table_char_count + text_char_count)` |
| `monetary_count` | Matches de `R\$\s*[\d.,]+` ou colunas com padrão de valor monetário |
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

Cada regra produz um `confidence` score. O score pode ser refinado conforme o cache acumula confirmações.

---

## Cache de Padrões de URL

### Normalização de URL

Antes de consultar ou gravar o cache, a URL é normalizada para extrair um padrão reutilizável:

1. Strip de protocolo (`https://`), query string (`?...`) e fragment (`#...`)
2. Segmentos do path que parecem IDs são substituídos por `*`:
   - UUIDs: `550e8400-e29b-41d4-a716-446655440000` → `*`
   - Números puros: `/conta/12345678` → `/conta/*`
   - Datas: `/extrato/2024-03-15` → `/extrato/*`
   - Hashes: `/doc/abc1234def` → `/doc/*`
3. Resultado: `domínio/path/normalizado/*`

**Exemplos:**

| URL original | Padrão normalizado |
|---|---|
| `https://bb.com.br/extrato/12345678/2024-03` | `bb.com.br/extrato/*/*` |
| `https://esaj.tjsp.jus.br/cpopg/show.do?processo.codigo=AB0001` | `esaj.tjsp.jus.br/cpopg/show.do` |
| `https://servicos.receita.fazenda.gov.br/Servicos/cnpjreva/Cnpjreva_Solicitacao.asp` | `servicos.receita.fazenda.gov.br/Servicos/cnpjreva/Cnpjreva_Solicitacao.asp` |

### Comportamento do cache

- **Hit (confiança ≥ 0.9):** usa tipo armazenado, incrementa `hit_count`, pula análise estrutural.
- **Hit (confiança < 0.9):** usa tipo armazenado mas ainda roda análise estrutural para confirmar.
- **Miss:** roda análise estrutural completa, cria registro de cache com confiança da detecção.
- **Divergência:** se a análise detecta tipo diferente do cache:
  - Incrementa `divergence_count` no cache.
  - Se `divergence_count ≥ 3`: marca o padrão como `needs_review`, usa tipo recém-detectado. Isso sinaliza que a página provavelmente mudou de estrutura (nova versão do banco, por exemplo).
  - Caso contrário: mantém tipo cacheado, registra divergência.

O cache é **isolado por tenant**: organização A nunca acessa o cache da organização B.

---

## Extratores por Tipo

### `artigo`
- **Lib:** trafilatura (comportamento atual, sem mudanças)
- **Output text:** texto narrativo limpo
- **structured_data:** `null`

### `tabular_financeiro`
- **Lib:** BeautifulSoup + detecção de colunas por heurística de header
- **Lógica:**
  1. Encontra todas as `<table>` do documento
  2. Para cada tabela, tenta mapear colunas para: data, descrição, valor, saldo
  3. Converte cada linha em registro `{data, descricao, valor, saldo}`
  4. Infere sinal de valor (débito/crédito) por cor, símbolo ou coluna separada
- **Output text:** texto narrativo gerado a partir das transações, apropriado para embedding:
  `"Transação em 15/03/2024: Pix enviado para João Silva — débito R$ 150,00. Saldo R$ 2.340,50."`
- **structured_data:**
  ```json
  {
    "tipo": "tabular_financeiro",
    "periodo": {"inicio": "2024-03-01", "fim": "2024-03-31"},
    "transacoes": [
      {"data": "2024-03-15", "descricao": "Pix enviado para João Silva", "valor": -150.00, "saldo": 2340.50}
    ]
  }
  ```

### `tabular_generico`
- **Lib:** BeautifulSoup
- **Lógica:** extrai todas as tabelas com cabeçalhos preservados como arrays de objetos
- **Output text:** serialização legível de cada tabela
- **structured_data:**
  ```json
  {
    "tipo": "tabular_generico",
    "tabelas": [
      {"cabecalho": ["Col A", "Col B"], "linhas": [["val1", "val2"]]}
    ]
  }
  ```

### `processo_judicial`
- **Lib:** BeautifulSoup + regex para padrão CNJ
- **Lógica:**
  1. Extrai número do processo (padrão CNJ obrigatório)
  2. Extrai partes (polo ativo, polo passivo) por labels conhecidos
  3. Extrai classe, assunto, juízo, distribuição
  4. Extrai movimentações (lista de `{data, descricao}`)
  5. Extrai documentos anexos se listados
- **Output text:** narrativa do processo para embedding
- **structured_data:**
  ```json
  {
    "tipo": "processo_judicial",
    "numero_cnj": "0001234-56.2024.8.26.0001",
    "classe": "Ação de Cobrança",
    "assunto": "Contratos Bancários",
    "partes": {
      "polo_ativo": ["Banco XYZ S.A."],
      "polo_passivo": ["José da Silva"]
    },
    "movimentacoes": [
      {"data": "2024-03-10", "descricao": "Petição inicial protocolada"}
    ]
  }
  ```

### `perfil_pessoa_juridica`
- **Lib:** BeautifulSoup
- **Lógica:** varre pares label/valor em `<table>` ou `<dl>` típicos de fichas cadastrais
- **Output text:** texto descritivo da empresa
- **structured_data:**
  ```json
  {
    "tipo": "perfil_pessoa_juridica",
    "cnpj": "12.345.678/0001-90",
    "razao_social": "Empresa Exemplo S.A.",
    "situacao": "Ativa",
    "data_abertura": "2001-05-14",
    "atividade_principal": "...",
    "socios": []
  }
  ```

### `documento_juridico`
- **Lib:** trafilatura com `favor_recall=True` + detecção de seções por numeração
- **Output text:** texto integral com seções identificadas
- **structured_data:** `{"tipo": "documento_juridico", "secoes": ["1. ...", "2. ..."]}`

### `misto`
- **Lib:** trafilatura (prosa) + BeautifulSoup (tabelas), resultados combinados
- **Output text:** prosa + serialização das tabelas
- **structured_data:** `{"tipo": "misto", "tabelas": [...]}`

### `desconhecido`
- **Lib:** trafilatura com `favor_recall=True` (comportamento atual de fallback)
- **Output text:** melhor extração possível
- **structured_data:** `null`

---

## Modelo de Dados

### Novo modelo: `URLPatternCache`

```python
class URLPatternCache(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE,
                               related_name="url_pattern_caches")
    domain = models.CharField(max_length=255, db_index=True)
    path_pattern = models.CharField(max_length=1024)
    page_type = models.CharField(max_length=50)
    extractor_config = models.JSONField(default=dict)
    confidence = models.FloatField()
    hit_count = models.PositiveIntegerField(default=1)
    divergence_count = models.PositiveIntegerField(default=0)
    needs_review = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("tenant", "domain", "path_pattern")]
        indexes = [Index(fields=["tenant", "domain"])]
```

### Campos novos no `content` do Artifact TEXT filho

```json
{
  "text": "...",
  "page_type": "tabular_financeiro",
  "detection_confidence": 0.90,
  "detection_source": "cache | structural_analysis",
  "url_pattern_cache_id": "uuid-do-cache",
  "extractor_version": "financial_table:1.0",
  "structured_data": { ... },
  "char_count": 1234,
  "word_count": 234
}
```

### Campo novo no `processor` do ArtifactLineage

```
"extractor:{page_type}:{versão}"
# Exemplo: "extractor:tabular_financeiro:1.0"
# Antes: "trafilatura:0.9.46"
```

---

## Observabilidade

Campos que devem ser logados a cada extração:

- `page_type` detectado
- `detection_source` (cache ou análise)
- `detection_confidence`
- `extractor_version`
- `divergence` (se houve divergência com cache)
- `structured_data_keys` (quais campos foram extraídos)
- Tempo de extração em ms

---

## Evolução Futura

- **Confirmação humana:** interface no admin Django para revisar padrões marcados como `needs_review` e corrigi-los manualmente.
- **Extratores por tribunal específico:** TJSP, TJRJ, STJ, TRF possuem layouts diferentes — extratores dedicados por `domain` quando `page_type == processo_judicial`.
- **Extrator bancário por banco:** cada banco tem estrutura de tabela diferente — `extractor_config` no cache pode armazenar os seletores CSS corretos por `domain`.
- **Detecção assistida por LLM:** para páginas `desconhecido` ou com baixa confiança, enviar amostra do HTML para LLM (somente se `allow_external_llm` e `classification_level` permitirem) para sugerir tipo e seletores.

---

## Critérios de Aceitação

- [ ] Extrato bancário (BB, Itaú, Nubank) detectado como `tabular_financeiro` na primeira captura sem configuração manual.
- [ ] `structured_data` com lista de transações gerado corretamente para página financeira.
- [ ] Processo judicial do TJSP detectado como `processo_judicial` com `numero_cnj` extraído corretamente.
- [ ] Artigo de notícia detectado como `artigo`, comportamento atual preservado sem regressão.
- [ ] Segunda captura do mesmo padrão de URL usa `detection_source: cache` e não roda análise estrutural.
- [ ] Mudança de layout da página (divergência) registrada em `divergence_count`; após 3 divergências, `needs_review = true`.
- [ ] Cache isolado por tenant: organização A não acessa registros da organização B.
- [ ] Página não reconhecida usa `desconhecido` sem lançar exceção.
- [ ] `ArtifactLineage.processor` identifica o extrator e sua versão (ex: `extractor:tabular_financeiro:1.0`).
- [ ] Todos os campos de observabilidade logados a cada extração.
