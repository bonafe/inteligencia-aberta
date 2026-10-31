"""Manifesto dos Gastos como extensão de domínio (ADR 022)."""

from apps.extensoes.contrato import AcaoLeitura, Consumidor, Extensao, ItemMenu, TipoDado, Vocabulario

#: Consumidor da extração válida do parser de página: vira lançamentos nos Gastos (`signals.py`).
CONSUMIDOR_MOVIMENTOS = "fin.movimentos"

EXTENSAO = Extensao(
    nome="financeiro",
    rotulo="Gastos",
    versao="1",
    vocabulario=Vocabulario(prefixo="fin", uri="https://w3id.org/inteligencia-aberta/fin/v1#"),
    urls="apps.financeiro.urls",
    prefixo_url="gastos/",
    tipos=(
        # O que o documento diz: cada lançamento e o período da fatura (`lancamentos.py`).
        TipoDado("fin:Movimento", "Movimento de conta", piso="restrito",
                 predicados=("fin:data", "fin:valor", "fin:descricao", "fin:secao", "fin:documento",
                             "fin:sentido", "fin:periodo")),
        # De que conta é o documento e o que a pessoa decide sobre ela (`decisoes.py`).
        TipoDado("fin:Conta", "Conta ou cartão", piso="restrito",
                 predicados=("fin:conta", "fin:titular", "fin:apelido", "fin:contaPropria", "fin:mesmaContaQue")),
        # O que a pessoa decide sobre o lugar: categoria, nome, empresa, fusão; e as categorias.
        TipoDado("fin:Estabelecimento", "Estabelecimento", piso="interno",
                 predicados=("fin:categoria", "fin:nome", "fin:empresa", "fin:mesmoLugarQue", "fin:rotulo")),
    ),
    consumidores=(
        Consumidor(CONSUMIDOR_MOVIMENTOS, "Lançamentos nos Gastos",
                   "A extração válida vira movimentos nos Gastos sozinha, sem LLM."),
    ),
    acoes_leitura=(
        AcaoLeitura("Registrar lançamentos", extracao="registrar-extracao/{id}/", estruturacao="registrar/{id}/"),
    ),
    menu=(ItemMenu("Gastos", "financeiro:gastos"),),
)
