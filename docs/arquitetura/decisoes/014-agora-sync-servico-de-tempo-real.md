# ADR 014 — `agora-sync`: serviço de tempo real do Ultima Agora

**Status:** aceito — 2026-10-05. **Implementado** (`services/agora-sync`, copiado de `../ultima-agora/server/agora-sync`).

## Contexto

O documento de um workspace é um CRDT (Yjs): vários navegadores editam ao mesmo tempo, offline também, e convergem. Isso pede conexões WebSocket longas e memória por documento, com perfil de carga diferente do portal. O portal continua dono de **autenticação e autorização** (R-BE-2 do Agora).

## Decisão

1. **Serviço próprio, em contêiner** (ADR 002), em Node (a implementação de referência do Yjs), opcional (`docker compose --profile agora`). O portal Django não hospeda o WebSocket.
2. **Autenticação por token curto do portal.** `POST /agora/api/v1/workspaces/<id>/token/` devolve um JWT HS256 (5 min) com `sub`, `name`, `workspace` e `role`, assinado com `AGORA_SYNC_SECRET` (≥ 32 caracteres; em produção só é exigido se `AGORA_SYNC_URL` estiver definida). O serviço **não** conhece usuários nem organizações: aplica o papel que o token carrega.
3. **Papéis aplicados no servidor**, não só na interface: `viewer` não altera nada, `participant` só o conteúdo autoral (`content`), `owner`/`editor` tudo. Uma escrita proibida fecha a conexão (4403) e **não** entra no documento. O cliente também recusa localmente, para o erro aparecer como exceção e não como desconexão.
4. **Mudança de papel vale na hora.** Ao alterar um papel, o portal chama `POST /admin/role` do serviço (Bearer `AGORA_SYNC_ADMIN_TOKEN`, rede interna do compose); sem o token configurado o endpoint não existe. Se o serviço estiver fora, o novo papel vale no próximo token (≤ 5 min). **O Caddy bloqueia `/agora-sync/admin/*`**.
5. **Persistência:** um arquivo por workspace em `DATA_DIR`, gravação atômica; uma falha de disco é registrada e não derruba a sessão. PostgreSQL (D-29 do Agora) fica para depois.
6. **Limites:** mensagem acima de 2 MB é recusada; id de workspace restrito a `[A-Za-z0-9_-]{1,80}`.

## Consequências

- Novo serviço `agora-sync` (porta 8787): em desenvolvimento publicada; em produção atrás do Caddy em `/agora-sync/` (wss) ou presa a `BIND_ADDR`.
- O segredo `AGORA_SYNC_SECRET` precisa ser igual no portal e no serviço; é gerado por instância como os demais (`.env.example`).
- Os **endpoints de desenvolvimento** (`/dev/token`, `/dev/role`, `AGORA_DEV_AUTH=1`) **não** são usados pelo IA e não existem sem aquela flag.

## Limites conhecidos

- Cada escrita de `participant` copia o documento no servidor para conferir o que ela toca: aceitável agora, caro em documentos grandes.
- Sem limite de taxa por conexão; autoria por conexão (não por operação).
- Escalar horizontalmente exigiria roteamento por workspace (um documento vive num processo).
