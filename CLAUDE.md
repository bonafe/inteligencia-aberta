# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Comandos de desenvolvimento

```bash
# Subir todos os serviços (modo desenvolvimento com hot-reload)
docker compose -f docker-compose.yml -f docker-compose.override.yml up

# Subir em produção (background)
docker compose up -d

# Migrations e admin (após subir)
docker compose exec portal python manage.py migrate
docker compose exec portal python manage.py createsuperuser
docker compose exec portal python manage.py shell

# Recriar apenas um serviço
docker compose up --build orchestrator

# Logs em tempo real
docker compose logs -f orchestrator
```

Não existe Makefile, CI/CD, pytest, linting ou pre-commit configurados. Ao adicionar testes, usar pytest; ao adicionar linting, usar ruff.

## Arquitetura de serviços

Três microserviços Python em containers independentes:

| Serviço | Stack | Porta | Papel |
|---------|-------|-------|-------|
| `portal` | Django 5.0.6 | 8000 | Interface web, modelos de dados, admin |
| `orchestrator` | FastAPI + LangGraph | 8001 | Grafo de investigação, motor de política |
| `mcp` | FastAPI + httpx | 8002 | Ferramentas externas (CNPJ, processos, notícias) |

Infraestrutura de suporte: PostgreSQL 16-alpine (porta 5432), Qdrant v1.9.0 (6333), MinIO latest (9000/9001).

O fluxo de uma investigação é: `POST /investigar` no orchestrator → `policy_engine.check()` → grafo LangGraph (`planejador → coletor → redator`) → chamadas HTTP ao MCP → resposta em markdown.

## Estrutura de código crítica

**`services/orchestrator/policy_engine.py`** — motor de política determinístico (sem LLM). Define quatro níveis de classificação (`público`, `interno`, `restrito`, `confidencial`) com regras sobre uso de LLM externo e obrigatoriedade de auditoria. Toda decisão de acesso passa por aqui.

**`services/orchestrator/graph.py`** — grafo LangGraph com `InvestigationState` (TypedDict). Nós: `planejador`, `coletor`, `redator`. Os três agentes em `services/orchestrator/agents/` são stubs a implementar (Fase 0).

**`services/portal/apps/accounts/`** — multi-tenancy. `User` com UUID PK, `Organization` (INDIVIDUAL/TEAM/INSTITUTIONAL), `Membership` com papéis (OWNER/ADMIN/MEMBER/GUEST).

**`services/portal/apps/artifacts/`** — modelo de dados central. `Artifact` com tipo, classificação e organização dona. `AuditLog` registra todo acesso. `Sharing` controla compartilhamento granular com expiração e revogação.

**`services/mcp/tools/cnpj.py`** — única ferramenta funcional (BrasilAPI). `processos.py` e `noticias.py` são stubs.

## Convenções

- **Idioma:** domínio e nomes de negócio em português (agentes, campos, endpoints); infraestrutura e código técnico em inglês. Commits em português.
- **Python:** Python 3.12, async/await, type hints com Pydantic e TypedDict. UUIDs como PKs padrão.
- **Settings Django:** três ambientes em `services/portal/config/settings/` — `base.py`, `development.py`, `production.py`. Variável `DJANGO_SETTINGS_MODULE` controla qual usar.
- **Classificação de dados:** os quatro níveis do `policy_engine` (`público → confidencial`) determinam se LLM externo pode ser usado e se auditoria é exigida. Esse contrato não deve ser quebrado.

## O que nunca tocar

- **`policy_engine.py`** — é intencionalmente determinístico. Não adicionar lógica de LLM nem condições que dependam de heurísticas. Qualquer mudança nas regras de classificação impacta auditoria, compliance e multi-tenancy.
- **`AuditLog` (artifacts/models.py)** — o modelo de auditoria não deve ter campos removidos nem registro suprimido. Toda operação auditada deve sempre criar uma entrada.
- **UUIDs como PKs** — todos os modelos usam UUID. Não trocar por inteiros sequenciais.
- **Níveis de classificação** — os quatro valores (`público`, `interno`, `restrito`, `confidencial`) são contratos de API entre serviços. Renomear quebra o orchestrator, o portal e futuramente o RAG pipeline.

## Documentação de referência

A pasta `docs/` contém ~2 400 linhas de especificação:

- `docs/roadmap.md` — 6 fases; fase 0 (MVP local) ainda em implementação
- `docs/arquitetura/visao-geral.md` — visão de 5 camadas e fluxos de dados
- `docs/arquitetura/decisoes/` — 4 ADRs explicando escolhas de MCP, containers, LLM local e voz
- `docs/componentes/agentes/` — spec detalhada de cada agente (planejador, coletor, extrator, correlacionador, validador, analista, redator)
- `docs/seguranca/classificacao.md` — regras completas do motor de política

Antes de implementar um agente ou ferramenta nova, ler a spec correspondente em `docs/`.
