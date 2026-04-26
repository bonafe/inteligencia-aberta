# Especificação — Inteligência Aberta

> Este arquivo é o índice da especificação. Cada seção vive em seu próprio arquivo — leia o que for relevante para o que está implementando.

---

## Visão e Contexto

O manifesto e a visão do projeto estão no [`README.md`](../README.md) na raiz do repositório. Leia antes de qualquer coisa.

| Documento | O que contém |
|---|---|
| [`visao/personas.md`](visao/personas.md) | Quem são os usuários, o que precisam, como interagem |
| [`visao/casos-de-uso.md`](visao/casos-de-uso.md) | Jornadas completas por persona com critérios de aceitação |

---

## Arquitetura

| Documento | O que contém |
|---|---|
| [`arquitetura/visao-geral.md`](arquitetura/visao-geral.md) | Diagrama macro, camadas, fluxo de dados, princípios |
| [`arquitetura/modelo-de-dados.md`](arquitetura/modelo-de-dados.md) | Entidades, grafo de conhecimento, modelo de classificação |
| [`arquitetura/decisoes/001-mcp-como-camada-de-integracao.md`](arquitetura/decisoes/001-mcp-como-camada-de-integracao.md) | Decisão: por que MCP é a camada de integração |
| [`arquitetura/decisoes/002-conteineres-como-unidade-fundamental.md`](arquitetura/decisoes/002-conteineres-como-unidade-fundamental.md) | Decisão: por que contêineres são a unidade fundamental |
| [`arquitetura/decisoes/003-llm-local-para-dados-sensiveis.md`](arquitetura/decisoes/003-llm-local-para-dados-sensiveis.md) | Decisão: por que dados sensíveis só usam LLM local |
| [`arquitetura/decisoes/004-voz-como-interface-de-primeira-classe.md`](arquitetura/decisoes/004-voz-como-interface-de-primeira-classe.md) | Decisão: por que voz é interface de primeira classe |

---

## Componentes

### Orquestração

| Documento | O que contém |
|---|---|
| [`componentes/orquestrador.md`](componentes/orquestrador.md) | LangGraph: estados, transições, persistência, contrato |
| [`componentes/mcp.md`](componentes/mcp.md) | MCP: registro de ferramentas, motor de políticas, executor |
| [`componentes/pipeline-rag.md`](componentes/pipeline-rag.md) | Pipeline RAG: indexação, recuperação, isolamento por organização |

### Agentes

| Documento | Agente |
|---|---|
| [`componentes/agentes/planejador.md`](componentes/agentes/planejador.md) | Planejador — define estratégia de investigação |
| [`componentes/agentes/coletor.md`](componentes/agentes/coletor.md) | Coletor — obtém dados de fontes externas e internas |
| [`componentes/agentes/extrator.md`](componentes/agentes/extrator.md) | Extrator — estrutura dados brutos (OCR, parsing) |
| [`componentes/agentes/correlacionador.md`](componentes/agentes/correlacionador.md) | Correlacionador — produz grafo de vínculos |
| [`componentes/agentes/validador.md`](componentes/agentes/validador.md) | Validador — audita qualidade e veracidade |
| [`componentes/agentes/analista.md`](componentes/agentes/analista.md) | Analista — gera insights |
| [`componentes/agentes/redator.md`](componentes/agentes/redator.md) | Redator — sintetiza relatório final |

### Interfaces

| Documento | Interface |
|---|---|
| [`componentes/interfaces/voz.md`](componentes/interfaces/voz.md) | Voz — STT/TTS, acessibilidade, português brasileiro |
| [`componentes/interfaces/chat.md`](componentes/interfaces/chat.md) | Chat — linguagem natural, múltiplos turnos |
| [`componentes/interfaces/web.md`](componentes/interfaces/web.md) | Web — Django, operacional, relatórios |

---

## Segurança

| Documento | O que contém |
|---|---|
| [`seguranca/classificacao.md`](seguranca/classificacao.md) | Níveis de classificação, enforcement, motor de políticas, auditoria |
| [`seguranca/compartilhamento.md`](seguranca/compartilhamento.md) | Multi-organização, compartilhamento, revogação, casos de uso |

---

## Roteiro

| Documento | O que contém |
|---|---|
| [`roadmap.md`](roadmap.md) | 6 fases, entregáveis por fase, persona atendida, dependências |

---

## Como usar esta especificação no desenvolvimento

1. **Antes de implementar qualquer componente:** leia o arquivo correspondente em `componentes/`.
2. **Antes de tomar uma decisão arquitetural importante:** verifique se já existe um registro em `arquitetura/decisoes/`. Se não existe, crie um antes de implementar.
3. **Ao definir "pronto":** os critérios de aceitação em cada spec são o Definition of Done do componente.
4. **Ao adicionar novo componente:** crie o arquivo de spec em `componentes/` antes de escrever código.
