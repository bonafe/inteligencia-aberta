# Componente: Interface de Chat (Linguagem Natural)

## Responsabilidade

Principal interface interativa do sistema para usuários com acesso a teclado ou dispositivo móvel. Permite conversa em linguagem completamente natural — sem formulários, sem campos obrigatórios, sem comandos especiais.

## Princípio de Design

O usuário nunca deve precisar aprender como falar com o sistema. Perguntas em linguagem coloquial, com erros de gramática, incompletas ou ambíguas são aceitas. O sistema pergunta quando precisar de mais informação, em vez de retornar erro.

**Aceito:**
- *"oi, quero saber sobre aquela empresa"*
- *"pode verificar o cnpj 00.000.000/0001-00"*
- *"e o sócio dela?"* (referência ao contexto anterior)

**Nunca retornar:**
- Mensagem de erro com código.
- Solicitação de formato específico sem explicar por quê.
- Resposta genérica que não avança a conversa.

## Arquitetura

O contêiner de chat expõe uma interface web de mensagens e uma API WebSocket para comunicação em tempo real.

```
Usuário digita/envia mensagem
    ↓
[Interpretador de intenção] LLM interpreta intenção e extrai entidades
    ↓
[Gerenciador de contexto] Mantém histórico da conversa
    ↓
[API Gateway] Encaminha para orquestrador com contexto
    ↓
[Transmissão da resposta] Resultado enviado em tempo real à interface
```

## Gerenciamento de Contexto

Cada sessão de chat mantém:
- Histórico de mensagens (limitado por tokens configurável, padrão: últimas 20 trocas).
- Entidades mencionadas na conversa (empresas, pessoas, processos).
- Investigações em andamento vinculadas à sessão.
- Classificação da sessão (determinada pelo dado mais sensível da conversa).

## Tipos de Resposta

O chat suporta respostas ricas:

| Tipo | Quando usar |
|---|---|
| Texto simples | Respostas curtas, confirmações |
| Markdown estruturado | Relatórios, listas, comparações |
| Tabela interativa | Dados tabulares com muitas linhas |
| Grafo de vínculos | Resultados de correlação |
| Cartão de entidade | Resumo de empresa/pessoa |
| Progresso | Investigações longas — atualização em tempo real |

## Envio de Documentos

Usuário pode enviar documentos diretamente no chat (arrastar ou botão). Formatos aceitos: PDF, imagens (JPG, PNG), texto (TXT, CSV).

Ao receber um documento, o sistema:
1. Pergunta a classificação desejada se não for óbvia.
2. Processa via pipeline RAG.
3. Confirma que o documento foi indexado.
4. Permite perguntas sobre o conteúdo imediatamente.

## Critérios de Aceitação

- [ ] Primeira resposta a qualquer mensagem em < 3 segundos (mesmo que seja "Estou buscando...").
- [ ] Referências ao contexto anterior da conversa funcionam corretamente ("e o sócio dela?").
- [ ] Mensagens ambíguas geram pergunta de esclarecimento, não erro.
- [ ] Envio de documento < 20MB processado em < 60 segundos.
- [ ] Resposta transmitida em tempo real (não espera a resposta completa para exibir).
- [ ] Histórico de conversa persiste entre sessões (usuário pode retomar no dia seguinte).
