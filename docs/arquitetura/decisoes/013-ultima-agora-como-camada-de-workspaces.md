# ADR 013 — Ultima Agora como camada de workspaces colaborativos

**Status:** aceito — 2026-10-05. **Implementado** como M4 do projeto Ultima Agora (`../ultima-agora`, especificação em `docs/especificacao/`).

## Contexto

Investigar é não linear: notícia → pessoa → empresa → contrato → evidência → nova pessoa. Telas fixas (galeria, busca, mapa) não acompanham esse caminho nem deixam duas pessoas olharem para a mesma coisa. O Ultima Agora é um ambiente em que o **usuário compõe o espaço de trabalho** com componentes (`ia-search`, `ia-entity`, tabela, grafo, notas…) ligados por portas tipadas, num documento persistente e colaborativo.

## Decisão

1. **O Agora é uma camada, o IA é uma aplicação sobre ela.** O núcleo (UltimaJS + Agora) não conhece `Entity`, `Claim` nem organização. O IA entrega um **pacote de domínio** (`static/agora-ia/pack/`: tipos, conversores e componentes `ia-*`) e um **adaptador de hospedeiro** (`static/agora-ia/django_host.js` + `apps/agora`).
2. **Cópia versionada, sem gerenciador de pacotes.** O runtime do Agora é copiado para `services/portal/static/agora/` por `scripts/sincronizar_agora.sh` (que também leva o `agora-sync` para `services/agora-sync/`), no mesmo regime em que o Agora copia o UltimaJS. **Não se edita a cópia**: muda-se o projeto de origem e sincroniza-se (`ORIGEM.txt` registra o commit).
3. **O workspace não é o `Space`.** O workspace é composição de interface com dono, papéis e classificação (`apps.agora.Workspace`); o `Space` (ADR 010) agrupa artefatos e carrega política. O workspace pode apontar para um `Space` (`space`), sem que um conceda acesso ao outro.
4. **O documento colaborativo guarda referências, não cópias, de dados do domínio.** O barramento do Agora transporta DTOs mínimos (id, rótulo, tipo, nível) e cada componente busca o detalhe em `/agora/api/v1/dominio/` **com a sessão de quem está olhando**. Ter acesso a um workspace não dá acesso aos objetos que ele referencia: um objeto de outra organização é 404, como se não existisse. Objetos mais restritos que o workspace são **referenciados e sinalizados** (`above_workspace`), nunca copiados.
5. **Papéis.** `owner`/`editor`/`participant`/`viewer` por workspace, **limitados pelo papel na organização** (`apps/agora/acesso.py`): dono/admin da organização → teto `owner`, membro → `editor`, convidado → `viewer`; sem linha explícita vale o padrão (editor, participante, leitor). Quem não é membro **vigente** da organização não tem acesso, mesmo com linha explícita. O dono do workspace é `owner` dentro do teto.
6. **Classificação do workspace** (`classification`, padrão `restrito`) é o piso do conteúdo autoral (notas, quadro). Reclassificar é operação do dono e **sempre** gera evento.
7. **Eventos operacionais** (ADR 005) com identificadores, nunca conteúdo: `agora.workspace.criado/alterado/arquivado/reclassificado/papel_alterado/papel_removido` e `agora.sync.token`.
8. **Colaboração é opcional.** Sem `AGORA_SYNC_URL` o Agora funciona, mas cada workspace fica só no navegador de quem o criou (IndexedDB, um banco por usuário).

## Consequências

- Novo app Django `apps.agora` e a rota `/agora/`; entrada "Investigação (Agora)" no menu.
- O portal passa a ter uma dependência de **runtime de front** copiada (≈ 550 KB de ES modules). Sem build.
- O que é local-first no navegador (documento do workspace) **não passa pelo `policy_engine`**: ele só contém composição e conteúdo autoral. Quando agentes entrarem (fase seguinte), o contexto que eles recebem **precisa** passar pelo `policy_engine` no servidor (R-CTX-4 do Agora) antes de qualquer chamada a LLM.
- Dados do domínio **não** são guardados no IndexedDB do navegador por este caminho (só o documento do workspace).

## Pendências conhecidas

- A limpeza do banco local no logout (R-LF-5 do Agora) ainda não existe; o isolamento é por usuário (`ia-agora-<id>`), não por sessão.
- A busca de entidades é textual simples (`icontains` sobre o conteúdo); a busca semântica (Qdrant) fica para depois.
- Sem link de convite com papel e expiração; os papéis são dados pelo dono a membros da organização.
- Sem chat, agentes nem conferência (fases seguintes do Agora).
