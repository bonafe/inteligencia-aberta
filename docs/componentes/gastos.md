# Gastos a partir de faturas

Responde "quanto gastei em tal lugar" e "quanto gastei com tal categoria" a partir das faturas
capturadas. Código em `services/portal/apps/financeiro/`, tela em `/gastos/`.

## Fluxo

```
captura (MHTML) ─┬→ parser aprovado do tipo de página (sem LLM, validado) ──┐
                 └→ estruturação por LLM (manual, aba LLM do visualizador) ─┤
                                                                           ↓
          "Registrar lançamentos" (ou sozinho, se o schema alimenta os Gastos)
            → alegações (Claim + Evidence) → leitura escolhida → Lancamento → /gastos/
```

1. **A fatura diz** — a leitura vira alegações pelo serviço único `registrar_alegacao`, de uma
   de duas fontes:
   - **parser do tipo de página** (`registrar_extracao`): só extração que **validou**. Sujeito
     `mencao:<blob_hash>#parser/<schema>/lancamento/<n>` (do schema, não da execução: rodar de
     novo não duplica), produtor `parser-pagina`, versão `v<n>` do schema, sem modelo.
     **Automático** quando o schema tem a chave "registrar nos Gastos sozinho" ligada
     (`SchemaPagina.alimenta_gastos`, desligada por padrão; quem ouve é `financeiro/signals.py`);
   - **estruturação por LLM** (`registrar`): sujeito `mencao:<blob_hash>#estruturacao/<id>/lancamento/<n>`,
     produtor `estruturacao-llm`, com o modelo que extraiu.

   Cada lançamento tem `ia:data`, `ia:valor`, `ia:descricao` (e `ia:secao`, se houver), copiados
   como estão na página; o período/vencimento é `ia:periodo` no sujeito-base. Autor: o domínio
   da captura. Evidência: a linha original como trecho e o localizador da fonte.
2. **Uma leitura por documento** — a última registrada vira a escolhida (`LeituraLancamentos`).
   As alegações das outras leituras **continuam ativas** (ninguém as desmentiu), só não alimentam
   os Gastos. Trocar e voltar não perde nada; a fatura nunca é somada duas vezes.
3. **Projeção** — `Lancamento` é derivado das alegações ativas da leitura escolhida e pode ser refeito a qualquer
   momento: `manage.py reconstruir_lancamentos [--organizacao <slug>]`.
4. **O que a pessoa sabe** — `Estabelecimento` agrupa descritores de maquininha
   (`DescritorEstabelecimento`, texto normalizado) e tem uma `CategoriaGasto`. Juntar dois
   estabelecimentos e escolher a categoria vale para todas as faturas, as de antes e as futuras, e
   sobrevive à reconstrução da projeção.

## Regras do parsing (determinístico, sem LLM)

- Os nomes de campo vêm do LLM e variam: uma linha é qualquer objeto numa lista com uma chave de
  data (`data*`) e uma de valor (`valor*`, sem `us`/`dolar`). Descrição: `descricao`, `transacao`,
  `historico`, `estabelecimento`... Linha sem valor (cotação do dólar) fica de fora.
- Valor em formato brasileiro (`R$ -9.964,56`); sinal antes, depois ou entre parênteses.
- Data sem ano (`16/09`): o ano vem do período da fatura (`Out/26`, `15/10/2026`); se a data cair
  mais de 10 dias depois dele (parcela traz a data da compra original), é do ano anterior. Sem período, vale a data da captura.
- Tipo, na convenção de **fatura de cartão** (positivo é gasto): negativo com `PGTO`/`PAGAMENTO` é
  pagamento (fora das somas); outro negativo é estorno (abate); `IOF`, anuidade, juros e tarifa são
  tarifa; o resto é compra. Extrato de conta corrente tem outra convenção e ainda não é tratado.
- Descritor: sem acento, maiúsculo, sem número de pedido/telefone, parcela e o código de país/UF do
  fim. A cidade fica: o mesmo nome em cidades diferentes é outro estabelecimento até a pessoa juntar.

## Categoria e CNAE

A fatura traz só o descritor da maquininha, sem CNPJ, então a categoria oficial (CNAE) não chega
sozinha. A categoria é da pessoa: uma lista por organização (semeada com uma lista padrão na primeira
visita) e escolhida por estabelecimento. `Estabelecimento.empresa_ref` guarda um `cnpj:` quando a
pessoa souber; usar o CNAE (BrasilAPI, `services/mcp/tools/cnpj.py`) como sugestão de categoria é o
próximo passo, não feito.

## Limites

- Só faturas de cartão; a qualidade depende da estruturação escolhida (o 7B às vezes omite linhas).
- Parcela entra pela **data da compra** (é o que a fatura mostra): o filtro de período por mês põe
  a parcela 8/10 cobrada em outubro no mês da compra. Somar por fatura (competência) ainda não existe.
- Classificação: cada lançamento herda o nível do documento. A tela não envia nada a LLM.
- Eventos: `financeiro.lancamentos` (`ok`, ou `vazio` quando a estruturação não tem lista).
