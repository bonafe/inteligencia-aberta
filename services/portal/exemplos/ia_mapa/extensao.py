"""Exemplo de componente de UI para o Ultima Agora (ADR 023): um mapa de pontos dos artefatos com coordenadas.

Traz o componente `ia-map` (pasta estática da própria extensão), uma rota de leitura de domínio
(`/agora/api/v1/dominio/mapa/pontos/`) e nada mais: o núcleo não cita a extensão, só lê este manifesto."""
from apps.extensoes.contrato import Extensao, PackUI, Vocabulario

EXTENSAO = Extensao(
    nome="mapa",
    rotulo="Mapa",
    versao="1",
    requer_api=4,
    vocabulario=Vocabulario(prefixo="geo", uri="https://exemplo.invalid/ia/geo/v1#"),
    packs=(PackUI(base="ia_mapa/pack/", manifestos=("ia-map/ia-map.manifest.json",)),),
    dominio_urls="ia_mapa.urls_dominio",
)
