# ADR 021 — Movimentos de contas: cartões, contas correntes e outros bancos

**Status:** aceita — 2026-10-31 (o dono concordou com as propostas das questões em aberto).
**Fases 1 e 2 implementadas em 2026-10-31**; as demais são o plano.

## Contexto

Os Gastos (`apps/financeiro`, `docs/componentes/gastos.md`) nasceram para **uma fatura de
cartão**: `Lancamento` é a linha de uma fatura, positivo é gasto, e a pessoa escolhe a leitura
de cada documento. Com o parser por tipo de página (`SchemaPagina`) funcionando, as 23 faturas do
BB (dez/2024–out/2026) entram sem LLM, todas conferidas contra o total do banco. Os próximos
documentos não cabem nesse modelo:

1. **Não se sabe de que conta veio um lançamento.** Com dois cartões, os gastos se misturam, e
   sem saber "quais contas são minhas" não há como reconhecer transferência entre elas.
2. **O sinal é adivinhado.** `financeiro.lancamentos` acha as colunas pelo nome (`data*`,
   `valor*`) e assume "positivo é gasto". No extrato do BB o valor vem com sufixo — `400,00 D`
   (saída), `300,00 C` (entrada) — e outros bancos usam sinal negativo ou duas colunas.
3. **Contagem dupla.** O extrato de outubro tem `Pagto cartão crédito ELO NANQUIM 13.405,63 D`
   em 28/10: é a fatura de outubro, cujas compras já estão nos Gastos. Somar os dois conta tudo
   em dobro. O mesmo vale para `BB Rende Fácil` (aplicação/resgate automáticos) e para TED/Pix
   entre contas da própria pessoa.
4. **Extratos se sobrepõem.** O de 30 dias capturado toda semana repete as mesmas linhas; a
   fatura capturada duas vezes vira dois documentos. A deduplicação por documento não basta.
5. **Linhas zebradas.** No extrato do BB (`table#tabelaExtrato`) as linhas alternam
   `tr.iTrs`/`tr.pTrs`; o `dom2parser` as vê como **duas** estruturas, e um parser ligado a uma
   leria metade dos movimentos.
6. **Outros bancos** oferecem, às vezes, OFX/CSV — fonte melhor que a página, com sinal e
   identificador por movimento.
7. **Compras parceladas.** No cartão, a parcela vem na descrição (`Vindi Catum PARC 08/12
   Campinas BR`, seção "Compras parceladas") e cada parcela carrega a **data da compra**. Nas 23
   faturas são 538 parcelas, R$ 87.292,21 (~35% dos gastos). Hoje cada uma é um lançamento da
   fatura que a cobrou — somando por fatura não há contagem dupla —, mas o sistema só guarda o
   texto: o filtro por mês soma as parcelas no mês da **compra** (mar/2026: R$ 25.334 pela data
   contra R$ 18.488 cobrados; out/2026: R$ 3.861 contra R$ 13.405), e uma identidade de movimento
   que ignore o "n/m" juntaria as 12 parcelas iguais (mesma data, valor e loja) numa só. A
   primeira parcela pode vir com centavos de arredondamento (Vindi Catum: 01/12 = R$ 10,52, as
   demais R$ 10,43).

Dados reais que o desenho precisa respeitar (extrato BB de outubro, captura `ed7792e7`):
`30/09 Saldo Anterior 2.825,71 D`, 45 movimentos com `Data | Histórico (+ complemento) |
Documento | Valor D/C | Saldo (às vezes)`, e `30/10 S A L D O 6.430,02 D`.

## Decisão (proposta)

**O que hoje é "lançamento de fatura" vira "movimento de conta".** Cada documento pertence a uma
**conta**; cada movimento tem **sinal** e **natureza** declarados; os **Gastos** são o dinheiro
que **sai do conjunto de contas da pessoa**, não o que circula entre elas.

### 1. Conta

Model `Conta` (`apps/financeiro`): organização, instituição (texto livre + domínio de origem),
tipo (`cartao`, `corrente`, `poupanca`, `investimento`), identificador (o que o documento traz:
`6516.XXXX.XXXX.1402`, `ag 1234 / cc 56789-0`), titular (texto), apelido (da pessoa), `propria`
(sim por padrão — é o que define "transferência interna").

- O **schema** de cada tipo de página declara qual campo único identifica a conta (papel
  `identifica_conta`, ver 2). Na primeira vez que um identificador aparece, a conta é criada e a
  tela de Gastos pede um apelido.
- Conta é estado da pessoa, como `Estabelecimento`: sobrevive à reconstrução da projeção.

### 2. Papéis e convenção no schema

O schema (`SchemaPagina.schema`) ganha, declarados na revisão e não adivinhados:

- **Tipo de documento**: `fatura_cartao` ou `extrato_conta` (outros depois). Diz o que a
  conferência e a natureza padrão significam.
- **Papel de cada campo único**: `identifica_conta`, `saldo_anterior`, `saldo_final`,
  `total_documento`, `vencimento`, `periodo` (ou nenhum).
- **Papel de cada coluna da lista**: `data`, `descricao`, `complemento` (concatenado à
  descrição), `documento`, `valor`, `debito`, `credito`, `dc` (marcador D/C separado),
  `saldo_linha` (ignorado nos movimentos, usado na conferência quando houver).
- **Convenção de sinal** (uma por schema): `positivo_e_saida` (fatura de cartão),
  `sufixo_dc` (extrato BB: `D` saída, `C` entrada), `negativo_e_saida`, `colunas_debito_credito`.
- **Linhas que não são movimento**: o parser já separa seção, especial e informativa; o schema
  passa a marcar as especiais conhecidas (`Saldo Anterior`, `S A L D O`, `SubTotal`) pelo papel.

A **conferência** muda com o tipo: fatura — saldo anterior + lançamentos = total (já existe);
extrato — saldo anterior + Σ movimentos assinados = saldo final (no BB: −2.825,71 + Σ = −6.430,02).
Essa conferência é o que pega o parser que lê metade das linhas zebradas.

O LLM propõe papéis e convenção junto com o schema (o prompt de `extractors/schema_pagina.py`
ganha esses campos); a tela de revisão mostra um seletor por campo e por coluna e a prévia já
exibe os movimentos assinados.

### 3. Movimento (substitui `Lancamento`)

Projeção, como hoje, a partir das alegações da leitura escolhida de cada documento:

- `conta`, `data` (a do documento: na fatura, a da compra), `competencia` (o mês em que o dinheiro
  saiu: o do vencimento da fatura que cobrou, ou a própria data no extrato), `valor` **assinado do
  ponto de vista da conta** (negativo = saída), `descricao`, `documento`, `secao`, `natureza`,
  `estabelecimento`, `identidade`, `fontes` (documentos/alegações que o sustentam), nível de
  classificação herdado.
- **Parcela**, quando houver: `parcela` (n), `parcelas` (m) e `compra` (a compra parcelada de
  origem, ver 3a). Lida da descrição pela projeção, sem LLM (`PARC 08/12`, `PARCELA 8 DE 12`;
  o padrão de cada banco vira regra do schema, como a convenção de sinal). O descritor do
  estabelecimento continua sem o "PARC" — a normalização já o remove.
- **Natureza**: `compra`, `tarifa`, `estorno`, `pagamento_fatura`, `transferencia_interna`,
  `aplicacao`, `resgate`, `entrada` (salário, Pix recebido, cashback), `saida_terceiros`
  (Pix/TED a outras pessoas), `outro`. Padrão por tipo de documento (fatura: compra; extrato:
  `entrada` se crédito, `saida_terceiros` se débito), refinado por regra e por conciliação (4).
- **Gastos** = Σ das saídas cuja natureza é de consumo (`compra`, `tarifa`, `saida_terceiros`,
  estornos abatendo); `pagamento_fatura`, `transferencia_interna`, `aplicacao` e `resgate` ficam
  fora. A tela ganha filtro por conta e os totais de entradas e saídas.
- **Regras de natureza** da pessoa (`descritor normalizado → natureza`, ex.: `BB RENDE FACIL` →
  aplicação/resgate conforme o sinal), no mesmo espírito de `Estabelecimento → categoria`.

### 3a. Compras parceladas

- **Compra parcelada** (`CompraParcelada`): conta + estabelecimento + data da compra + número de
  parcelas. As parcelas de faturas diferentes se juntam por essa chave, **não pelo valor** (a
  primeira parcela costuma diferir em centavos). O valor da compra é a soma das parcelas vistas;
  enquanto faltar alguma (anterior à primeira fatura capturada), o valor é estimado como
  `parcela × m` e marcado como estimado.
- **Compromisso futuro**: na fatura mais recente de cada cartão, a parcela n de m diz que faltam
  m − n; a tela mostra, mês a mês, quanto já está comprometido nas próximas faturas, e por compra.
- **Duas visões na tela de Gastos**:
  - **por competência** (padrão): quanto saiu em cada mês — bate com o total de cada fatura;
  - **por compra**: quanto se decidiu gastar em cada mês — a compra parcelada entra inteira na
    data da compra, uma vez só.

  Categoria e estabelecimento valem igual nas duas; só muda a data que agrupa e se a parcelada
  aparece como parcelas ou como compra.

### 4. Conciliação

Casa os dois lados de um mesmo dinheiro entre contas próprias e tira ambos dos Gastos:

- **Pagamento de fatura**: débito no extrato com valor = total da fatura e data no intervalo do
  vencimento (±3 dias) ↔ crédito `PGTO DEBITO CONTA` na fatura seguinte do cartão. No BB de
  outubro: `Pagto cartão crédito 13.405,63 D` em 28/10 ↔ total da fatura de 28/10.
- **Transferência interna**: saída numa conta própria e entrada de mesmo valor em outra conta
  própria em até 1 dia útil.
- Determinística, registrada (`Conciliacao`: par de movimentos, regra que casou, quando) e
  desfazível pela pessoa. O que não casa fica com a natureza padrão e aparece numa lista
  "a conferir" — sem LLM.

### 5. Identidade do movimento e deduplicação

- **Identidade** = conta + data + valor assinado + descrição normalizada + **parcela n/m** (quando
  houver) + ordem entre iguais no mesmo dia; para OFX, o `FITID`. Sem a parcela, as 12 parcelas de
  uma compra (mesma data, valor e loja) virariam uma.
- Documentos que se sobrepõem (extratos semanais, a mesma fatura capturada duas vezes)
  **reforçam** o mesmo movimento em vez de duplicá-lo; cada alegação mantém a sua evidência e o
  movimento lista as fontes. Isso substitui a deduplicação de fatura por documento que ficou
  pendente.

### 6. Fontes

- **Página do internet banking** (o que existe): um schema por tipo de página por banco, gerado
  uma vez e revisado (agora com papéis e convenção); o parser lê o resto.
- **OFX/CSV** (novo): `importar_extrato` (comando e upload na tela de Gastos). O OFX traz conta
  (`ACCTID`), sinal e `FITID`; vira alegações com evidência no arquivo (hash `ni:` do blob, como
  o acervo) e movimentos pela mesma projeção. CSV precisa de um mapeamento de colunas salvo por
  banco — o mesmo vocabulário de papéis do schema.

### 7. `dom2parser`: linhas zebradas

Estruturas irmãs que só diferem por classes alternadas (`iTrs`/`pTrs`, `odd`/`even`) devem virar
**um** registro, com seletor que cobre as duas (ex.: `table#tabelaExtrato tr`). Correção na
família/agrupamento do `dom2parser`, com teste no corpus (HTML sintético; o extrato real nunca
entra no repositório). Até lá, a conferência de saldo do extrato recusa a leitura pela metade.

## Fases

Cada fase entrega algo verificável e não muda os totais das faturas que já estão nos Gastos
(R$ 251.587,74 em 23 faturas) — exceto onde dito.

1. **Conta + papéis/convenção no schema + `Movimento`.** Model `Conta`; papéis e convenção no
   formato do schema, na normalização, no prompt e na tela de revisão; `Movimento` substitui
   `Lancamento` (migração: as 23 faturas reprojetadas na conta do cartão 1402, natureza `compra`
   ou `pagamento_fatura`); `competencia` e parcela (n, m) preenchidas; `/gastos/` com filtro
   por conta e filtro de mês **por competência**. **Verificação:** mesmos 2.482 lançamentos e
   mesmo total; o pagamento da fatura (`PGTO DEBITO CONTA`) passa a `pagamento_fatura`; a soma
   por competência de cada mês é igual ao total da fatura daquele mês; 538 parcelas reconhecidas.
2. **Compras parceladas** (seção 3a): agrupamento, visão por compra e compromisso futuro.
   **Verificação:** a Vindi Catum de 13/05/2025 vira uma compra de 12× (01/12 = R$ 10,52, demais
   R$ 10,43); a soma "por compra" de todas as compras com todas as parcelas capturadas é igual à
   soma das parcelas delas.
3. **`dom2parser` zebrado** (seção 7), com pin atualizado. **Verificação:** o registro do extrato
   BB cobre as 45 linhas de movimento.
4. **Extrato BB.** Schema `extrato_conta` com `sufixo_dc` e a conferência de saldo; movimentos
   entram, **fora dos Gastos** até a fase 5. **Verificação:** nos 4 extratos (jul–out/2026) a
   conferência fecha (outubro: −2.825,71 + Σ = −6.430,02).
5. **Conciliação + regras de natureza.** Pagamento de fatura e transferências internas; lista
   "a conferir"; extrato passa a alimentar os Gastos. **Verificação:** o `Pagto cartão crédito`
   de 28/10 casa com a fatura de outubro e sai dos Gastos; o total de gastos sobe só pelas saídas
   a terceiros e compras no débito.
6. **Identidade e deduplicação.** **Verificação:** capturar de novo um extrato que se sobrepõe a
   outro e a mesma fatura duas vezes não muda os totais.
7. **OFX/CSV e outros bancos.** Importador OFX primeiro; CSV com mapeamento salvo; schemas de
   página para bancos sem exportação.

## O que não muda

`policy_engine`, `AuditLog`, níveis de classificação, `PipelineEvent` e a imutabilidade das
alegações (`registrar_alegacao` segue sendo o único caminho). Movimentos e contas herdam o nível
dos documentos (extratos e faturas: `restrito`). Nada disso chama LLM fora da geração do schema.

## Questões em aberto (do dono)

1. **Sinal do `Movimento`**: proposta "negativo = saída da conta" (convenção contábil do extrato);
   a tela de Gastos continua mostrando gastos como positivos.
2. **Pix/TED a terceiros contam como gasto?** Proposta: sim, como `saida_terceiros`, categorizável
   (aluguel, diarista, presente) — a pessoa pode marcar um destinatário como "não é gasto"
   (ex.: transferência para a própria conta em outro banco ainda não cadastrada).
3. **Parcelas**: proposta na seção 3a — `Movimento` guarda a **competência** e a parcela (n, m),
   as parcelas se juntam em `CompraParcelada`, a tela tem as visões por competência (padrão) e
   por compra e o compromisso futuro. Falta decidir: a visão padrão ser mesmo a por competência
   (a que bate com as faturas).
4. **Contas de outras pessoas** (família, cartão adicional "1 - FERNANDO FERNANDES"): titular por
   movimento (seção do cartão adicional) ou por conta? Proposta: o titular vem da seção quando o
   documento a traz, e a tela filtra por titular.
