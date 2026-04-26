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

## Fase 1 — Dados Privados e RAG

**Objetivo:** Usuário consegue trazer seus próprios documentos e o sistema os usa na análise.

**Entregáveis:**
- [ ] Envio de documentos na interface web
- [ ] Pipeline RAG completo (indexação, fragmentação, embedding, busca semântica)
- [ ] Agente Extrator com OCR
- [ ] LLM local integrado (Ollama) — para dados restrito/confidencial
- [ ] Isolamento de índice vetorial por organização
- [ ] Controle de compartilhamento básico (criar, revogar)

**Caso de uso habilitado:** UC-02 (análise financeira do MEI), UC-03 parcial.

**Persona atendida:** P2 (Carlos).

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
