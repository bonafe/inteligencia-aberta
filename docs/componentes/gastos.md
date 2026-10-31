# Gastos a partir de faturas

Responde "quanto gastei em tal lugar" e "quanto gastei com tal categoria" a partir das faturas
capturadas. Código em `services/portal/apps/financeiro/`, tela em `/gastos/`.

## Fluxo

```
captura (MHTML) → estruturação por LLM (manual, aba LLM do visualizador)
  → "Registrar lançamentos" → alegações (Claim + Evidence)
  → projeção Lancamento → somas em /gastos/
```

1. **A fatura diz** — a estruturação escolhida vira alegações pelo serviço único
   `registrar_alegacao`. Cada lançamento é um sujeito
   `mencao:<blob_hash>#estruturacao/<id>/lancamento/<n>` com `ia:data`, `ia:valor`, `ia:descricao`
   (e `ia:secao`, se houver), copiados como estão na página. O período/vencimento da fatura é
   `ia:periodo` no sujeito `mencao:<blob_hash>#estruturacao/<id>`. Autor: o domínio da captura.
   Evidência: `localizador_tipo="estruturacao-llm"`, `{"estruturacao", "indice"}` e a linha original
   como trecho. Produtor `estruturacao-llm`, com o modelo que extraiu.
2. **Uma leitura por documento** — registrar outra estruturação do mesmo documento **retrata** as
   alegações da anterior (não apaga). Assim, comparar modelos não duplica gastos.
3. **Projeção** — `Lancamento` é derivado das alegações ativas e pode ser refeito a qualquer
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
