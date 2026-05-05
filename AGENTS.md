# AGENTS.md

Guia para agentes de IA (Codex, Claude, Gemini, etc.) que operam neste repositório.

## Contexto do projeto

Plataforma de agentes de IA que transforma dados públicos em inteligência acionável para cidadãos brasileiros. Fase atual: **Fase 0 — MVP local**, com foco em implementar o grafo de investigação e as ferramentas MCP básicas.

Stack: Python 3.12, Django 5.0.6, FastAPI 0.111.0, LangGraph 0.1.19, LangChain-Anthropic 0.1.19, PostgreSQL 16, Qdrant v1.9.0, MinIO, Docker Compose.

## Comandos para validar mudanças

```bash
# Verificar se os serviços sobem sem erro
docker compose -f docker-compose.yml -f docker-compose.override.yml up --build

# Checar sintaxe Python sem subir container
python -m py_compile services/orchestrator/graph.py
python -m py_compile services/mcp/main.py
python -m py_compile services/portal/manage.py

# Validar migrations Django
docker compose exec portal python manage.py migrate --check

# Endpoint de saúde
curl http://localhost:8001/health
curl http://localhost:8002/health
```

Não há suite de testes ainda. Ao criar testes, colocá-los em `tests/` dentro de cada serviço e usar pytest.

## Mapa de responsabilidades por arquivo

| Arquivo | Responsabilidade |
|---------|-----------------|
| `services/orchestrator/policy_engine.py` | Controle de acesso determinístico — nunca adicionar LLM aqui |
| `services/orchestrator/graph.py` | Grafo LangGraph — define `InvestigationState` e sequência de nós |
| `services/orchestrator/agents/planejador.py` | **TODO Fase 0** — decompor query em passos de coleta |
| `services/orchestrator/agents/coletor.py` | **TODO Fase 0** — executar passos via MCP tools |
| `services/orchestrator/agents/redator.py` | **TODO Fase 0** — sintetizar dados coletados em relatório markdown |
| `services/mcp/tools/cnpj.py` | Funcional — consulta BrasilAPI |
| `services/mcp/tools/processos.py` | **TODO** — integrar DataJud/CNJ |
| `services/mcp/tools/noticias.py` | **TODO** — integrar NewsAPI ou RSS |
| `services/portal/apps/artifacts/models.py` | Modelos centrais: Artifact, AuditLog, Sharing |
| `services/portal/apps/accounts/models.py` | Multi-tenancy: User, Organization, Membership, Team |
| `services/portal/config/settings/` | Django settings por ambiente (base/development/production) |

## Regras obrigatórias para agentes

### Nunca faça

- **Não modifique `policy_engine.py` para incluir LLM ou heurísticas.** O motor de política é intencionalmente determinístico para garantir auditabilidade.
- **Não remova campos de `AuditLog` nem torne o registro opcional.** Auditoria de acesso é requisito de compliance.
- **Não troque UUIDs por PKs inteiros** em nenhum modelo Django.
- **Não renomeie os níveis de classificação** (`público`, `interno`, `restrito`, `confidencial`) — são contratos entre serviços.
- **Não commite `.env`** — use `.env.example` como referência.
- **Não escreva lógica de negócio no portal Django** que deveria estar no orchestrator. O portal é interface e persistência; o orchestrator é processamento.

### Sempre faça

- Leia a spec em `docs/componentes/agentes/<agente>.md` antes de implementar um agente.
- Mantenha nomes de domínio em português (campos, agentes, endpoints) e infraestrutura em inglês.
- Use `async/await` em FastAPI e type hints em todo código novo.
- Ao adicionar uma ferramenta MCP nova, registre o endpoint em `services/mcp/main.py` e documente em `docs/componentes/mcp.md`.
- Ao alterar o `InvestigationState` em `graph.py`, verifique se todos os agentes que leem/escrevem esse estado ainda estão coerentes.

## Classificação de dados — contrato entre serviços

O `policy_engine` define quatro níveis com regras fixas:

| Nível | LLM externo | Embeddings externos | Auditoria |
|-------|------------|---------------------|-----------|
| `público` | Permitido | Permitido | Não obrigatória |
| `interno` | Permitido | Permitido | Obrigatória |
| `restrito` | **Proibido** | **Proibido** | Obrigatória |
| `confidencial` | **Proibido** | **Proibido** | Obrigatória em todo acesso |

Ao implementar os agentes, verificar `classification` no `InvestigationState` antes de chamar qualquer API externa.

## Fluxo git

- Branch principal: `main`
- Commits em português, no imperativo: `adiciona`, `corrige`, `refatora`, `remove`
- Não há proteção de branch configurada — mas não force-push em `main`
- Não há CI/CD — validar manualmente antes de push

## Referências para implementação

Ao implementar funcionalidades da Fase 0, consultar nesta ordem:

1. `docs/roadmap.md` — escopo exato da fase
2. `docs/componentes/agentes/<nome>.md` — spec detalhada do agente
3. `docs/arquitetura/visao-geral.md` — posição do componente na arquitetura
4. `docs/seguranca/classificacao.md` — restrições de segurança aplicáveis
