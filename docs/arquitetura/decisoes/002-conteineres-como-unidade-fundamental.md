# Decisão-002: Contêineres como unidade fundamental de execução

**Status:** Aceito  
**Data:** 2026-04-25

## Contexto

O sistema precisa ser modular o suficiente para que novos tipos de interface (voz, mobile), novos serviços de coleta, novos bancos de dados e novos agentes possam ser adicionados sem modificar o núcleo da plataforma.

Duas abordagens foram consideradas:
- **Monolito com plugins:** Um processo central com pontos de extensão internos.
- **Contêiner por funcionalidade:** Cada componente relevante roda em contêiner independente, comunicando-se via rede.

## Decisão

Toda funcionalidade relevante do sistema é entregue como contêiner Docker independente. Isso inclui interfaces de usuário (web, chat, voz), bancos de dados, agentes de processamento, workers assíncronos e serviços especializados.

O `docker-compose` (ou orquestrador equivalente) é a fonte da verdade sobre o que está rodando. Não existe código de negócio embutido no núcleo que substitua um contêiner.

## Consequências

**Positivas:**
- Adicionar interface de voz = criar um contêiner novo. Zero alteração no código existente.
- Falha de um contêiner não derruba os outros.
- Escala seletiva: só sobe mais instâncias dos contêineres que estão sendo gargalo.
- Equipes diferentes podem trabalhar em contêineres diferentes sem conflito.

**Negativas:**
- Custo operacional maior que um monolito — mais contêineres para monitorar.
- Latência de rede entre contêineres vs. chamada de função local.
- Complexidade de configuração de rede Docker para isolamento entre organizações.

**Restrições geradas:**
- Contêineres de organizações diferentes não compartilham rede interna.
- Todo serviço novo precisa ter seu `Dockerfile` e especificação de contrato de API antes de ser integrado.
- O MCP é o único serviço autorizado a instanciar contêineres dinamicamente em tempo de execução.
