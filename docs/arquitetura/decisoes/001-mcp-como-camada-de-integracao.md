# Decisão-001: MCP como camada de integração entre IA e infraestrutura

**Status:** Aceito  
**Data:** 2026-04-25

## Contexto

Os agentes de IA precisam acionar ferramentas externas: consultar APIs, instanciar contêineres, executar consultas em bancos de dados. Sem uma camada de integração padronizada, cada agente precisaria conhecer os detalhes de cada serviço — criando acoplamento forte e tornando a adição de novas ferramentas um trabalho repetitivo de código.

Duas abordagens foram consideradas:
- **Chamada direta de ferramentas:** Cada agente define suas próprias ferramentas como funções Python.
- **MCP (Model Context Protocol):** Camada dedicada que expõe ferramentas de forma padronizada e descobrível dinamicamente.

## Decisão

Adotar MCP como única camada de integração entre agentes e infraestrutura.

Os agentes não chamam serviços diretamente — sempre passam pelo MCP. O MCP é responsável por: descobrir ferramentas disponíveis, validar permissões de confidencialidade antes de cada chamada, e instanciar contêineres sob demanda quando necessário.

## Consequências

**Positivas:**
- Adicionar uma nova ferramenta é registrá-la no MCP — os agentes a descobrem automaticamente.
- Aplicação centralizada de políticas: um único ponto onde a regra "dado confidencial não vai para LLM externo" é aplicada.
- Substituição de implementação de ferramenta sem alterar nenhum agente.
- Rastreabilidade: toda chamada de ferramenta passa por um único ponto de registro.

**Negativas:**
- MCP vira ponto crítico do sistema — precisa de alta disponibilidade.
- Latência adicional em toda chamada de ferramenta.
- Curva de aprendizado para quem for implementar novas ferramentas.

**Restrições geradas:**
- Nenhum agente pode acessar serviço externo sem passar pelo MCP.
- O MCP deve implementar um Motor de Políticas determinístico — sem LLM tomando decisões de política.
