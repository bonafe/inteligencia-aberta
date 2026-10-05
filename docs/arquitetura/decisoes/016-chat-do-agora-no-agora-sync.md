# ADR 016 — Chat do Ultima Agora vive no `agora-sync`

**Status:** aceito — 2026-10-05. **Implementado** (M5 do projeto Ultima Agora).

## Contexto

Os workspaces têm um chat em tempo real (R-CHAT do Agora). A especificação previa a tabela `ChatMessage` no portal Django (PostgreSQL). Mas o Agora também roda **sem backend** (hospedeiro de desenvolvimento) e o chat precisa funcionar nele; e o `agora-sync` (ADR 014) já autentica por token, conhece o papel de cada conexão e entrega eventos em tempo real.

## Decisão

1. **O chat é do `agora-sync`**, não do Django. Um log por workspace (`<workspace>.chat.jsonl`, no mesmo `DATA_DIR` dos documentos), **fora do documento colaborativo** (cresce sem limite, tem retenção própria e permissões diferentes).
2. **O autor vem sempre do token**, nunca do cliente. O envio é **idempotente** por `clientId` (uma retentativa após queda de rede nunca repete a mensagem).
3. **Papéis:** `viewer` lê o chat e não escreve; `participant`/`editor`/`owner` escrevem; **só o autor edita**; **o autor ou o dono do workspace removem** (moderação). Mudar o papel vale na próxima mensagem.
4. **Remover apaga o texto do disco** (o log é reescrito; fica só a lápide "mensagem removida"). Um log só de acréscimo deixaria o texto original gravado.
5. **Limites:** 4000 caracteres por mensagem, 20 envios a cada 10 s por conexão, 100 mensagens de histórico ao entrar (paginável). **Retenção** opcional por instância: `CHAT_RETENTION_DAYS` (0 = para sempre; o log é compactado ao abrir).
6. **Uma falha de disco não derruba a sessão:** o remetente recebe um erro claro (`storage`), a conexão continua e a mensagem não existe pela metade (grava antes de aceitar).
7. **No cliente o chat é local-first:** o histórico e as mensagens ainda não enviadas ficam **no dispositivo** (IndexedDB, banco por usuário), então o chat **lê e escreve offline**; as pendentes saem, uma vez e em ordem, quando a conexão volta. Como é conteúdo autoral do workspace (como as notas), **não** está sujeito à política de cache de dados de domínio do ADR 015 (mas é apagado por "Limpar este dispositivo").
8. **Render seguro:** texto simples com marcação mínima, tudo escapado antes; só links `http(s)`.

## Consequências

- Nenhuma migração nem tabela nova no portal. O chat aparece no portal automaticamente (botão 💬) para quem tem um workspace com `AGORA_SYNC_URL`.
- O conteúdo do chat **não** passa pelo `policy_engine`: é conteúdo autoral e herda o piso de classificação do workspace. Antes de agentes lerem o chat, o contexto precisa passar pelo `policy_engine` no servidor (como já dito no ADR 013).
- O conteúdo do chat **não** vai para o painel de eventos (ADR 005): só identificadores e tamanhos poderiam ir.
- **Contêiner:** o serviço inicia como root só para ajustar a permissão de `DATA_DIR` (um bind mount criado pelo Docker é `root:root`) e **baixa para o usuário `node`**. Sem isso, nada era gravado (documentos incluídos), o que só apareceu ao rodar o serviço no contêiner real.

## Limites conhecidos

- Armazenamento em arquivo, não PostgreSQL: backup é o do `DATA_DIR`; migrar para o banco do portal é uma evolução que não muda o protocolo.
- Referências a instâncias e objetos dentro do chat, chat de componente e mensagens privadas não existem ainda (fase 2 do Agora).
- Menção é por nome (`@Maria` casa o primeiro nome ou o nome completo sem espaços); sem diretório de usuários, dois "Maria" recebem a mesma menção.
- Sem notificação do sistema operacional: o aviso é o contador, a cor, o título da aba e o rótulo acessível.
