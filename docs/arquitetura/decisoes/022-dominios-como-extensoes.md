# ADR 022 — Domínios como extensões do núcleo

**Status:** aceita — 2026-10-31. **Etapas 1 a 5 implementadas em 2026-10-31.**

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
   como projeção delas. O exportar/importar fica como conveniência. *(feito: ver abaixo)*
4. **Piso de classificação por tipo** aplicado na criação da alegação, e `tipo_objeto` das regras de
   replicação validado contra os tipos do registro. *(feito: ver abaixo)*
5. **Uma segunda extensão pequena** (saúde: exames de laboratório, piso `confidencial`) para provar o
   contrato antes de fixá-lo. *(feito: ver abaixo)*

## Etapa 3: como ficou

- **No núcleo**, `Claim.artefato` passou a aceitar nulo: a alegação sem artefato é a decisão de uma
  pessoa, registrada pelo mesmo `registrar_alegacao` com `tenant` e `classificacao` no lugar do
  artefato e só com evidência do tipo `decisao` (sem blob; o localizador diz a tela e a ação).
  A chave de idempotência inclui a alegação revisada, para que decidir A, B e de novo A sejam três
  alegações encadeadas. `normalizar_referencia` aceita `<prefixo>:<valor>` (sem espaço,
  percent-encoded) para o vocabulário de uma extensão instalada: os indivíduos do domínio
  (`fin:descritor/IFD%20MERCADO`) são referências como `cnpj:` e `mencao:`.
- **Nos Gastos** (`apps/financeiro/decisoes.py`): categoria, nome, empresa e fusão de
  estabelecimentos têm como sujeito o descritor; apelido, "própria" e junção de contas, a conta por
  tipo, instituição e identificador; criar categoria, a própria categoria. Vale, por sujeito e
  predicado, a decisão ativa que nenhuma outra ativa revisa (empate: a mais recente). As telas, a
  aplicação das sugestões e o importar registram decisões; `projetar` refaz o estado no fim de cada
  uma e da reconstrução.
- **Limites**: a projeção junta, nunca separa (não há decisão de separar); o pacote da federação
  ainda só leva alegações de artefatos, então as decisões sem artefato não viajam até a F1 definir
  como (provavelmente um evento próprio por decisão, no espaço da pessoa). A escolha da leitura de
  cada documento (`LeituraLancamentos`) segue como estado local.

## Etapa 4: como ficou

- Cada `TipoDado` declara os seus **predicados**; o registro confere que tipos e predicados estão
  no vocabulário da extensão e que nenhum predicado está em dois tipos.
- `registrar_alegacao`: predicado de extensão que nenhum tipo declara é recusado (o vocabulário é
  fechado, o que pega erro de digitação antes de virar dado federado); o de um tipo nasce no
  `mais_alto` entre o nível pedido (o do artefato, ou o da decisão) e o piso do tipo. Os
  predicados do núcleo (`schema:`, `ia:`, `prov:`) não têm piso.
- A projeção da extensão aplica o mesmo piso (`Movimento` nunca abaixo de `restrito`).
- Replicação: `RegraReplicacao.tipo_objeto` só aceita um tipo de artefato do núcleo ou um tipo de
  dado de extensão instalada (`politica.tipos_de_objeto`; a simulação em `/cluster/regras/` lista os
  dois); e `politica.decidir` sobe o nível do contexto ao piso do tipo antes do motor, que segue
  puro. O `policy_engine` não muda.
- Ainda não: o leitor tolerante da federação (guardar alegação de vocabulário que a instância não
  tem instalado) — hoje o pacote recusa essa alegação, com erro no resumo.

## Etapa 5: o que a segunda extensão ensinou

`apps/saude` (`docs/componentes/saude.md`) entrou sem tocar uma linha do núcleo **por causa dela**,
fora o que o contrato ganhou. O teste de arquitetura roda para as duas extensões. O que mudou no
contrato:

- **`Consumidor.ao_validar`.** Cada extensão escrevia o seu `post_save` de `ExtracaoPagina` e a sua
  conferência da chave no schema. Agora a extensão declara a função e o núcleo entrega a extração
  válida a cada consumidor ligado (`apps/extensoes/signals.py`), isolando falhas (uma não impede a
  outra nem a captura; evento `extensao.consumidor`). O `financeiro/signals.py` saiu.
- **`TipoDado.eleva_fonte`.** O piso na alegação não basta para dado sensível: o laudo `interno`
  continuaria indo a LLM externo com o exame dentro. O tipo pode declarar que o documento de onde a
  alegação vem sobe ao piso também; o núcleo faz isso num serviço único, só para cima e auditado
  (`artifacts/elevacao.py`), depois de validar a alegação. Os Gastos não usam (as faturas já nascem
  `restrito` pela regra de domínio).

O que se repetiu e **fica fora do contrato por ora** (duas ocorrências ainda não justificam
abstração): a leitura escolhida por documento (`LeituraLancamentos`, `LeituraExames`), o registro
"leitura → alegações com evidência" e as views de registrar que devolvem `mensagem`/`link`; o
`_org` das telas. Se uma terceira extensão repetir, vira serviço do núcleo.

## Consequências

- Domínio novo entra como app com manifesto, sem tocar o núcleo; o núcleo fica menor e testável sem
  nenhum domínio instalado.
- A troca de `ia:` por `fin:` invalidou as alegações e os movimentos anteriores dos Gastos: em
  2026-10-31 as leituras foram registradas de novo (as alegações `ia:` antigas continuam no banco,
  ativas, de um produtor de versão anterior, e não alimentam nada).
- **Fora de escopo:** extensão instalável sem rebuild (pacote pip, entry points, isolamento de
  processo). Por ora a extensão é uma app no repositório; separá-la depois é mecânico se a regra 1
  for cumprida. A publicação dos `@context` no w3id.org fica para a F1 da federação.
