"""Manifesto da extensão de exemplo: só declara; o núcleo lê, nunca importa o pacote pelo nome."""
from apps.extensoes.contrato import Extensao, ItemMenu, TipoDado, Vocabulario

EXTENSAO = Extensao(
    nome="exemplo",
    rotulo="Exemplo",
    versao="1",
    requer_api=2,
    vocabulario=Vocabulario(prefixo="ex", uri="https://exemplo.invalid/ia/ex/v1#"),
    urls="ia_exemplo.urls",
    prefixo_url="exemplo/",
    tipos=(TipoDado("ex:Nota", "Nota de exemplo", piso="interno", predicados=("ex:titulo", "ex:texto")),),
    menu=(ItemMenu("Exemplo", "ia_exemplo:inicio"),),
)
