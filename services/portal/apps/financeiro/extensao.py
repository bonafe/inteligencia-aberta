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
        TipoDado("fin:Movimento", "Movimento de conta", piso="restrito"),
        TipoDado("fin:Conta", "Conta ou cartão", piso="restrito"),
        TipoDado("fin:Estabelecimento", "Estabelecimento", piso="interno"),
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
