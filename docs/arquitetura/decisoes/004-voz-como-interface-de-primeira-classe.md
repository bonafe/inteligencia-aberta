# Decisão-004: Interface de voz como cidadã de primeira classe

**Status:** Aceito  
**Data:** 2026-04-25

## Contexto

A visão do projeto é que qualquer cidadão consiga usar o sistema, independente de letramento digital. A interface web e o chat de texto criam barreiras para pessoas idosas, com baixo letramento ou com deficiência visual/motora.

Voz poderia ser tratada como um recurso adicional, implementado depois, ou como uma interface de segunda categoria. Isso seria coerente tecnicamente mas incompatível com o manifesto do projeto.

## Decisão

A interface de voz é implementada como contêiner de primeira classe, com o mesmo nível de prioridade arquitetural que a interface web. Ela converge para o mesmo API Gateway que os outros canais — não é um complemento nem um invólucro da interface web.

O contêiner de voz é responsável por: receber áudio, transcrever para texto (STT), enviar para o Gateway, receber resposta textual, sintetizar em áudio (TTS), e devolver ao usuário.

Internamente, o sistema opera em texto — a voz é apenas o canal de entrada e saída. Isso significa que toda funcionalidade disponível em chat está disponível por voz.

## Consequências

**Positivas:**
- Qualquer funcionalidade nova está automaticamente disponível por voz.
- Pessoas sem letramento digital têm acesso ao sistema completo.
- Alinha com legislação de acessibilidade digital.

**Negativas:**
- Processamento de STT/TTS adiciona latência — respostas demoram mais por voz que por texto.
- Modelos de STT/TTS precisam suportar português brasileiro com sotaques regionais.
- Algumas respostas do sistema (tabelas, gráficos, relatórios longos) não se traduzem bem para áudio — precisam de adaptação.

**Restrições geradas:**
- O agente redator deve ser capaz de gerar dois formatos de resposta: completo (para tela) e resumido/conversacional (para voz).
- Respostas por voz têm limite de duração — respostas longas precisam ser interativas ("quer ouvir mais detalhes?").
- O contêiner de voz deve suportar modelos STT/TTS locais para não depender de APIs externas para dados sensíveis.
- Latência máxima aceitável para resposta por voz: 15 segundos de ponta a ponta.
