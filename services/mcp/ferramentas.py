"""Registro de ferramentas do MCP (ADR 023, fase 4).

Uma ferramenta é um dicionário simples — o plugin não importa nada deste serviço:

    {"id": "consultar_cep", "descricao": "…", "fonte": "ViaCEP",
     "parametros": {"cep": {"tipo": "string", "descricao": "8 dígitos", "obrigatorio": True}},
     "classificacao_maxima": "publico",          # o dado mais restrito que ela pode receber
     "executar": async_function}                 # async def executar(**parametros) -> dict

`classificacao_maxima` é a política da ferramenta: uma ferramenta que manda os parâmetros para fora
(a internet, um serviço de terceiro) é `publico`, e o MCP recusa qualquer chamada cujo dado seja mais
restrito, sem depender de o chamador lembrar disso. Chamada sem classificação vale `confidencial`.

Descoberta (só código confiável, que roda neste processo): o que está em `tools/` (do projeto) e o que a
variável `FERRAMENTAS_MCP` lista — o nome de um *entry point* do grupo `inteligencia_aberta.ferramentas`
ou um módulo importável (`pacote.modulo`) que expõe `FERRAMENTAS`, uma lista desses dicionários.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Awaitable, Callable

NIVEIS = ("publico", "interno", "restrito", "confidencial")
GRUPO = "inteligencia_aberta.ferramentas"
TIPOS = {"string": str, "integer": int, "number": (int, float), "boolean": bool}


class ParametroInvalido(ValueError):
    pass


@dataclass(frozen=True)
class Ferramenta:
    id: str
    descricao: str
    parametros: dict
    executar: Callable[..., Awaitable[dict]]
    classificacao_maxima: str = "publico"
    fonte: str = ""
    confianca: float | None = None
    timeout_s: float = 15.0

    def esquema(self) -> dict:
        return {"id": self.id, "descricao": self.descricao, "parametros": self.parametros,
                "classificacao_maxima": self.classificacao_maxima, "fonte": self.fonte}


def construir(dados: dict, origem: str = "") -> Ferramenta:
    """Valida o dicionário de um plugin e devolve a `Ferramenta`; erro de contrato falha na subida."""
    local = f" ({origem})" if origem else ""
    try:
        id_ = dados["id"]
        if not isinstance(id_, str) or not id_.replace("_", "").isalnum():
            raise ValueError(f"id inválido: {id_!r}")
        if not callable(dados["executar"]):
            raise ValueError("`executar` não é chamável")
        maxima = dados.get("classificacao_maxima", "publico")
        if maxima not in NIVEIS:
            raise ValueError(f"classificacao_maxima desconhecida: {maxima!r}")
        parametros = dict(dados.get("parametros", {}))
        for nome, spec in parametros.items():
            if spec.get("tipo", "string") not in TIPOS:
                raise ValueError(f"parâmetro {nome!r}: tipo desconhecido {spec.get('tipo')!r}")
        return Ferramenta(id=id_, descricao=dados["descricao"], parametros=parametros, executar=dados["executar"],
                          classificacao_maxima=maxima, fonte=dados.get("fonte", ""),
                          confianca=dados.get("confianca"), timeout_s=float(dados.get("timeout_s", 15.0)))
    except (KeyError, ValueError, TypeError, AttributeError) as erro:
        raise ValueError(f"ferramenta inválida{local}: {erro}") from erro


def _modulos_dos_plugins(valor: str, pontos=None) -> list[str]:
    nomes = [n.strip() for n in (valor or "").split(",") if n.strip()]
    if not nomes:
        return []
    por_nome = {ep.name: ep.value for ep in (entry_points(group=GRUPO) if pontos is None else pontos)}
    modulos: list[str] = []
    for nome in nomes:
        alvo = por_nome.get(nome, nome).split(":")[0]
        if alvo not in modulos:
            modulos.append(alvo)
    return modulos


def carregar(embutidas: list[dict], valor_env: str = "", pontos=None) -> dict[str, Ferramenta]:
    """Monta o catálogo: as do projeto e as dos plugins. Id repetido é erro (nada é sobrescrito em silêncio)."""
    catalogo: dict[str, Ferramenta] = {}

    def acrescentar(dados: dict, origem: str):
        ferramenta = construir(dados, origem)
        if ferramenta.id in catalogo:
            raise ValueError(f"ferramenta repetida: {ferramenta.id!r} ({origem})")
        catalogo[ferramenta.id] = ferramenta

    for dados in embutidas:
        acrescentar(dados, "projeto")
    for modulo in _modulos_dos_plugins(valor_env, pontos):
        for dados in importlib.import_module(modulo).FERRAMENTAS:
            acrescentar(dados, modulo)
    return catalogo


def validar_parametros(ferramenta: Ferramenta, dados: dict) -> dict:
    """Só os parâmetros declarados, com tipo conferido; faltou um obrigatório ou sobrou um, é erro."""
    sobras = sorted(set(dados) - set(ferramenta.parametros))
    if sobras:
        raise ParametroInvalido(f"parâmetros desconhecidos: {sobras}")
    limpos = {}
    for nome, spec in ferramenta.parametros.items():
        if nome not in dados or dados[nome] is None:
            if spec.get("obrigatorio", False):
                raise ParametroInvalido(f"parâmetro obrigatório ausente: {nome}")
            continue
        valor, tipo = dados[nome], spec.get("tipo", "string")
        if isinstance(valor, bool) and tipo != "boolean" or not isinstance(valor, TIPOS[tipo]):
            raise ParametroInvalido(f"parâmetro {nome!r} deve ser {tipo}")
        limpos[nome] = valor
    return limpos


def bloqueio_por_politica(ferramenta: Ferramenta, classificacao: str | None) -> str | None:
    """O motivo de não poder chamar a ferramenta com dado desta classificação, ou None. Determinístico."""
    nivel = classificacao if classificacao in NIVEIS else "confidencial"      # sem classificação: o mais restrito
    if NIVEIS.index(nivel) > NIVEIS.index(ferramenta.classificacao_maxima):
        return f"dado {nivel!r} não pode ir a uma ferramenta que só aceita até {ferramenta.classificacao_maxima!r}"
    return None
