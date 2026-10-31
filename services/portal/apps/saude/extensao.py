"""Manifesto da Saúde como extensão de domínio (ADR 022, etapa 5): resultados de exames de
laboratório. A segunda extensão existe para provar o contrato antes de fixá-lo."""

from apps.extensoes.contrato import AcaoLeitura, Consumidor, Extensao, ItemMenu, TipoDado, Vocabulario

CONSUMIDOR_EXAMES = "saude.exames"

EXTENSAO = Extensao(
    nome="saude",
    rotulo="Saúde",
    versao="1",
    vocabulario=Vocabulario(prefixo="saude", uri="https://w3id.org/inteligencia-aberta/saude/v1#"),
    urls="apps.saude.urls",
    prefixo_url="saude/",
    tipos=(
        # Dado de saúde é confidencial (LGPD, dado sensível), e o laudo de onde veio também.
        TipoDado("saude:Exame", "Resultado de exame", piso="confidencial", eleva_fonte=True,
                 predicados=("saude:analito", "saude:resultado", "saude:unidade", "saude:referencia",
                             "saude:material", "saude:dataColeta", "saude:laboratorio")),
    ),
    consumidores=(
        Consumidor(CONSUMIDOR_EXAMES, "Resultados na Saúde",
                   "A extração válida do laudo vira resultados de exame, sem LLM; o laudo passa a confidencial.",
                   ao_validar="apps.saude.exames.registrar_extracao"),
    ),
    acoes_leitura=(
        AcaoLeitura("Registrar exames", extracao="registrar-extracao/{id}/", estruturacao="registrar/{id}/"),
    ),
    menu=(ItemMenu("Saúde", "saude:exames"),),
)
