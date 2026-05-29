# Pipeline de Transformação de Artefatos

## Visão Geral

Todo artefato bruto capturado (MHTML, PDF, imagem) percorre um pipeline de transformação antes de estar disponível para análise e busca semântica. O pipeline é assíncrono (Celery + Redis), auditável (ArtifactLineage) e extensível (SkillManifest).

---

## Estágios do Pipeline

```
Artefato Bruto (MinIO)
      ↓
[1] Extrator de Texto      → DocumentText (modelo de pipeline)
      ↓
[2] Fragmentador           → N DocumentFragment (modelo de pipeline)
      ↓
[3] Embedder               → vetores no Qdrant (com payload de linhagem)
      ↓ [paralelo]
[4a] NER                   → entidades Artifact (pessoa, empresa…) via ArtifactLineage
[4b] Sumarizador           → artifact_type="sumario" via LLM local
      ↓ [Fase 2]
[5] Correlacionador        → relações entre entidades → Neo4j
```

**Separação de responsabilidades:** `DocumentText` e `DocumentFragment` são modelos de infraestrutura de pipeline — não são artefatos de inteligência. Não carregam `info_type`, `sources` independentes ou auditoria de negócio. A classificação e o tenant são herdados do artefato `documento` de origem no momento do processamento.

`ArtifactLineage` é reservado para rastrear relações entre artefatos de inteligência (ex: NER produzindo `pessoa` ou `empresa` a partir de um `documento`). O pipeline MHTML→texto→fragmento é rastreado pelos relacionamentos diretos dos modelos (`DocumentText.document`, `DocumentFragment.document_text`).

---

## Extração de Texto — Estratégia em Camadas

Extrair texto relevante de HTML é o primeiro e mais crítico passo. A estratégia usa três camadas, em ordem de preferência:

### Camada 1 — Genérica (trafilatura)

`trafilatura` (biblioteca Python) extrai o conteúdo principal de páginas web sem LLM. Cobre ~75% dos casos: jornais, portais de governo, blogs, diários oficiais.

### Camada 2 — Aprendida (SiteProfile)

Quando `trafilatura` retorna qualidade baixa (texto < 200 tokens ou ratio texto/HTML < 0.15), dispara o **LLM Discovery**:

1. Envia o HTML truncado (primeiros 8K tokens) ao LLM
2. LLM identifica e devolve os seletores CSS do conteúdo principal
3. Resultado salvo como `SiteProfile` para o domínio
4. Capturas futuras do mesmo domínio usam os seletores direto — sem custo de LLM

### Camada 3 — Fallback

Se os seletores salvos falham (site redesenhado: seletor retorna vazio), retorna à Camada 1 e agenda novo LLM Discovery.

---

## Embeddings

Um **embedding** é a representação vetorial de um texto — um array de números (tipicamente 768 dimensões) que codifica o significado semântico. Dois textos sobre o mesmo assunto terão vetores próximos no espaço dimensional. Isso é o que permite busca semântica: "encontre documentos com significado similar a esta frase", independente das palavras exatas usadas.

O pipeline usa `sentence-transformers` localmente (modelo `paraphrase-multilingual-mpnet-base-v2`, otimizado para português) para não enviar dados a serviços externos — compatível com a política de LLM local para dados `restrito` e `confidencial`.

Cada vetor é armazenado no Qdrant com payload de linhagem:

```json
{
  "artifact_id":           "uuid-do-fragmento",
  "parent_artifact_id":    "uuid-do-texto-limpo",
  "source_artifact_id":    "uuid-do-mhtml-original",
  "tenant_id":             "uuid-da-organizacao",
  "fragment_index":        3,
  "text_preview":          "primeiros 200 caracteres...",
  "classification_level":  "restrito",
  "source_url":            "https://...",
  "transformation_chain":  ["text_extraction", "fragmentation", "embedding"],
  "created_at":            "2026-05-20T..."
}
```

O isolamento por organização é garantido por collections separadas no Qdrant: uma collection por `tenant_id`.

---

## Modelos de Dados

### DocumentText e DocumentFragment

Modelos de pipeline que representam as etapas de extração e fragmentação. Ver spec completa em [`../arquitetura/modelo-de-dados.md`](./modelo-de-dados.md).

**Cadeia de derivação:**

```
Artifact(tipo=documento)  ← artefato de inteligência
  └─ DocumentText          ← texto extraído (1:1)
       ├─ DocumentFragment[0] → ponto vetorial no Qdrant
       ├─ DocumentFragment[1] → ponto vetorial no Qdrant
       └─ DocumentFragment[N] → ponto vetorial no Qdrant
```

### ArtifactLineage

Registra relações de linhagem **entre artefatos de inteligência** — entidades extraídas, correlações, relatórios gerados. Não é usado para a cadeia documento→texto→fragmento (rastreada pelos FKs dos modelos de pipeline).

```python
class ArtifactLineage(Model):
    id             = UUIDField(primary_key=True)
    parent         = FK(Artifact, related_name="children_lineage")
    child          = FK(Artifact, related_name="parent_lineage")
    transformation = CharField   # "ner", "correlation", "report_generation"
    processor      = CharField   # "spacy:pt_core_news_lg", "correlacionador-v1"
    parameters     = JSONField
    created_at     = DateTimeField
```

**Exemplo de linhagem de inteligência (Fase 2+):**

```
Artifact(documento, doc_abc)
  └─ [ner / spacy:pt_core_news_lg]
       Artifact(empresa, ent_101)  — CNPJ 12.345.678/0001-99
       Artifact(pessoa,  ent_102)  — "João Silva"
```

### SiteProfile

Armazena seletores CSS aprendidos por domínio para extração determinística.

```python
class SiteProfile(Model):
    id            = UUIDField(primary_key=True)
    domain        = CharField(unique=True)    # "in.gov.br"
    selectors     = JSONField                 # ["article.conteudo", "div#texto-da-decisao"]
    discovered_by = CharField                 # "llm:claude-opus-4-7", "manual"
    confidence    = FloatField                # 0.0–1.0
    last_verified = DateTimeField
    created_at    = DateTimeField
```

### SkillManifest

Registry de skills e MCPs disponíveis. O orchestrator consulta o manifest para saber quais transformações aplicar a cada tipo de artefato.

```python
class SkillManifest(Model):
    id             = UUIDField(primary_key=True)
    name           = CharField(unique=True)   # "text_extraction", "ner_cnpj", "embed_pt"
    description    = TextField
    input_type     = CharField                # artifact_type aceito como entrada
    output_type    = CharField                # artifact_type produzido como saída
    trigger_rules  = JSONField                # {"artifact_type": "documento", "source_domain": "in.gov.br"}
    processor_ref  = CharField                # "tasks.extract_text" (referência à task Celery)
    version        = CharField
    active         = BooleanField(default=True)
    created_at     = DateTimeField
```

---

## Fila de Tarefas — Celery + Redis

O pipeline é disparado por Django signals ao criar um novo artefato:

```python
@receiver(post_save, sender=Artifact)
def dispatch_pipeline(sender, instance, created, **kwargs):
    if not created:
        return
    skills = SkillManifest.objects.filter(
        active=True,
        input_type=instance.artifact_type
    )
    for skill in skills:
        if matches_trigger(instance, skill.trigger_rules):
            celery_app.send_task(skill.processor_ref, args=[str(instance.id)])
```

O encadeamento de etapas (extração → fragmentação → embedding) é modelado como `chain` do Celery. Etapas paralelas (NER + sumarização) usam `group`.

**Por que não Kafka agora?** Kafka resolve alto volume, replay de longa duração e dezenas de consumidores independentes. Com 3 serviços e escala MVP, o overhead operacional (broker, KRaft, partições, consumer groups) não tem retorno. Celery + Redis satisfaz todos os requisitos das Fases 0–4. Redis Streams é o próximo passo antes do Kafka, caso necessário. Kafka entra naturalmente ao migrar para Kubernetes (Fase 5).

---

## Orquestração Dinâmica de Skills

Quando uma nova skill é registrada no `SkillManifest`, o orchestrator executa o **catch-up automático**:

1. Consulta todos os `Artifact` existentes que correspondem ao `trigger_rules` da nova skill
2. Enfileira uma task Celery de reprocessamento para cada um
3. O signal `post_save` passa a disparar a nova skill automaticamente para capturas futuras

Isso garante que dados históricos recebem novas capacidades retroativamente, sem intervenção manual. O `ArtifactLineage` registra que a transformação foi aplicada após a captura original, preservando a rastreabilidade.

---

## Referências

- Agente Extrator: [`../componentes/agentes/extrator.md`](../componentes/agentes/extrator.md)
- Motor de Políticas: [`../../services/orchestrator/policy_engine.py`](../../services/orchestrator/policy_engine.py)
- Classificação de dados: [`../seguranca/classificacao.md`](../seguranca/classificacao.md)
- Visão geral da arquitetura: [`./visao-geral.md`](./visao-geral.md)
