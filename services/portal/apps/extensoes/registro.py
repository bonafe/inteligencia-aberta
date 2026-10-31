"""Descoberta das extensões instaladas (ADR 022).

Lê o atributo `extensao` das `AppConfig` em `INSTALLED_APPS`; tirar uma extensão de lá a desliga
por inteiro (rotas, menu, consumidores), e o núcleo segue funcionando.
"""

from functools import cache

from django.apps import apps
from django.urls import include, path
from django.utils.module_loading import import_string

from .contrato import Consumidor, Extensao, ItemMenu, TipoDado


@cache
def extensoes() -> tuple[Extensao, ...]:
    encontradas = []
    for config in apps.get_app_configs():
        alvo = getattr(config, "extensao", None)
        if alvo:
            extensao = import_string(alvo) if isinstance(alvo, str) else alvo
            if not isinstance(extensao, Extensao):
                raise TypeError(f"{config.name}.extensao não é um Extensao")
            encontradas.append(extensao)
    prefixos = [e.vocabulario.prefixo for e in encontradas]
    if len(set(prefixos)) != len(prefixos):
        raise ValueError(f"prefixo de vocabulário repetido entre extensões: {prefixos}")
    for e in encontradas:
        _validar_tipos(e)
    return tuple(encontradas)


def _validar_tipos(extensao: Extensao) -> None:
    """Tipos e predicados no vocabulário da própria extensão; cada predicado em um tipo só."""
    prefixo = extensao.vocabulario.prefixo + ":"
    vistos: set[str] = set()
    for t in extensao.tipos:
        for nome in (t.nome, *t.predicados):
            if not nome.startswith(prefixo):
                raise ValueError(f"{extensao.nome}: {nome!r} fora do vocabulário {prefixo}")
        repetidos = vistos & set(t.predicados)
        if repetidos:
            raise ValueError(f"{extensao.nome}: predicado em mais de um tipo: {sorted(repetidos)}")
        vistos |= set(t.predicados)
    nomes = [t.nome for t in extensao.tipos]
    if len(set(nomes)) != len(nomes):
        raise ValueError(f"{extensao.nome}: tipo repetido: {nomes}")


def prefixos() -> set[str]:
    """Os prefixos de vocabulário das extensões instaladas (`fin`)."""
    return {e.vocabulario.prefixo for e in extensoes()}


def modulos() -> set[str]:
    """Os pacotes Python das extensões instaladas."""
    return {c.name for c in apps.get_app_configs() if getattr(c, "extensao", None)}


def consumidores() -> tuple[Consumidor, ...]:
    return tuple(c for e in extensoes() for c in e.consumidores)


def consumidor(chave: str) -> Consumidor | None:
    return next((c for c in consumidores() if c.chave == chave), None)


def tipos() -> tuple[TipoDado, ...]:
    return tuple(t for e in extensoes() for t in e.tipos)


def tipo(nome: str) -> TipoDado | None:
    return next((t for t in tipos() if t.nome == nome), None)


def tipo_do_predicado(predicado: str) -> TipoDado | None:
    """O tipo que declara o predicado (`fin:valor` → `fin:Movimento`), ou None."""
    return next((t for t in tipos() if predicado in t.predicados), None)


def menu() -> tuple[ItemMenu, ...]:
    return tuple(i for e in extensoes() for i in e.menu)


def acoes_leitura() -> list[dict]:
    """As ações de leitura com as rotas já completas, para o visualizador (JSON)."""
    def rota(extensao, relativa):
        return f"/{extensao.prefixo_url}{relativa}" if relativa else ""
    return [{"rotulo": a.rotulo, "extracao": rota(e, a.extracao), "estruturacao": rota(e, a.estruturacao)}
            for e in extensoes() for a in e.acoes_leitura]


def urlpatterns() -> list:
    return [path(e.prefixo_url, include(e.urls)) for e in extensoes() if e.urls]
