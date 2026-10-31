# ADR 022 — Domínios como extensões do núcleo

**Status:** aceita — 2026-10-31. **Etapas 1 e 2 implementadas em 2026-10-31**; as demais são o plano.

## Contexto

Os Gastos (`apps/financeiro`, ADR 021) nasceram dentro do IA, e o núcleo passou a conhecer o
domínio: o `SchemaPagina` tinha um campo `alimenta_gastos`, com rota, view e caixa na tela de
revisão; o comando `medir_parser_pagina` importava o financeiro; o menu e as rotas citavam os
Gastos; os predicados `ia:data`, `ia:valor` e `ia:descricao` ficavam no vocabulário do núcleo; e o
que a pessoa decide (categoria, fusão de estabelecimentos, apelido de conta) mora em tabelas
mutáveis, sem autor, que a federação não teria como replicar — por isso foi preciso um
exportar/importar à parte (`apps/financeiro/estado.py`).

Depois dos Gastos virão outros domínios: dados de saúde, de construção. Cada um que entrar do mesmo
jeito espalha mais nomes de domínio pelo núcleo, e a federação (ADR 010/011) teria de aprender
cada um.

## Decisão

**Núcleo** é o que vale para qualquer domínio: captura e blobs (`ni:`), artefatos e linhagem,
`Claim`/`Evidence`, classificação e `policy_engine`, `AuditLog`, eventos, espaços, identidade e
chaves, parser e schemas de página, federação. **Extensão** é uma app Django que cumpre um contrato
(`apps/extensoes/contrato.py`) e só fala com o núcleo pela sua API pública.

1. **O núcleo nunca importa uma extensão.** A extensão aponta para o seu manifesto (`Extensao`) na
   `AppConfig`; o núcleo descobre pelo registro (`apps/extensoes/registro.py`) as rotas, o menu, os
   tipos de dado e os consumidores. Tirar a extensão de `INSTALLED_APPS` a desliga por inteiro, e o
   núcleo segue capturando e extraindo. Um teste de arquitetura falha se o núcleo citar uma extensão
   (`tests/test_arquitetura_extensoes.py`).
2. **O que os documentos dizem é alegação** (`registrar_alegacao`, como já era), mas no
   **vocabulário da extensão**: `fin:` = `https://w3id.org/inteligencia-aberta/fin/v1#`, depois
   `saude:`, `obra:`. O vocabulário é versionado e imutável; mudança incompatível cria `v2`.
3. **O que a pessoa decide também é alegação**, com a pessoa como autora: "este estabelecimento é
   Mercado", "estes dois descritores são o mesmo lugar" (`sameAs`, `federacao.md` seção 12), "esta
   conta é minha". Uma decisão nova revisa a anterior, nada se edita. As tabelas de estado passam a
   ser projeção; a federação replica as decisões sem nada especial, e zerar e reprocessar não perde
   nada. O sujeito é o que sobrevive a zerar e o que outra instância entende (o descritor, o número
   da conta), nunca o UUID local.
4. **Projeções são locais e nunca viajam.** `Movimento`, `CompraParcelada` e qualquer tabela de
   extensão se reconstroem das alegações por um projetor determinístico e versionado. Uma instância
   que recebe alegações de um domínio que não tem instalado as guarda (leitor tolerante,
   `federacao.md` seção 10) e projeta quando a extensão chegar.
5. **Classificação só sobe.** Cada `TipoDado` declara um **piso** (saúde nasce `confidencial`):
   dado daquele tipo nunca fica abaixo dele, mesmo vindo de documento menos restrito. A extensão não
   toca o `policy_engine`. As regras de replicação (`RegraReplicacao.tipo_objeto`) passam a
   reconhecer os tipos declarados, com a negação por padrão de sempre.
6. **Integração por dado genérico do núcleo, não por campo de domínio.** O schema de página aprovado
   guarda uma lista de **consumidores** (`SchemaPagina.consumidores = ["fin.movimentos"]`) que as
   extensões oferecem; a tela de revisão lista os consumidores do registro, e a extensão escuta a
   extração válida (`post_save` de `ExtracaoPagina`) e confere se é um deles.

## Etapas

1. **Contrato e registro**, sem mudar comportamento: `apps/extensoes/`, manifesto do financeiro
   (`apps/financeiro/extensao.py`), rotas e menu lidos do registro. *(feito)*
2. **Os Gastos fora do núcleo**: `alimenta_gastos` → `consumidores`, view genérica
   `schemas/<id>/consumidores/`, `medir_parser_pagina` movido para o financeiro, predicados
   `ia:` → `fin:` (produtor na versão 2), teste de arquitetura. *(feito)*
3. **Decisões da pessoa como alegações** (produtor `pessoa`), e as tabelas de estado do financeiro
   como projeção delas. O exportar/importar fica como conveniência.
4. **Piso de classificação por tipo** aplicado na criação da alegação, e `tipo_objeto` das regras de
   replicação validado contra os tipos do registro.
5. **Uma segunda extensão pequena** (saúde: exames de laboratório, piso `confidencial`) para provar o
   contrato antes de fixá-lo.

## Consequências

- Domínio novo entra como app com manifesto, sem tocar o núcleo; o núcleo fica menor e testável sem
  nenhum domínio instalado.
- A troca de `ia:` por `fin:` invalidou as alegações e os movimentos anteriores dos Gastos: em
  2026-10-31 as leituras foram registradas de novo (as alegações `ia:` antigas continuam no banco,
  ativas, de um produtor de versão anterior, e não alimentam nada).
- **Fora de escopo:** extensão instalável sem rebuild (pacote pip, entry points, isolamento de
  processo). Por ora a extensão é uma app no repositório; separá-la depois é mecânico se a regra 1
  for cumprida. A publicação dos `@context` no w3id.org fica para a F1 da federação.
