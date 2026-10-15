# ADR 020 — Chat com os modelos do cluster como componente `ia-llm-chat` do Ultima Agora

**Status:** aceita e implementada em 2026-10-15 (sem streaming; sem contexto de artefatos).

## Contexto

Os modelos do Ollama instalados nas máquinas do cluster (ADR 009, 012) só podiam ser exercitados por código ou por `curl` no gateway OpenAI-compatível, que é autenticado por token e isento de sessão. Faltava um jeito de **testar pelo próprio sistema** qual máquina responde, com que velocidade, e uma base para um assistente multiusuário no Ultima Agora.

## Decisão

1. **Um componente do pacote IA, `ia-llm-chat`**, em `static/agora-ia/pack/` (a parte do IA; `static/agora/` e `services/agora-sync/` são cópias do projeto `ultima-agora` e não se editam). Não se reaproveita o `ultima-chat` (ADR 016): nele o autor vem do token da pessoa e não há noção de assistente; um bot exigiria mudar o projeto de origem.
2. **Duas rotas de domínio**, sob a sessão e a organização da pessoa, como as demais do `ia-*`: `GET /agora/api/v1/dominio/llm/modelos/` e `POST .../llm/chat/`. O servidor chama `gerar_chat` em processo, escolhendo a máquina com o roteador (`escolher_execucao`) ou a que a pessoa fixou (`execucao_para`). O gateway `/v1/chat/completions` continua sendo o canal entre nós e para ferramentas externas.
3. **Só LLM do cluster**, nunca provedor externo. A decisão local × externo continua do `policy_engine`, que não é tocado.
4. **O prompt é só o que a pessoa digita.** Nenhum artefato, nota ou conteúdo do workspace é injetado. Por isso a rota não classifica nada. **Quando o chat ganhar contexto, o servidor terá de passar cada item pelo `policy_engine` antes da chamada** (já dito nas ADR 013 e 016); isso muda esta decisão.
5. **O histórico fica só no navegador** (`sessionStorage`) e viaja inteiro a cada rodada. Nada em banco nem no documento compartilhado (CRDT). O servidor registra `AuditLog` `llm.chat` (quem, modelo, máquina) **sem conteúdo**, e a telemetria `ChamadaLLM` com `Finalidade.CHAT`.
6. **Sem streaming** nesta versão (o cliente Ollama usa `stream: false` em toda parte). Limites: 50 mensagens, 8 000 caracteres por mensagem, 40 000 no total.
7. **Erro de máquina = `424`**, não 502/503/504: o `api()` do host trata esses três como "sem conexão com o servidor".

## Consequências

- Um modelo só aparece se a máquina estiver ativa, confirmada, com heartbeat recente e o modelo visto há ≤ 180 s (a mesma régua do roteador).
- Quem não é da organização que administra as máquinas não vê modelo nenhum (isolamento por organização).
- Reabrir o assunto quando houver: streaming; conversa guardada no servidor (exigirá modelo de dados, classificação, retenção e auditoria); contexto de artefatos (exige `policy_engine`); participação do assistente num chat entre pessoas (exige mudança no `ultima-agora`).
