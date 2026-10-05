# ADR 015 — Ultima Agora é offline-first

**Status:** aceito — 2026-10-05. **Implementado.**

## Contexto

Quem investiga trabalha em lugares sem rede estável (viagem, campo, rede isolada) e precisa continuar com **tudo** que já estava aberto: o aplicativo, os workspaces, o que escreveu e o que já consultou. É o mesmo princípio do nossotreino.com.br ("funciona offline"). A primeira versão do Agora tinha o documento local-first (IndexedDB + CRDT), mas **o aplicativo e o catálogo dependiam do servidor**: recarregar sem rede falhava.

## Decisão

Offline-first em quatro camadas, todas verificadas com o servidor desligado:

1. **O aplicativo abre sem rede.** Um service worker (`/agora/sw.js`, o `sw.js` do projeto Agora servido de dentro de `/agora/` para ter escopo sobre a página) guarda o app shell: "rede primeiro, cache como reserva" (com limite de 4 s para rede lenta), pré-cache **gerado** (`scripts/gerar_precache.py`, versão = hash do conteúdo) e uma página estática `offline.html` para a navegação que falha. A página do Django **não** é guardada (ela carrega o usuário): a identidade vem da última abertura com conexão (`localStorage`), e o documento do IndexedDB.
2. **O catálogo é local.** A lista e os metadados dos workspaces (com o último papel conhecido) ficam no IndexedDB do usuário. **Criar, renomear e arquivar funcionam offline**: o `id` do workspace é gerado no cliente (o `POST /workspaces/` aceita `id`, idempotente para o mesmo dono, `409` caso contrário), então o workspace já nasce com identidade e documento, e é enviado quando a rede volta.
3. **Os documentos são locais** (já eram): editar offline e mesclar na volta (CRDT).
4. **O que o usuário já consultou fica disponível**, **dentro de uma política de classificação** (abaixo).

## A política de cache de dados: o teto é da instância, o nível é de cada pessoa

Tudo que fica no navegador acompanha o dispositivo (um notebook perdido leva consigo o que estava guardado). Duas camadas de decisão:

- **A instância define o TETO** (`AGORA_OFFLINE_CACHE_NIVEL`: `nenhum` | `publico` | `interno` | `restrito` | `confidencial`, padrão `restrito`): ninguém guarda acima dele.
- **Cada pessoa escolhe o próprio nível**, até o teto, em **Configurações → Dados neste dispositivo → Nível do cache offline**, por dispositivo (padrão: `interno`, ou o teto se for menor). A escolha vale na hora: o que está acima do novo nível é **apagado imediatamente**. Escolher `restrito` ou mais mostra o aviso de que o armazenamento local **não é cifrado**.

A aplicação é em três camadas (`static/agora-ia/offline_cache.js`):

- o que está acima do nível é **descartado antes de ser gravado**, e a interface diz quantos itens ficaram de fora ("1 item não guardado por nível de classificação");
- resposta de formato desconhecido **não** é guardada;
- baixar o nível (pela pessoa ou pelo teto da instância) **apaga** o que já estava guardado acima dele.

Respostas do servidor que **recusam** (403/404: acesso revogado) **nunca** são respondidas pela cópia. O cache é por usuário (um banco por usuário) e limitado (300 entradas, as mais antigas saem).

## Configurações (usuário e aplicativo)

O botão ⚙ do Agora abre um diálogo montado a partir do que o hospedeiro oferece (`host.settings`):

- **Conta:** nome e **Sair**. Sair encerra a sessão no servidor (`POST /sair/`), **esquece a configuração guardada** (sem ela a página offline não abre o app para a próxima pessoa que pegar o dispositivo) e leva ao login. Há a opção **"Também limpar os dados deste dispositivo ao sair"**. Offline, Sair **não finge**: avisa que a sessão continua aberta no servidor e não apaga nada.
- **Dados neste dispositivo:** o nível do cache, um resumo (workspaces, cópias de dados, pendentes de envio) e **Limpar este dispositivo**: apaga workspaces, documentos, cópias de dados e as preferências locais. É em **duas etapas**, exige marcar que entendeu, e **avisa quantos itens ainda não chegaram ao servidor** (serão perdidos). O que já foi enviado continua no servidor e volta no próximo acesso com conexão.
- **Aplicativo:** tema (automático, claro, escuro) e estado da rede.

## Consequências

- O padrão de cada pessoa (`interno`) **não** mantém offline os artefatos `restrito` (o padrão de classificação dos capturados): quem quiser tudo que vê offline escolhe `restrito` em Configurações — uma decisão consciente, com o aviso de que não é cifrado. Uma instância que não quer isso baixa o teto.
- `confidencial` só deve ser guardado em dispositivo com **cifra em repouso** (decisão D-17 do Agora, ainda em aberto): hoje o IndexedDB não é cifrado.
- O IA passa a depender de um service worker e de um segundo arquivo estático gerado (`precache.json`), que `scripts/testar_agora_ia.sh` confere que está em dia.

## Limites conhecidos

- Primeiro acesso exige conexão (para instalar o app e fazer login). Sem login recente a sessão do Django pode expirar; offline isso não importa, e online o portal pede o login de novo.
- Sair **sem** limpar mantém os dados no dispositivo para o próximo login da mesma pessoa (isolados por usuário, mas **não cifrados**): em dispositivo compartilhado, use "Também limpar os dados deste dispositivo ao sair".
- Papéis e acesso mudados enquanto offline só valem na volta: o servidor continua sendo a autoridade (`agora-sync` recusa escritas sem permissão, e o aviso "descartar alterações locais" cobre o caso).
- Busca e detalhes só funcionam offline para o que já foi consultado antes (cópia, não índice).
- Mudar o título de um workspace que outra pessoa renomeou offline: vale a última sincronização (último a chegar).
