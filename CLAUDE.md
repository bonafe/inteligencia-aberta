# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Comandos de desenvolvimento

```bash
# Subir todos os serviços (modo desenvolvimento com hot-reload)
docker compose -f docker-compose.yml -f docker-compose.override.yml up

# Subir em produção (background) — SEM o override de dev (que o compose carregaria
# sozinho sem -f). Host com domínio público: acrescentar --profile publico (Caddy).
# Detalhes, segredos e contrato para automação: docs/deploy.md
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# Migrations e admin (após subir)
docker compose exec portal python manage.py migrate
docker compose exec portal python manage.py createsuperuser
docker compose exec portal python manage.py shell

# Recriar apenas um serviço
docker compose up --build orchestrator

# Logs em tempo real
docker compose logs -f orchestrator

# Testes
docker compose exec portal python -m pytest tests/ -q

# Diagnóstico do pipeline (ver docs/componentes/observabilidade.md)
docker compose exec portal python manage.py reprocessar_captura <artifact_id|url> --forcar
docker compose exec portal python manage.py reprocessar_incompletos --criterio dom2parser --simular
docker compose exec portal python manage.py reconstruir_projecoes
```

O painel ao vivo do pipeline fica em `http://localhost:8000/eventos/`.

Não existe Makefile, CI/CD, linting ou pre-commit configurados. Ao adicionar linting, usar ruff. Os testes usam pytest + pytest-django (`services/portal/pytest.ini`, suíte em `services/portal/tests/`).

## Arquitetura de serviços

Três microserviços Python + workers de processamento:

| Serviço | Stack | Porta | Papel |
|---------|-------|-------|-------|
| `portal` | Django 5.0.6 | 8000 | Interface web, modelos de dados, admin, API interna |
| `orchestrator` | FastAPI + LangGraph | 8001 | Captura MHTML, grafo de investigação, motor de política |
| `mcp` | FastAPI + httpx | 8002 | Ferramentas externas (CNPJ, processos, notícias) |
| `worker` | Celery 5.4 | — | Executa tasks assíncronas do pipeline |
| `beat` | Celery Beat | — | Agenda tasks periódicas (catch-up scan a cada 2min) |

Em produção só portal (8000) e orchestrator (8001) publicam porta, presas a `BIND_ADDR` (padrão `127.0.0.1`); MCP, Postgres, Redis, Qdrant e Garage (S3) ficam na rede interna. As portas 8002, 5432 e 3900 só são publicadas em dev (`docker-compose.override.yml`).

Infraestrutura de suporte: PostgreSQL 16-alpine (5432), Qdrant v1.9.0 (6333), Garage (armazenamento S3, imagem própria em `infra/garage/`, `GARAGE_VERSION`; 3900; substituiu o MinIO — ADR 008), Ollama opcional por nó (profile `ollama`; Macs usam o nativo — ADR 009), Redis 7-alpine (6379 — banco 0 para o Celery, banco 1 para o channel layer do painel de eventos).

O `portal` roda sob **ASGI (daphne)**, não WSGI: o painel de eventos usa WebSocket. Em desenvolvimento, `manage.py runserver` já sobe em ASGI porque `daphne` é o primeiro item de `INSTALLED_APPS`.

**Fluxo de captura MHTML (funcional):**
```
Extensão Chrome → POST orchestrator:8001/api/v1/capture/mhtml
  → Garage/S3 (armazena MHTML bruto)
  → POST portal:8000/artifacts/api/v1/artefatos/  (Django ORM → signal)
  → Celery worker: extract_text_from_mhtml
  → Artifact(tipo=texto) + ArtifactLineage criados
```

**Fluxo de investigação:** `POST /investigar` no orchestrator → `policy_engine.check()` → grafo LangGraph (`planejador → coletor → redator`) → chamadas HTTP ao MCP → resposta em markdown. (Agentes ainda são stubs — Fase 0.)

## Estrutura de código crítica

**`services/orchestrator/policy_engine.py`** — motor de política determinístico (sem LLM). Define quatro níveis de classificação (`público`, `interno`, `restrito`, `confidencial`) com regras sobre uso de LLM externo e obrigatoriedade de auditoria. Toda decisão de acesso passa por aqui.

**`services/orchestrator/graph.py`** — grafo LangGraph com `InvestigationState` (TypedDict). Nós: `planejador`, `coletor`, `redator`. Os três agentes em `services/orchestrator/agents/` são stubs a implementar (Fase 0).

**`services/portal/apps/accounts/`** — multi-tenancy. `User` com UUID PK, `Organization` (INDIVIDUAL/TEAM/INSTITUTIONAL), `Membership` com papéis (OWNER/ADMIN/MEMBER/GUEST).

**`services/portal/apps/artifacts/`** — modelo de dados central.
- `models.py`: `Artifact` (tipos: pessoa/empresa/documento/processo/texto/fragmento), `ArtifactLineage` (rastreia pai→filho + transformação + processador), `AuditLog`, `Sharing`.
- `tasks.py`: `extract_text_from_mhtml` (Etapa 1 do pipeline), `scan_unprocessed_documents` (catch-up periódico via Beat).
- `signals.py`: `post_save` em `Artifact` dispara a task de extração automaticamente.
- `views.py`: `ArtefatoCreateAPIView` — endpoint interno `POST /artifacts/api/v1/artefatos/` usado pelo orchestrator para criar artefatos via ORM (necessário para o signal disparar).

**`services/portal/apps/events/`** — log de eventos e observabilidade (transversal aos três serviços).
- `models.py`: `PipelineEvent` (append-only, ordem total por `sequence`, correlação ponta a ponta, identidade do nó) e `PipelineRun` (projeção reconstruível, uma linha por captura).
- `emit.py`: `emit()` e o gerenciador de contexto `etapa()`. **Nunca levantam exceção** — observabilidade não pode derrubar o pipeline. Trunca payload em 8 KB e descarta chaves de conteúdo/segredo.
- `context.py`: `contextvars` de correlação — é o que permite instrumentar sem mudar assinatura de função.
- `celery_signals.py`: fila/início/fim/falha/retry de toda task, automático, propagando a correlação por header.
- `projecao.py`: `aplicar_evento()`, a única escrita em `PipelineRun`; usada tanto no caminho incremental quanto na reconstrução.
- `consumers.py`: WebSocket do painel; assina apenas os grupos das organizações do usuário.

**Classificação por domínio** (`apps/artifacts/classificacao_dominio.py`, model `RegraClassificacaoDominio`) — regra "tudo de `dominio.com.br` nasce, no mínimo, nível X", aplicada na criação do artefato em `ArtefatoCreateAPIView`; só sobe o nível, casa por sufixo de rótulo, não reclassifica o existente. Não altera o `policy_engine`.

**`services/portal/apps/federacao/`** — base da federação entre instâncias (ADR 010; só a F0 existe, o resto é desenho em `docs/arquitetura/federacao.md`). `ChaveInstancia` guarda o par Ed25519 da instância com a privada cifrada; `did.py` e `chaves.py` geram o `did:key`, assinam e verificam. `Artifact.blob_hash` (RFC 6920, `ni:`) é a identidade de conteúdo do MHTML, calculada no orchestrator (`conteudo_hash.py`, com cópia em cada serviço). Fora de `apps/cluster` de propósito: cluster é confiança única, federação não.

**Implantação** (ver `docs/deploy.md` e `docs/arquitetura/decisoes/007-implantacao-por-instancia.md`):
- `docker-compose.prod.yml` — restart, portas em `BIND_ADDR`, serviço `bootstrap` (one-shot) e `caddy` (profile `publico`). `infra/caddy/Caddyfile` publica só o portal e `/api/v1/capture/*`.
- `config/settings/production.py` — `TLS_MODE` (`proxy`|`none`), `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`; valida segredos na subida (`config/segredos.py`, com cópias em `orchestrator/` e `mcp/`).
- `config/health.py` — `/health` do portal (checa o banco); orchestrator e mcp têm o seu. Devolvem `INSTANCIA_NOME` e `IA_VERSION`.
- `apps/accounts/management/commands/bootstrap_instancia.py` — migrations, buckets do Garage (S3) e superusuário inicial (`DJANGO_SUPERUSER_*`), idempotente.
- Registro: `/registro/` fecha (403) após o primeiro usuário, salvo `REGISTRO_ABERTO=true` (padrão em dev).

**`services/portal/config/celery.py`** — app Celery do projeto. `config/__init__.py` o exporta para que `celery -A config` funcione.

**`services/mcp/tools/cnpj.py`** — única ferramenta funcional (BrasilAPI). `processos.py` e `noticias.py` são stubs.

## Convenções

- **Idioma:** domínio e nomes de negócio em português (agentes, campos, endpoints); infraestrutura e código técnico em inglês. Commits em português.
- **Python:** Python 3.12, async/await, type hints com Pydantic e TypedDict. UUIDs como PKs padrão.
- **Settings Django:** três ambientes em `services/portal/config/settings/` — `base.py`, `development.py`, `production.py`. Variável `DJANGO_SETTINGS_MODULE` controla qual usar.
- **Produção × dev:** produção é `-f docker-compose.yml -f docker-compose.prod.yml`; `docker compose up` sem `-f` carrega o override de dev (runserver, `--reload`, portas abertas). Segredos com valor `CHANGE_ME` ou vazio são recusados em produção.
- **Classificação de dados:** os quatro níveis do `policy_engine` (`público → confidencial`) determinam se LLM externo pode ser usado e se auditoria é exigida. Esse contrato não deve ser quebrado.

## Fase de desenvolvimento: compatibilidade com outras instâncias não é requisito (por ora)

O projeto ainda não está em produção e **o dono pode zerar tudo** (bancos, Garage, log) quando precisar. Enquanto ele não disser o contrário:

- **Não se preocupe com outras instâncias nem com dados já existentes.** Pode mudar formato de envelope, IDs, nomes de campo, schema e contratos entre serviços sem escrever migração de dados, backfill, camada de compatibilidade ou fallback para o formato antigo. Prefira o desenho certo a um caminho de transição.
- **Mesmo assim**, as migrations do Django continuam sendo geradas normalmente, e os serviços **da mesma instância** têm de continuar consistentes entre si (portal, orchestrator, mcp, worker).
- **Avise quando uma mudança invalidar dados existentes** (ex.: novo formato de hash ou de ID, campo obrigatório novo), numa linha no resumo, para o dono decidir se zera. Não trate isso como bloqueio nem peça confirmação antes de mudar.
- **Isto não afrouxa "O que nunca tocar"** (abaixo): `policy_engine`, `AuditLog`, UUIDs como PKs, níveis de classificação, `PipelineEvent` e a semântica de `status` seguem valendo — são contratos do próprio sistema, não de compatibilidade com terceiros.
- **Quando o dono disser que agora é preciso compatibilidade** com outras instâncias (por exemplo, antes do primeiro intercâmbio real da federação, ADR 010), esta seção deve ser removida ou revista, e as decisões "difíceis de reverter" do `docs/arquitetura/federacao.md` (seção 7) passam a valer como restrição de verdade.

## O que nunca tocar

- **`policy_engine.py`** — é intencionalmente determinístico. Não adicionar lógica de LLM nem condições que dependam de heurísticas. Qualquer mudança nas regras de classificação impacta auditoria, compliance e multi-tenancy.
- **`AuditLog` (artifacts/models.py)** — o modelo de auditoria não deve ter campos removidos nem registro suprimido. Toda operação auditada deve sempre criar uma entrada.
- **UUIDs como PKs** — todos os modelos usam UUID. Não trocar por inteiros sequenciais.
- **Níveis de classificação** — os quatro valores (`público`, `interno`, `restrito`, `confidencial`) são contratos de API entre serviços. Renomear quebra o orchestrator, o portal e futuramente o RAG pipeline.
- **`PipelineEvent` (events/models.py)** — append-only como o `AuditLog`: nenhum processo faz `UPDATE` ou `DELETE`. Não é substituto do `AuditLog` (aquele é trilha de compliance, este é diário operacional) — não fundir os dois. E `emit()` jamais pode passar a propagar exceção: uma falha de observabilidade não pode quebrar uma captura.
- **Semântica de `status` nos eventos** — `vazio` ("rodou e não produziu") é distinto de `falhou` ("quebrou"). Essa distinção é a razão de ser do modelo; colapsá-la devolve o sistema ao estado em que um `NULL` no banco não dizia nada.

## Documentação de referência

A pasta `docs/` contém ~2 400 linhas de especificação:

- `docs/roadmap.md` — 6 fases; fase 0 (MVP local) ainda em implementação
- `docs/arquitetura/visao-geral.md` — visão de 5 camadas e fluxos de dados
- `docs/arquitetura/decisoes/` — 9 ADRs explicando escolhas de MCP, containers, LLM local, voz, log de eventos, cluster multi-máquina, implantação por instância, armazenamento S3 (Garage) e Ollama como capacidade do nó
- `docs/componentes/agentes/` — spec detalhada de cada agente (planejador, coletor, extrator, correlacionador, validador, analista, redator)
- `docs/seguranca/classificacao.md` — regras completas do motor de política
- `docs/arquitetura/federacao.md` — **proposta em discussão** (não implementada): federação entre instâncias, espaços, JSON-LD/PROV-O, identidade, roadmap e decisões pendentes
- `docs/componentes/observabilidade.md` — log de eventos, taxonomia de `stage`/`status`, painel e reprocessamento
- `docs/deploy.md` — implantação em produção: segredos, variáveis, HTTPS, Caddy, bootstrap, contrato para automação

Antes de implementar um agente ou ferramenta nova, ler a spec correspondente em `docs/`.
