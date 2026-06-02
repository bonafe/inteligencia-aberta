# Changelog das Inteligências Artificiais

Este arquivo documenta as alterações, configurações e implementações feitas por IAs (agentes) neste repositório. O objetivo é manter um histórico unificado e transparente sobre o estado do desenvolvimento, facilitando o onboarding de novas IAs e humanos na base de código.

## [02-06-2026] - Correção de encoding no pipeline MHTML + scripts de ambiente

**Contexto e motivação:**
- Páginas capturadas com a extensão exibiam caracteres portugueses corrompidos (`Cart?o`, `Poupan?a`, `Aten??o`) na galeria e no visualizador MHTML. O problema afetava sites que declaram charset incorreto ou inconsistente nos headers MIME — padrão comum em portais bancários e governamentais brasileiros.
- A causa raiz estava em dois lugares com o mesmo padrão: `payload.decode(charset, errors='replace')` usava apenas o charset declarado no header MIME e, ao falhar silenciosamente com `errors='replace'`, gravava U+FFFD no banco ou exibia lixo no preview.

**O que foi implementado:**

- **`services/portal/apps/artifacts/tasks.py`:**
  - Adicionada função `_decode_html_bytes(payload, mime_charset)` com cascade de decodificação em quatro níveis: (1) charset do header MIME; (2) `<meta charset>` extraído por regex nos primeiros 4 KB dos bytes brutos (funciona mesmo quando o charset do MIME está errado); (3) fallbacks explícitos para Europa Ocidental — `utf-8`, `cp1252`, `iso-8859-1` (cobrem todos os sites legados brasileiros); (4) `latin-1` com `errors='replace'` como último recurso absoluto.
  - Removida dependência de `charset-normalizer`: durante testes, a biblioteca identificava incorretamente `cp1250` (Europa Central) em vez de `cp1252` para texto português, produzindo `ă` no lugar de `ã`. Para conteúdo brasileiro, a cascade explícita é mais confiável.
  - Task `reprocess_garbled_documents` adicionada: localiza `DocumentText` com U+FFFD, apaga seus fragmentos e re-enfileira a extração via `extract_text_from_mhtml`.
  - Nota: worker Celery precisa ser reiniciado (`docker compose restart worker`) para registrar novas tasks adicionadas em runtime.

- **`services/portal/apps/artifacts/views.py`:**
  - `ServeMHTMLView` tinha o mesmo bug de encoding que `tasks.py`, mas não havia sido corrigido na sessão anterior — era o único lugar de fato visível para o usuário (o preview na galeria). Corrigido para reutilizar `_decode_html_bytes` importado de `tasks`.
  - Esta foi a causa real dos caracteres corrompidos na interface: o banco armazenava o texto corretamente, mas o viewer renderizava o MHTML com `errors='replace'`.

- **`scripts/subir_containers.sh` e `scripts/limpar_containers.sh` (novos):**
  - `subir_containers.sh`: sobe com `docker-compose.override.yml` (hot-reload), aguarda o portal responder, roda `migrate` automaticamente e imprime as URLs dos serviços.
  - `limpar_containers.sh`: pede confirmação explícita, para containers com `--remove-orphans`, apaga `./data/` (requer `sudo` por volumes Postgres pertencentes a root) e remove imagens buildadas.

**Diagnóstico que enganou:**
- A inspeção inicial do `DocumentText` no banco mostrou texto correto (`Último`, `Sessão`, `Transações`). Isso levou a suspeitar do display, não do armazenamento — o que estava certo, mas direcionou a investigação para o lugar errado inicialmente. O banco estava correto porque o `docker-compose.override.yml` monta o código como volume: a fix em `tasks.py` estava ativa. A corrupção visível vinha de `views.py`, que não havia sido atualizado.

**Status Atual:**
- Encoding robusto em toda a cadeia: extração (tasks) e visualização (views) usam a mesma lógica de decode com fallback. Sites com charset incorreto, ausente ou incompatível com o conteúdo real são tratados corretamente.

**Próximos Passos Sugeridos:**
- NER (Etapa 4): extrair entidades (CPF, CNPJ, nomes, datas) de `DocumentText` e criar `Artifact(tipo=pessoa/empresa)` com `ArtifactLineage`.
- Adicionar `reprocess_garbled_documents` ao `CELERY_BEAT_SCHEDULE` como varredura semanal opcional.

---

## [29-05-2026] - Refatoração: separação de artefatos de inteligência e modelos de pipeline

**Contexto e motivação:**
- O modelo `Artifact` acumulava dois tipos que não são entidades de inteligência: `texto` (texto extraído de MHTML) e `fragmento` (chunk de RAG). Esses tipos violavam o contrato semântico do modelo — campos como `info_type` (`fato/opinião/inferência`) e `sources` independentes não fazem sentido para um chunk de texto.
- Problema de escala: um documento de 50 páginas produzia ~150 fragmentos na tabela `artifacts_artifact`, contaminando queries sobre entidades reais (pessoas, empresas, processos) e inflando o `AuditLog` com eventos de pipeline sem valor de auditoria de negócio.
- `ArtifactLineage` estava sendo usado para rastrear `documento → texto → fragmento`, uma cadeia de pipeline — seu propósito correto é rastrear linhagem entre artefatos de inteligência (ex: NER produzindo `empresa` a partir de `documento`).

**O que foi implementado:**

- **`services/portal/apps/artifacts/models.py`:**
  - Removidos `TEXT = "texto"` e `FRAGMENT = "fragmento"` de `Artifact.Type`. O modelo agora tem exatamente os 6 tipos de inteligência da spec: `pessoa`, `empresa`, `documento`, `processo`, `endereco`, `evento`.
  - Adicionado `DocumentText`: modelo de pipeline com relação OneToOne para `Artifact(tipo=documento)`. Campos próprios: `text`, `title`, `source_url`, `page_type`, `detection_confidence`, `detection_source`, `url_pattern_cache` (FK), `structured_data`, `extractor_version`, `char_count`, `word_count`.
  - Adicionado `DocumentFragment`: modelo de pipeline pertencente a `DocumentText`. Campos: `text`, `fragment_index`, `total_fragments`, `qdrant_point_id`, `qdrant_collection`. Classificação e tenant derivados de `fragment.document_text.document` no momento do embedding.

- **`migrations/0004_pipeline_models.py`:**
  - `RunPython` apaga artefatos existentes do tipo `texto`/`fragmento` e seus registros de `ArtifactLineage` antes de alterar as choices.
  - `AlterField` remove `texto` e `fragmento` das choices de `artifact_type`.
  - `CreateModel` para `DocumentText` e `DocumentFragment`.

- **`services/portal/apps/artifacts/tasks.py`** — reescrito:
  - `extract_text_from_mhtml`: cria `DocumentText` em vez de `Artifact(type=TEXT)`. Não cria mais `ArtifactLineage`. Idempotência via `DocumentText.objects.filter(document=artifact).first()`.
  - `fragment_text(document_text_id)`: recebe ID de `DocumentText` em vez de ID de artefato. Cria `DocumentFragment`. Não usa `ArtifactLineage`.
  - `embed_fragment(fragment_id)`: recebe ID de `DocumentFragment`. Usa `select_related("document_text__document")` para obter tenant/classificação em uma query. Persiste `qdrant_point_id` e `qdrant_collection` direto no `DocumentFragment` em vez do `content` JSON.
  - `scan_unprocessed_documents`: queries simplificadas usando os novos modelos diretamente.

- **`services/portal/apps/artifacts/admin.py`:**
  - Adicionados `DocumentTextAdmin` e `DocumentFragmentAdmin`.

- **Specs atualizadas:**
  - `docs/arquitetura/modelo-de-dados.md`: tipos de `Artifact` reduzidos a 6; nova seção "Modelos de Pipeline" documenta `DocumentText` e `DocumentFragment`.
  - `docs/componentes/pipeline-rag.md`: payload do Qdrant atualizado com `fragment_id`, `document_text_id`, `document_artifact_id` (removidos nomes antigos `artifact_id`, `parent_artifact_id`).
  - `docs/arquitetura/pipeline-transformacao.md`: diagrama de estágios corrigido; seção de modelos reescrita — `ArtifactLineage` declarado como exclusivo para linhagem de inteligência (Fase 2+, NER/correlação); cadeia de derivação documentada como `Artifact(documento) → DocumentText → DocumentFragment[N]`.

**Status Atual:**
- Modelo de dados limpo: `Artifact` representa somente entidades de inteligência. Pipeline de texto é infraestrutura separada.
- `ArtifactLineage` preservado para uso futuro em NER e correlação entre entidades.
- Para aplicar: `docker compose exec portal python manage.py migrate`.

**Próximos Passos Sugeridos:**
- NER (Etapa 4): extrair entidades (CPF, CNPJ, nomes, datas) de `DocumentText` e criar `Artifact(tipo=pessoa/empresa)` com `ArtifactLineage` apontando para o documento de origem — primeiro uso real do lineage entre artefatos de inteligência.

---

## [24-05-2026] - Especificação da Extração Adaptativa de HTML

**O que foi feito:**
- **Identificação de limitação arquitetural:**
  - A task `extract_text_from_mhtml` trata todas as páginas de forma idêntica (trafilatura sobre texto puro), o que é inadequado para extratos bancários, processos judiciais e fichas de CNPJ — tipos de página onde a estrutura tabular é o dado, não o texto narrativo.

- **Spec `docs/componentes/pipeline/extracao-adaptativa.md` (novo):**
  - Define 8 tipos de página: `artigo`, `tabular_financeiro`, `tabular_generico`, `processo_judicial`, `perfil_pessoa_juridica`, `documento_juridico`, `misto`, `desconhecido`.
  - Algoritmo de detecção em duas fases: (1) lookup no `URLPatternCache` por padrão de URL normalizado; (2) análise estrutural do HTML (table_ratio, monetary_count, process_number_count, etc.) com regras de classificação priorizadas.
  - Extratores por tipo: BeautifulSoup + heurística de colunas para `tabular_financeiro`; regex CNJ + extração de partes e movimentações para `processo_judicial`; trafilatura preservado para `artigo` e `desconhecido`.
  - Campo `structured_data` no `content` do Artifact TEXT: extrato financeiro gera JSON com lista de transações `{data, descricao, valor, saldo}`; processo judicial gera JSON com número CNJ, partes e movimentações.
  - Modelo `URLPatternCache` (novo): `tenant`, `domain`, `path_pattern` (URL normalizada com IDs substituídos por `*`), `page_type`, `confidence`, `hit_count`, `divergence_count`, `needs_review`. Isolado por tenant.
  - Lógica de aprendizado: na segunda captura do mesmo padrão, usa tipo cacheado (sem análise estrutural). Após 3 divergências entre cache e análise, marca `needs_review = true` — sinal de que o layout da página mudou (ex: nova versão do internet banking).
  - `ArtifactLineage.processor` passa a identificar extrator e versão: `extractor:tabular_financeiro:1.0`.
  - 10 critérios de aceitação definidos.

**Status Atual:**
- Especificação completa. Nenhum código escrito — objetivo desta sessão foi especificar antes de implementar.

**Próximos Passos:**
- Implementar `URLPatternCache` como modelo Django + migration.
- Implementar `detect_page_type()` e os extratores por tipo em `apps/artifacts/tasks.py` (ou módulo separado `apps/artifacts/extractors/`).
- Integrar o roteamento na task `extract_text_from_mhtml`.

---

## [20-05-2026] - Etapas 2 e 3 do Pipeline + Busca Semântica

**O que foi feito:**
- **Etapa 2 — Fragmentação de texto (`fragment_text`):**
  - Task Celery `fragment_text` em `apps/artifacts/tasks.py`: divide o artefato `texto` em chunks de 1000 chars com overlap de 100, preservando parágrafos e frases.
  - Cada chunk cria um `Artifact(tipo=fragmento)` com `ArtifactLineage(transformation='fragmentation', processor='split_text:chunk=1000,overlap=100')`.
  - Dispatch automático de `embed_fragment.delay()` para cada fragmento ao final.
  - Idempotente: verifica linhagem existente antes de reprocessar.

- **Etapa 3 — Embeddings e indexação vetorial (`embed_fragment`):**
  - Task Celery `embed_fragment` em `apps/artifacts/tasks.py`: gera vetor de 384 dimensões com `fastembed` (modelo `paraphrase-multilingual-MiniLM-L12-v2`, ONNX, CPU-only, multilíngue).
  - Upsert no Qdrant em coleção isolada por tenant: `ia_{tenant_id_sem_hifens}`, distância Cosine.
  - Payload no Qdrant: `title`, `source_url`, `fragment_index`, `text_preview`, `source_artifact_id`.
  - Salva `qdrant_point_id` e `qdrant_collection` no `content` do fragmento para rastreabilidade.
  - Idempotente: pula se `qdrant_point_id` já presente no content.
  - `scan_unprocessed_documents` atualizado com 3 gaps: doc→texto, texto→frag, frag sem qdrant_point_id.

- **Módulo `apps/artifacts/embeddings.py` (novo):**
  - Singletons lazy `get_embedding_model()` e `get_qdrant_client()` — instância única por processo worker.
  - `ensure_collection()`: cria coleção Qdrant se não existir (VectorParams dim=384, Cosine).

- **View de busca semântica (`BuscaSemanticaView`):**
  - `GET /artifacts/busca/`: formulário de busca (requer login).
  - `POST /artifacts/busca/`: gera embedding da query, busca no Qdrant da coleção do tenant do usuário, retorna top-10 por similaridade Cosine. Carrega texto completo do Artifact do banco.
  - URL registrada em `apps/artifacts/urls.py`.

- **Template `templates/artifacts/busca.html` (novo):**
  - Três estados: inicial (sem busca), sem resultados, lista de resultados.
  - Cards `<details>/<summary>` expansíveis — sem JavaScript.
  - Score exibido como porcentagem + barra CSS colorida (verde ≥70%, amarelo ≥40%, cinza <40%).
  - Cada card: score, título, URL, snippet 2 linhas, tag "trecho N"; expandido: texto completo + links "Fonte original" e "Ver captura".

- **Dashboard e navbar atualizados:**
  - Card "Busca Semântica" adicionado ao `dashboard.html`.
  - Link "Busca" adicionado à navbar em `base.html`.

- **Portal reconstruído** para incluir `fastembed==0.3.6` e `qdrant-client==1.9.2` (já estavam no `requirements.txt` mas imagem não havia sido rebuilt).

**Testes realizados:**
- Pipeline ponta-a-ponta: documento sintético criado → signal disparado → `extract_text_from_mhtml` (38ms, 179 palavras) → `fragment_text` (2 fragmentos) → `embed_fragment` × 2 (vetores indexados no Qdrant). Total < 500ms.
- Linhagem completa: `documento → texto → fragmento → Qdrant`. Todos os `ArtifactLineage` criados com transformation/processor corretos.
- Busca semântica: query "irregularidades fiscais empresa investigada" → scores 59% e 58% nos dois fragmentos do documento de teste. Sem resultados falsos positivos.
- View HTTP: `GET /artifacts/busca/` → 200, `POST` com query → 200 com resultados.

**Status Atual:**
- Pipeline completo funcional (Etapas 1–3). Do MHTML capturado até vetores indexados e buscáveis.
- Busca semântica operacional no portal web, integrada ao dashboard.

**Próximos Passos Sugeridos:**
- Etapa 4: NER — extrair entidades (CPF, CNPJ, nomes, datas) dos fragmentos e criar `Artifact(tipo=pessoa/empresa)` com linhagem.
- `SiteProfile`: LLM Discovery para aprender seletores CSS por domínio (reduz custo de extração a zero na segunda visita).
- Grafo de vínculos (Neo4j) — Fase 2 do roadmap.

---

## [20-05-2026] - Implementação da Etapa 1 do Pipeline + Correção de Integração

**O que foi feito:**
- **Implementação completa da Etapa 1 do pipeline de transformação:**
  - Modelo `ArtifactLineage` adicionado em `apps/artifacts/models.py` com tabela `artifacts_lineage`.
  - Novos tipos `texto` e `fragmento` adicionados a `Artifact.Type`.
  - Migration `0002` criada e aplicada.
  - Task Celery `extract_text_from_mhtml` em `apps/artifacts/tasks.py`: lê MHTML do MinIO, desempacota com o módulo `email`, extrai texto com `trafilatura` (modo padrão + fallback `favor_recall`), cria `Artifact(tipo=texto)` e `ArtifactLineage`.
  - Task Celery `scan_unprocessed_documents` em `apps/artifacts/tasks.py`: varre artefatos `documento` sem filho `texto` e enfileira extração — cobre histórico e garante resiliência a falhas.
  - Signal Django `dispatch_extraction_pipeline` em `apps/artifacts/signals.py`: dispara `extract_text_from_mhtml.delay()` automaticamente no `post_save` de qualquer `Artifact(tipo=documento, mhtml_path presente)`.
  - `config/celery.py` criado; `config/__init__.py` expõe `celery_app`; settings com `CELERY_BEAT_SCHEDULE` (scan a cada 2 minutos).
  - Redis 7-alpine + serviços `worker` e `beat` adicionados ao `docker-compose.yml`.

- **Bug identificado e corrigido — orchestrator bypassing Django ORM:**
  - O endpoint `POST /api/v1/capture/mhtml` do orchestrator escrevia direto no PostgreSQL via `psycopg2`, o que fazia o signal `post_save` nunca disparar.
  - Correção: orchestrator substituiu o bloco `psycopg2` por `httpx.post()` ao novo endpoint Django `POST portal:8000/artifacts/api/v1/artefatos/`.
  - Novo endpoint `ArtefatoCreateAPIView` em `apps/artifacts/views.py` encapsula a lógica de fallback (user/org) e cria o artefato via ORM, disparando o signal automaticamente.
  - `psycopg2` e `datetime` removidos das importações do orchestrator (`main.py`).

**Testes realizados:**
- Task disparada manualmente: extraiu 1166 palavras de captura existente (National Geographic Brasil). Linhagem criada corretamente.
- Signal automático: novo artefato criado → filho `texto` apareceu em < 1 segundo.
- Endpoint Django: `POST /artifacts/api/v1/artefatos/` retorna `{"artifact_id": "uuid"}` com status 201.
- Beat catch-up: ao subir, processou todos os artefatos históricos sem filho texto em paralelo.
- Fluxo real via orchestrator: artefato criado via API dispara signal → worker processa → linhagem registrada.

**Status Atual:**
- Pipeline Etapa 1 funcional ponta-a-ponta. Qualquer captura nova via extensão Chrome ou chamada ao orchestrator gera automaticamente um artefato de texto extraído com linhagem completa.

**Próximos Passos Sugeridos:**
- Etapa 2: Fragmentador — dividir o `Artifact(tipo=texto)` em chunks com overlap e criar `Artifact(tipo=fragmento)` com linhagem.
- Etapa 3: Embedder — gerar vetores com `sentence-transformers` local e indexar no Qdrant por tenant.
- Implementar `SiteProfile` (LLM Discovery para seletores CSS por domínio).

---

## [20-05-2026] - Especificação do Pipeline de Transformação de Artefatos

**O que foi feito:**
- **Conversação arquitetural:** Discussão aprofundada sobre o fluxo do dado após a captura — desde a extração de texto até a indexação semântica, NER e futuramente o grafo de vínculos.
- **Novo documento de especificação:** Criado `docs/arquitetura/pipeline-transformacao.md` descrevendo os 5 estágios do pipeline (extração → fragmentação → embedding → NER/sumarização → grafo), os novos modelos de dados e a estratégia de orquestração.
- **Decisões registradas:**
  - *Extração de texto em camadas:* `trafilatura` como extrator genérico padrão; LLM Discovery como fallback que aprende seletores CSS por domínio e salva em `SiteProfile` (LLM roda uma vez por domínio, depois é custo zero).
  - *Linhagem de dados:* Modelo `ArtifactLineage` — todo artefato derivado registra seu pai, a transformação aplicada, o processador e os parâmetros. Forma um DAG auditável de ponta a ponta.
  - *Mensageria:* Celery + Redis (já previsto na arquitetura) é suficiente para as Fases 0–4. Kafka adiado para Fase 5 (Kubernetes). Redis Streams como stepping stone intermediário se necessário.
  - *Registry dinâmico:* Modelo `SkillManifest` — skills e MCPs se auto-registram com input_type, output_type e trigger_rules. Quando uma skill nova chega, catch-up automático reprocessa artefatos históricos compatíveis.
  - *Embeddings locais:* `sentence-transformers` com `paraphrase-multilingual-mpnet-base-v2` para não enviar dados a APIs externas, compatível com a política de LLM local para dados `restrito`/`confidencial`.
- **Roadmap atualizado:** Fase 1 renomeada para "Pipeline de Transformação e RAG" com os entregáveis refinados para refletir o pipeline especificado.

**Status Atual:**
- Especificação da Fase 1 completa e coerente com a arquitetura existente. Nenhum código foi escrito nesta sessão — deliberadamente, o objetivo foi especificar antes de implementar.

**Próximos Passos Sugeridos:**
- Implementar `ArtifactLineage` e `SiteProfile` como modelos Django e gerar migrações.
- Adicionar Celery + Redis ao `docker-compose.yml`.
- Implementar a Etapa 1 do pipeline: task Celery que lê o MHTML do MinIO, extrai texto com `trafilatura` e cria um novo `Artifact` do tipo "texto" com a linhagem registrada.

---

## [19-05-2026] - Extensão de Captura e Armazenamento MHTML

**O que foi feito:**
- **Extensão do Navegador:**
  - Especificação e desenvolvimento de uma extensão Chrome (Manifest V3) capaz de capturar a página atual como um arquivo MHTML único (`chrome.pageCapture`), com o objetivo de preservar o layout e possibilitar acesso offline (cadeia de custódia).
  - Estrutura base criada em `clients/browser-extension/` (`manifest.json`, `background.js`, `popup.html`, `popup.js`).
- **Orquestrador e Armazenamento (MinIO/PostgreSQL):**
  - Implementação do endpoint `POST /api/v1/capture/mhtml` no serviço Orquestrador (FastAPI) com suporte a requisições CORS da extensão.
  - Conexão e armazenamento direto do arquivo bruto (Blob) em um bucket do MinIO.
  - Conexão banco de dados via `psycopg2` para registro automático do artefato na tabela `artifacts_artifact` do Portal (Django).
  - Implementação de fallback no backend: caso a extensão não envie dados de contexto (tenant/user), o sistema mapeia o artefato para o primeiro usuário administrador do portal e auto-cria uma organização tipo `individual` para satisfazer a constraint do banco.
- **Visualizador de Artefatos (Portal Django):**
  - Interface construída com HTML puro (Vanilla CSS Dark Mode) e JavaScript para visualização de capturas MHTML como uma SPA simples (Galeria e iframe).
  - Escrita de um Parser/Proxy Backend no Django (`ServeMHTMLView`): lê o arquivo binário do MinIO, utiliza o módulo `email` nativo do Python para descompactar o `.mhtml` dinamicamente, converte todos os *assets* (imagens, estilos) em Base64 `data:URIs` e devolve ao navegador como puro `text/html`. Isso contornou uma trava de segurança moderna do Chrome que força downloads em arquivos `multipart/related`.
  - **Sandboxing de Evidências OSINT:** Adição do atributo `sandbox=""` no iframe para desabilitar JavaScript da página capturada. Isso congela a evidência, bloqueando pop-ups e *Deep Links* maliciosos (como chamadas a `xdg-open` disparadas por *adwares* dos sites capturados) protegendo a segurança e estabilidade da aplicação.

**Status Atual:**
- Fluxo de OSINT Web ponta-a-ponta finalizado: A aba capturada é processada, atrelada a uma Organização automaticamente (Fallback MVP), salva no MinIO e listada no Visualizador Django. O arquivo é reproduzido perfeitamente offline em ambiente enclausurado (sandbox).

## [10-05-2026] - Implementação da Camada Base do Portal

**O que foi feito:**
- **Ambiente de Desenvolvimento:** 
  - Criação do `.gitignore` mapeando caches Python, ambientes virtuais (`.venv`, `.env`) e IDEs.
  - Exposição da porta `5432` do PostgreSQL no `docker-compose.override.yml` para habilitar desenvolvimento híbrido (Django local conectando no DB containerizado).
  - Configuração do `python-dotenv` em `services/portal/config/settings/base.py` para carregar as variáveis locais automaticamente.

- **Modelos do Portal (Django):**
  - Identificado que os apps `accounts` e `artifacts` já possuíam modelos criados em alinhamento estrito com a especificação da Fase 0 (User, Organization, Artifact, AuditLog, etc).
  - Criado o novo app `infrastructure` (`apps/infrastructure`) implementando todos os modelos de provedores LLM, servidores MCP e repositórios de imagens Docker, conforme definido em `docs/componentes/interfaces/web.md`.
  - Registrados os modelos recém-criados do `infrastructure` no painel de administração (`admin.py`).

**Status Atual:**
- Toda a estrutura de banco de dados (Models) do Portal web estipulada para a Fase 0 está 100% pronta e com as migrações geradas. O usuário pode rodar a aplicação via Docker ou localmente com virtualenv.

**Próximos Passos Sugeridos:**
- Implementação do frontend no Portal: Configurar a UI baseada em Tailwind CSS e HTMX (`templates/base.html`).
- Implementar as views básicas de Autenticação (Login) e Dashboard do Portal.
