# AGENTS.md

Guia para agentes de IA (Codex, Claude, Gemini, etc.) que operam neste repositório.

## Contexto do projeto

Plataforma de agentes de IA que transforma dados públicos em inteligência acionável para cidadãos brasileiros. Fase atual: **Fase 0 em conclusão / início de Fase 1** — pipeline de captura MHTML ponta-a-ponta funcional, pipeline de transformação de texto (Etapa 1) implementado. Próximo: fragmentação, embeddings, NER.

Stack: Python 3.12, Django 5.0.6, FastAPI 0.111.0, LangGraph 0.1.19, LangChain-Anthropic 0.1.19, Celery 5.4.0, Redis 7, trafilatura 1.12.2, PostgreSQL 16, Qdrant v1.9.0, Garage (S3), Docker Compose.

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

# Endpoint de saúde (portal, orchestrator e mcp respondem /health; 8002 só é publicado em dev)
curl http://localhost:8000/health
curl http://localhost:8001/health
curl http://localhost:8002/health

# Validar o compose de produção (sem subir)
docker compose -f docker-compose.yml -f docker-compose.prod.yml config -q

# Ultima Agora: testes do back e do front (Chrome headless)
docker compose exec portal python -m pytest tests/test_agora_*.py -q
scripts/testar_agora_ia.sh
```

Produção usa `docker-compose.prod.yml` **em vez do** override de dev — ver `docs/deploy.md`.

Os testes do portal usam pytest + pytest-django (`services/portal/tests/`): `docker compose exec portal python -m pytest tests/ -q` (a suíte completa leva mais de 2 minutos). Se o container parecer enxergar código antigo (`makemigrations` dizendo "sem mudanças" depois de mudar um model), recrie-o: `docker compose up -d --force-recreate portal worker beat`. Para os serviços FastAPI ainda não há suíte; ao criá-la, colocar em `tests/` dentro do serviço e usar pytest.

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
| `services/portal/apps/artifacts/models.py` | Modelos: Artifact (tipos incl. texto/fragmento), ArtifactLineage, AuditLog, Sharing |
| `services/portal/apps/artifacts/tasks.py` | Tasks Celery: `extract_text_from_mhtml`, `scan_unprocessed_documents` |
| `services/portal/apps/artifacts/signals.py` | Signal post_save → dispara pipeline automaticamente |
| `services/portal/apps/artifacts/views.py` | Views web + `ArtefatoCreateAPIView` (API interna para orchestrator) |
| `services/portal/config/celery.py` | App Celery — não modificar sem entender impacto no worker/beat |
| `services/portal/apps/accounts/models.py` | Multi-tenancy: User, Organization, Membership, Team |
| `services/portal/config/settings/` | Django settings por ambiente (base/development/production) |
| `services/portal/apps/accounts/permissoes.py` | Papéis: `exige_admin`/`eh_admin` (só OWNER/ADMIN vigentes); `orgs_do_usuario` continua sendo o isolamento por organização |
| `services/portal/apps/artifacts/alegacoes.py` | **Único** caminho para criar `Claim`/`Evidence` (`registrar_alegacao`); alegação é imutável e nasce com evidência |
| `services/portal/apps/artifacts/classificacao_dominio.py` | Regra "tudo deste domínio nasce, no mínimo, nível X": só sobe o nível, casa por sufixo de rótulo |
| `services/portal/apps/federacao/` | Chave Ed25519 da instância, `Space`, motor de regras de replicação (`regras.py`, função pura), canal assinado (`canal.py`, `views_controle.py`) |
| `services/portal/apps/cluster/` | `Maquina` = a instância local ou um **par**; enrolamento (`pares.py`), pull do estado dos pares (`pull.py`), roteador de LLM e **controle dos modelos do Ollama** (`ollama_admin.py`, `operacoes.py`, `views_modelos.py`) |
| `services/portal/apps/agora/` | Ultima Agora (ADR 013/014): `acesso.py` calcula o **papel efetivo** (o papel na organização é o teto), `sync.py` emite os tokens do `agora-sync`, `views_dominio.py` lê o domínio para os componentes `ia-*` (isolado por organização; sem acesso = 404) |
| `services/portal/static/agora/` e `services/agora-sync/` | **Cópias** do projeto `ultima-agora`, geradas por `scripts/sincronizar_agora.sh` — **não editar**; mude na origem e sincronize (o script também regenera `static/agora-ia/precache.json`) |
| `services/portal/static/agora-ia/` | Parte do IA: host Django (`django_host.js`), pacote de domínio `pack/` (`ia-search`, `ia-entity`, `ia-news`), política de cache offline (`offline_cache.js`). Testes: `scripts/testar_agora_ia.sh` |

## Regras obrigatórias para agentes

### Nunca faça

- **Não modifique `policy_engine.py` para incluir LLM ou heurísticas.** O motor de política é intencionalmente determinístico para garantir auditabilidade.
- **Não remova campos de `AuditLog` nem torne o registro opcional.** Auditoria de acesso é requisito de compliance.
- **Não troque UUIDs por PKs inteiros** em nenhum modelo Django.
- **Não renomeie os níveis de classificação** (`público`, `interno`, `restrito`, `confidencial`) — são contratos entre serviços.
- **Não commite `.env`** — use `.env.example` como referência.
- **Não crie `Claim`/`Evidence` direto** (`Claim.objects.create`): use `registrar_alegacao`, que valida e grava alegação e evidência juntas.
- **Não exponha o Ollama nem crie rota que repasse tráfego a ele.** Ele não tem autenticação; entre instâncias o caminho é o canal assinado (`apps/federacao`), e o receptor **não consegue verificar** o papel que a origem afirma — por isso `próprio` é um rótulo que dá poder e exige confirmação.
- **Não afirme no site (`index.html`, `jornada.html`, `diario.html`) mais do que o código faz:** um item só entra em "Funciona hoje" quando existe e roda. Entradas do diário levam a assinatura do dono: escreva-as como rascunho.
- **Compatibilidade com outras instâncias e com dados existentes não é requisito por ora** (o dono pode zerar tudo); veja a seção correspondente do `CLAUDE.md`, que é a referência mais completa.
- **Não edite `services/portal/static/agora/` nem `services/agora-sync/`:** são cópias do projeto `ultima-agora`. Mude na origem e rode `scripts/sincronizar_agora.sh` (regenera também o `precache.json`; o teste do front confere que está em dia).
- **Não escreva lógica de negócio no portal Django** que deveria estar no orchestrator. O portal é interface e persistência; o orchestrator é processamento.
- **Não crie artefatos com psycopg2 direto no banco a partir do orchestrator.** O orchestrator deve chamar `POST portal:8000/artifacts/api/v1/artefatos/` para que o signal Django dispare o pipeline. Escrever diretamente no banco bypassa o ORM e o signal nunca é acionado.

### Sempre faça

- Leia a spec em `docs/componentes/agentes/<agente>.md` antes de implementar um agente.
- Mantenha nomes de domínio em português (campos, agentes, endpoints) e infraestrutura em inglês.
- Use `async/await` em FastAPI e type hints em todo código novo.
- Ao adicionar uma ferramenta MCP nova, registre o endpoint em `services/mcp/main.py` e documente em `docs/componentes/mcp.md`.
- Ao alterar o `InvestigationState` em `graph.py`, verifique se todos os agentes que leem/escrevem esse estado ainda estão coerentes.
- Atualize o arquivo `CHANGELOG_IA.md` na raiz do projeto detalhando o que você fez, para manter o histórico claro para humanos e outras IAs.
- Ao criar uma nova transformação de artefato (nova Etapa do pipeline), registre `ArtifactLineage` com `transformation`, `processor` e `parameters`. A linhagem é o contrato de rastreabilidade do sistema.
- Ao adicionar uma nova task Celery, registre-a também no `scan_unprocessed_documents` se ela precisar processar artefatos históricos.

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
