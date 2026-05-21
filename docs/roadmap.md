# Roadmap

## Critério de priorização

Cada fase deve entregar valor real a pelo menos uma persona. Nenhuma fase é "só infraestrutura" — toda entrega técnica habilita um caso de uso concreto.

---

## Fase 0 — Fundação (atual)

**Objetivo:** Sistema funcionando localmente, de ponta a ponta, com caso de uso básico de OSINT.

**Entregáveis:**
- [ ] `docker-compose` com contêineres básicos rodando (PostgreSQL, MinIO, Qdrant)
- [ ] Orquestrador com LangGraph — ciclo de inteligência básico
- [ ] Agentes: Planejador, Coletor, Redator
- [ ] Ferramentas iniciais via MCP: `consultar_cnpj`, `buscar_processos`, `buscar_noticias`
- [ ] Interface web (Django) — entrada de consulta, visualização de relatório simples
- [ ] Classificação de dados implementada (estrutura de metadados)
- [ ] Motor de Políticas básico (bloqueia LLM externo para dados restrito/confidencial)

**Caso de uso habilitado:** UC-05 parcial — investigação de empresa com dados públicos.

**Persona atendida:** P7 (Rafael) e P3 (Priya) com funcionalidade limitada.

---

## Fase 1 — Pipeline de Transformação e RAG

**Objetivo:** O sistema processa automaticamente cada artefato capturado — extrai texto, fragmenta, indexa semanticamente e reconhece entidades. Usuário consegue buscar por similaridade e trazer seus próprios documentos.

**Entregáveis:**

*Pipeline de transformação (base de tudo):*
- [ ] Celery + Redis como fila de tarefas assíncronas (já previsto na arquitetura)
- [ ] `ArtifactLineage` — modelo de linhagem: todo artefato derivado sabe de onde veio e como foi transformado
- [ ] Etapa 1: extração de texto com `trafilatura` (genérica, sem LLM)
- [ ] Etapa 1b: `SiteProfile` — extração aprendida por domínio via LLM Discovery (LLM roda uma vez, seletores CSS ficam salvos)
- [ ] Etapa 2: fragmentação em chunks com overlap configurável
- [ ] Etapa 3: embedding com `sentence-transformers` local (`paraphrase-multilingual-mpnet-base-v2`)
- [ ] Etapa 4a: NER básico — extração de CPF, CNPJ, nomes, datas de texto não estruturado
- [ ] `SkillManifest` — registry de skills com catch-up automático para artefatos históricos
- [ ] Isolamento de índice vetorial por organização (collections separadas no Qdrant)

*Documentos do usuário:*
- [ ] Envio de documentos na interface web (PDF, imagem)
- [ ] Agente Extrator com OCR (integrado ao mesmo pipeline)
- [ ] LLM local integrado (Ollama) — para dados restrito/confidencial

*Compartilhamento:*
- [ ] Controle de compartilhamento básico (criar, revogar)

**Spec de referência:** [`docs/arquitetura/pipeline-transformacao.md`](../arquitetura/pipeline-transformacao.md)

**Caso de uso habilitado:** UC-02 (análise financeira do MEI), UC-03 parcial. Qualquer captura MHTML da Fase 0 passa a ser buscável semanticamente.

**Persona atendida:** P2 (Carlos), P7 (Rafael) com busca sobre capturas existentes.

---

## Fase 2 — Agentes Completos e Grafo de Vínculos

**Objetivo:** Investigação completa com correlação e grafo.

**Entregáveis:**
- [ ] Agentes Correlacionador, Validador e Analista
- [ ] Neo4j integrado — grafo de vínculos
- [ ] Visualização de grafo na interface web
- [ ] Relatório com grafo, fontes e grau de confiança
- [ ] Exportação PDF
- [ ] Alertas de movimentação processual

**Caso de uso habilitado:** UC-05 completo, UC-03 completo.

**Persona atendida:** P3 (Priya), P4 (João), P7 (Rafael).

---

## Fase 3 — Interface de Chat e Voz

**Objetivo:** Qualquer cidadão consegue usar o sistema sem letramento digital.

**Entregáveis:**
- [ ] Contêiner de chat (interface de linguagem natural)
- [ ] Contêiner de voz (STT + TTS local, PT-BR)
- [ ] Adaptador de resposta para voz (resumo conversacional)
- [ ] Suporte a múltiplos turnos (contexto de conversa)
- [ ] Envio de documento por voz (descrição) ou via chat
- [ ] Resposta adaptada por persona

**Caso de uso habilitado:** UC-01 (Dona Maria por voz), UC-06 (IR assistido).

**Persona atendida:** P1 (Dona Maria), P4 (João).

---

## Fase 4 — Múltiplas Organizações e Compartilhamento Avançado

**Objetivo:** Uso institucional e compartilhamento entre usuários diferentes.

**Entregáveis:**
- [ ] Isolamento completo por organização com segregação de rede
- [ ] Compartilhamento com validade e revogação
- [ ] Registro de auditoria por organização, exportável
- [ ] Suporte a equipes dentro de uma organização
- [ ] Políticas de acesso configuráveis por organização
- [ ] Notificações de compartilhamento

**Caso de uso habilitado:** UC-04 (paciente → médico), UC-07 (institucional).

**Persona atendida:** P5 (Dra. Fernanda), P6 (Auditor da Receita Federal).

---

## Fase 5 — Escala e Resiliência

**Objetivo:** Sistema pronto para múltiplos usuários simultâneos e operação contínua.

**Entregáveis:**
- [ ] Migração de docker-compose para Kubernetes / Docker Swarm
- [ ] Escalabilidade horizontal de agentes e workers
- [ ] Monitoramento: Prometheus + Grafana
- [ ] Rastreamento de agentes (LangSmith ou similar)
- [ ] SLA definido e monitorado
- [ ] Cópia de segurança e recuperação documentados e testados

---

## Fase 6 — Acesso Público e Parceria com Estado (visão de longo prazo)

**Objetivo:** Viabilizar acesso do sistema como serviço público.

**Entregáveis:**
- [ ] API pública para integração com sistemas governamentais
- [ ] Modelo de provisionamento de LLM público (parceria com governo)
- [ ] Versão gratuita com capacidade limitada para acesso universal
- [ ] Conformidade formal com LGPD auditada por terceiros
- [ ] Documentação de uso para políticas públicas

**Contexto:** Esta fase depende de articulação política além do desenvolvimento técnico. O objetivo é que o Estado ofereça infraestrutura de LLM como serviço para seus cidadãos — o Inteligência Aberta seria a camada de aplicação sobre essa infraestrutura pública.

---

## Dependências entre Fases

```
Fase 0 → Fase 1 → Fase 2 → Fase 3
                         ↘ Fase 4
Fase 2 + Fase 4 → Fase 5 → Fase 6
```

Fase 3 e Fase 4 podem correr em paralelo após a Fase 2.
