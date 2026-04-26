# Componente: Interface de Voz

## Responsabilidade

Permitir que qualquer usuário interaja com o sistema usando apenas a voz — sem necessidade de digitar, navegar em menus ou ter letramento digital. É o canal prioritário para inclusão digital.

## Arquitetura do Contêiner

O contêiner de voz é autônomo: recebe áudio, entrega áudio. Internamente coordena STT, o ciclo de conversa via Gateway, e TTS.

```
Usuário fala
    ↓
[STT] Transcrição de áudio para texto (PT-BR)
    ↓
[Normalização] Limpeza de ruído, pontuação, formatação
    ↓
[API Gateway] Mesma entrada que o canal de chat
    ↓
[Processamento pelos agentes] (idêntico a qualquer canal)
    ↓
[Adaptador de resposta] Converte resposta completa para formato conversacional
    ↓
[TTS] Síntese de texto para áudio (PT-BR)
    ↓
Usuário ouve
```

## Tecnologias Candidatas

### STT (Voz para Texto)
- **Whisper (OpenAI, local):** Melhor qualidade, roda localmente, suporte robusto a PT-BR com sotaques regionais. Opção padrão.
- **Faster-Whisper:** Versão otimizada do Whisper com menor latência. Preferida quando hardware for limitado.
- **Google Speech-to-Text:** Opção externa se latência for crítica e dados não forem sensíveis.

### TTS (Texto para Voz)
- **Coqui TTS (local):** Código aberto, qualidade aceitável em PT-BR, roda localmente.
- **Piper TTS (local):** Mais leve, menor latência, qualidade boa para PT-BR.
- **Google TTS / ElevenLabs:** Opção externa para melhor qualidade — apenas para dados públicos.

**Regra:** Para sessões com dados `restrito` ou `confidencial`, STT e TTS devem rodar localmente. APIs externas de voz só são usadas quando a classificação da sessão for `público`.

## Adaptador de Resposta

O sistema gera respostas em formato completo (para tela). O adaptador de voz transforma esse resultado em resposta conversacional.

**Regras de adaptação:**

| Tipo de resposta | Comportamento |
|---|---|
| Texto < 100 palavras | Lê completo |
| Texto > 100 palavras | Lê resumo + pergunta "quer mais detalhes?" |
| Tabela ou gráfico | Descreve em prosa: "encontrei 3 empresas: X, Y e Z. Quer que eu fale sobre cada uma?" |
| Relatório longo | Resume principais achados, oferece envio por texto/e-mail |
| Erro | Explica o problema em linguagem simples, sugere o que fazer |

## Fluxo de Conversa com Múltiplos Turnos

O contêiner mantém contexto de conversa por sessão:

```
[Usuário]: "Tenho direito a algum benefício?"
[Sistema]: "Preciso do seu CPF para consultar. Pode me falar?"
[Usuário]: "É 123 ponto 456 ponto 789 traço 09"
[Sistema]: [normaliza CPF] → consulta → responde
[Usuário]: "E o meu marido também tem?"
[Sistema]: [mantém contexto] → consulta com mesmo escopo
```

Sessão de voz expira após 10 minutos de inatividade.

## Requisitos de Acessibilidade

- Velocidade de fala configurável (0.75x a 1.5x).
- Suporte a sotaques regionais brasileiros no STT.
- Tolerância a ruído ambiente moderado.
- Usuário pode interromper a resposta a qualquer momento.
- Confirmação audível antes de operações sensíveis: *"Vou consultar seu CPF. Pode confirmar os últimos 4 dígitos?"*

## Critérios de Aceitação

- [ ] Latência de ponta a ponta (fala até resposta em áudio) < 15 segundos para consultas simples.
- [ ] Taxa de acerto de transcrição (WER) < 15% para PT-BR padrão e < 25% para sotaques regionais.
- [ ] STT e TTS rodando localmente quando classificação da sessão for `restrito` ou `confidencial`.
- [ ] Sessão mantém contexto de conversa por ao menos 10 turnos.
- [ ] Erros são comunicados em linguagem coloquial, sem rastreamentos técnicos ou códigos de erro.
- [ ] Contêiner sobe e fica pronto para receber áudio em < 30 segundos.
- [ ] Funciona com conexão de internet de baixa qualidade (buffer de áudio, nova tentativa automática).
