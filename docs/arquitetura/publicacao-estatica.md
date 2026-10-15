# Publicação estática (proposta)

**Status:** proposta, não implementada. Motivo: o projeto **Programadores pela Democracia** (`../programadorespelademocracia`, site estático em Cloudflare Workers + Static Assets, eleição em 2026-10-25) vai usar o Inteligência Aberta como bancada de trabalho. Este documento planeja o que isso exige. Quando virar decisão, vira ADR 017.

## 1. O requisito novo

Até aqui toda saída do IA é para **quem tem conta na instância**. Agora há dois públicos:

| Público | Onde | O que faz |
|---|---|---|
| ~20 pessoas voluntárias | portal/Agora na instância (Antares, rede local) | capturam, conferem, escrevem, aprovam |
| Qualquer pessoa | site estático na Cloudflare | **lê** o que foi aprovado, com fontes, e é avisada quando sai coisa nova |

A instância **nunca fica exposta ao público**. O que sai dela é um **pacote estático, assinado, empurrado de dentro para fora**. Cloudflare serve; Antares só precisa de saída para a internet.

## 2. Quem faz o quê (IA × Ultima Agora × site)

| Peça | Dono | Por quê |
|---|---|---|
| Captura, extração, alegações, evidências, classificação | **IA** | é o domínio (Claim/Evidence, `policy_engine`) |
| Mesa editorial (workspace, chat, presença, papéis) | **Agora** (já existe) sobre contas do **IA** | é colaboração; hoje funciona, sem trabalho novo |
| **Decidir o que é público**, aprovar, corrigir, retirar | **IA** | exige classificação, auditoria e linhagem |
| Gerar e assinar o pacote, enviar à Cloudflare | **IA** (novo app `apps.publicacao`) | depende do domínio e da chave da instância (federação F0) |
| Leitor estático, página de novidades, aviso de "novo" | **site PPD** (JS puro, sem build) | é a vitrine; o site não afirma mais do que o código faz |
| Renderizador genérico "workspace/documento → HTML somente leitura" | **Agora** (candidato, não urgente) | não conhece domínio; serve a qualquer aplicação |
| Abstração de canais de notificação (hoje só menção no chat) | **Agora** (candidato, depois) | idem |

**O Agora não faz isso hoje.** Ele é offline-first (service worker, IndexedDB), mas o que ele guarda é o documento do workspace, para quem está logado. Não exporta, não publica, não manda push; a "notificação" dele é contador e título de aba por menção no chat. Publicar o resultado é capacidade nova.

Dentro do Agora cabem duas coisas, **ambas sem pressa** e sem bloquear o IA:
1. **Visualização estática** de um documento de workspace (somente leitura, sem servidor). Reaproveita o renderizador markdown de `components/` e o `tools/gerar_precache.py`. Só vale a pena quando houver um segundo usuário além do PPD.
2. **Canais de notificação** como contrato (in-app, Web Push, feed), em vez de cada app inventar o seu.

Recomendação: **não mexer no Agora agora.** A mesa editorial já roda nele. Abrir uma issue/spec no `ultima-agora` com os dois itens e voltar quando o PPD mostrar o que faltou.

## 3. Princípios (herdam do que já existe)

1. **Publicar é ato humano explícito e aprovado**, nunca consequência automática de um artefato existir.
2. **Só nível `público`.** Um item só entra se ele **e toda a sua linhagem** (`ArtifactLineage` ancestrais) forem `público`. Texto derivado de fonte `interno` não sai, mesmo que alguém tenha marcado o derivado como público. A checagem **chama** o `policy_engine`; não o altera.
3. **Sem edição silenciosa.** A regra do PPD é "direito de correção e de resposta". Corrigir cria **nova versão ligada à anterior**; retirar deixa uma **lápide** (id, data, motivo). O histórico é público.
4. **Proveniência na saída.** Cada item mostra *quem afirma, baseado em quê, dá para conferir*, que é exatamente a seção "De onde veio essa informação?" do site. Fonte vem de `Claim`/`Evidence`; fato, opinião e inferência de IA continuam separados.
5. **Auditável por quem lê.** O pacote é assinado com a chave Ed25519 da instância (`apps/federacao/chaves.py`, `did:key`). Qualquer pessoa confere que veio da instância e que não foi alterado.
6. **Observabilidade.** Eventos `publicacao.*` no `PipelineEvent` (identificadores, nunca conteúdo) e `AuditLog` em aprovar, publicar, corrigir e retirar.
7. **Sem rastreio.** O leitor estático não grava nada no servidor. Vale também para o aviso de novidades (seção 6).

## 4. Fluxo editorial

```
Artefato / Claim ──► [rascunho de publicação] ──► [aprovado] ──► [publicado]
                          (autor)               (outra pessoa)       │
                                                                     ├─► corrigido (nova versão)
                                                                     └─► retirado (lápide)
```

- Autor e aprovador **são pessoas diferentes** (regra dos dois olhos; configurável por instância, ligada por padrão neste projeto).
- Modelos novos em `apps.publicacao`: `ItemPublicacao` (UUID, tipo, título, corpo markdown, `substitui` → versão anterior, estado, aprovado_por, fontes → `Claim`/`Artifact`) e `Lote` (um deploy: seq, hash do manifesto, assinatura, resultado). Estados e `status` seguem a semântica já existente: `vazio` ≠ `falhou`.
- Papéis: reutilizam `exige_admin` e os papéis do Agora (editor escreve; owner/admin aprova e publica). **Sem papel novo.**
- Tela: `/publicacao/` (fila de aprovação, histórico, botão "publicar lote", "retirar").

## 5. O pacote estático

Gerado por task Celery (`gerar_lote`), determinístico, idempotente, em `public/dados/v1/` do repositório do site:

```
dados/v1/manifest.json     versão, seq do lote, gerado_em, did da instância, lista de itens+hash, assinatura
dados/v1/feed.json         JSON Feed 1.1 (leitura por qualquer cliente); campos próprios em "_ia"
dados/v1/itens/<id>.json   item completo: corpo, fontes (url, trecho, tipo), versões, lápide se retirado
atom.xml                   o mesmo feed em Atom, para leitores de RSS e automações
```

Pontos que o site já impõe:
- A CSP do PPD é `default-src 'self'`: o leitor só consegue buscar dados **do próprio domínio**, então o pacote precisa estar dentro do deploy do site (não em Antares nem em outro host).
- `_headers` hoje põe JS/CSS em `max-age=300`; `/dados/*` precisa de `max-age=0, must-revalidate` (e ETag, que a Cloudflare já faz) para a novidade aparecer na hora.
- O site é "sem build": o leitor é um módulo ES de ~100 linhas que lê `feed.json`. Sem dependência.

### Como o pacote chega à Cloudflare

| Opção | Prós | Contras |
|---|---|---|
| **A. Commit + push no repositório do site** (deploy key), Cloudflare faz o deploy | histórico público do que foi publicado (combina com o projeto); Antares não guarda token da Cloudflare; reversível por `git revert` | exige Workers Builds ligado ao repo; commit a cada lote |
| B. `wrangler deploy` direto de Antares | simples | token com poder de deploy em Antares; sem histórico |
| C. R2/KV + Worker de leitura | desacopla dados do site | mais peças; CSP precisaria de mudança |

**Recomendo A.** Em desenvolvimento, `wrangler dev` / `python -m http.server -d public` leem o mesmo pacote.

## 6. "Receber notificações" sem servidor

Site estático não envia nada; quem avisa precisa de algo ativo. Do mais barato ao mais caro, e o que cabe até 25/10:

| Fase | Mecanismo | Servidor? | Privacidade | Prazo |
|---|---|---|---|---|
| N1 | **"Novidades desde a sua última visita"**: o leitor guarda o último `seq` visto no `localStorage` e mostra contador | não | nada sai do aparelho | junto com o leitor |
| N2 | **Atom/JSON Feed** (já gerados) para leitores de RSS | não | ok | grátis |
| N3 | **Canal próprio**: Antares posta no **canal do Telegram** (bot) a cada lote; o grupo de WhatsApp recebe o link por quem coordena (a API oficial de canal é restrita) | Antares, saída | ok; é onde o PPD já quer circular | 1–2 dias |
| N4 | **Web Push** (PWA instalada): um Worker mínimo guarda as inscrições e dispara com VAPID quando o lote sai | Worker + KV | a inscrição é um identificador do aparelho: **opt-in claro**, sem nome, sem IP guardado, apagável. Choca com "sem rastreadores"? Não, se declarado e opcional, mas a página de regras precisa dizer | 3–5 dias; **só se N1–N3 não bastarem** |

N4 também esbarra em limites reais: iOS só entrega push a PWA instalada na tela inicial. Por isso N3 (onde o público já está) vem antes.

## 7. Plano em fases (hoje é 2026-10-07; eleição em 18 dias)

**Fase P0: dez dias para algo útil no ar**
1. Decidir as perguntas da seção 9 (principalmente acesso das 20 pessoas).
2. `apps.publicacao`: modelos, fila de aprovação, `exige_admin`, eventos e `AuditLog`.
3. Gate de linhagem e de nível (`público`) com testes, incluindo o caso "derivado público de fonte interna".
4. Gerador do pacote (`manifest`, `feed`, `itens`, `atom`) + assinatura com a chave da instância + verificador em ~30 linhas de JS para o site.
5. Entrega por commit/push (opção A) e primeiro lote real.
6. No site PPD: leitor de feed + N1; trocar o texto de "Vai ter uma instância no ar" só quando existir (regra do site público: não afirmar mais que o código faz).

**Fase P1: alcance** — N3 (Telegram), página "como conferir a assinatura", correção/lápide visível no site.

**Fase P2: só se pedido** — N4 (Web Push); spec dos dois itens no `ultima-agora`.

## 8. O que **não** muda

`policy_engine.py`, `AuditLog`, UUIDs, os quatro níveis, `PipelineEvent` append-only e a semântica de `status`. A publicação é **cliente** deles. O motor de replicação da federação (`regras.py`) **não** é usado: ele decide troca entre instâncias pares, e isto é saída para o público. Se um dia convier, "público" pode virar um destino especial lá, mas não agora.

Avisos de desenvolvimento: nenhuma mudança aqui invalida dados existentes (apps e tabelas novos).

## 9. Perguntas em aberto (precisam do dono)

1. **Como as 20 pessoas chegam a Antares?** Rede local e VPN só servem a quem está perto. Para gente de fora: **Cloudflare Tunnel + Access** (saída, sem abrir porta; Access limita a e-mails da lista) ou o Caddy do profile `publico`. Isso muda o `BIND_ADDR`/`TLS_MODE`.
2. **Cadastro.** `/registro/` é aberto por padrão e cada pessoa nova ganha **organização própria**; `/membros/` só adiciona gente **já cadastrada**. Para 20 pessoas na mesma organização sem deixar estranhos entrarem, falta um **convite** (link com papel e expiração, já listado como pendência do ADR 013) ou o administrador criar as contas. Qual caminho? Eu sugiro o convite, que serve também ao Agora.
3. **Dois olhos obrigatórios** para publicar, ou basta uma pessoa admin?
4. **Opção A** (git do site) está bem para você, com o histórico público dos lotes?
5. **O PPD publica só o que passa pelo IA**, ou o site também terá textos próprios (as "frentes")? Isto define se `feed.json` é o site inteiro ou só a seção de novidades. (Sugestão: só novidades.)
6. **Telegram** (N3): já existe canal/bot, ou criamos um?
7. Dentro do **WhatsApp**, a divulgação é manual; está ok?
