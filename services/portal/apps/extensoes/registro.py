"""Descoberta das extensões instaladas (ADR 022).

Lê o atributo `extensao` das `AppConfig` em `INSTALLED_APPS`; tirar uma extensão de lá a desliga
por inteiro (rotas, menu, consumidores), e o núcleo segue funcionando.
"""

from functools import cache

from django.apps import apps
from django.urls import include, path
from django.utils.module_loading import import_string

from .contrato import (API_EXTENSOES, Agendamento, Consumidor, EtapaProcessamento, Extensao, FonteIngestao,
                       ItemMenu, PackUI, TipoDado)


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
    nomes = [e.nome for e in encontradas]
    if len(set(nomes)) != len(nomes):
        raise ValueError(f"nome de extensão repetido: {nomes}")
    for e in encontradas:
        _validar_compatibilidade(e, set(nomes))
        _validar_tipos(e)
        _validar_chaves(e)
        _validar_packs(e)
    chaves = [c for e in encontradas for c in _chaves(e)]
    if len(set(chaves)) != len(chaves):
        raise ValueError(f"chave repetida entre extensões: {sorted({c for c in chaves if chaves.count(c) > 1})}")
    return tuple(encontradas)


RESERVADAS_ESTATICOS = ("agora/", "agora-ia/")


def _validar_packs(extensao: Extensao) -> None:
    """Pastas e manifestos são caminhos relativos de estáticos, fora das pastas do próprio Agora."""
    for pack in extensao.packs:
        for caminho in (pack.base, *pack.manifestos):
            partes = caminho.split("/")
            if not caminho or caminho.startswith("/") or ".." in partes or "\\" in caminho:
                raise ValueError(f"{extensao.nome}: caminho de pack inválido: {caminho!r}")
        if pack.base.startswith(RESERVADAS_ESTATICOS):
            raise ValueError(f"{extensao.nome}: o pack não pode ficar em {pack.base!r} (pasta do Agora)")


def _chaves(extensao: Extensao) -> list[str]:
    return [x.chave for x in (*extensao.fontes, *extensao.etapas, *extensao.agendamentos)]


def _validar_chaves(extensao: Extensao) -> None:
    """Fontes, etapas e agendamentos levam o nome da extensão (`email.pasta`): ficam rastreáveis nos
    eventos e nunca colidem com os de outra."""
    prefixo = extensao.nome + "."
    for chave in _chaves(extensao):
        if not chave.startswith(prefixo):
            raise ValueError(f"{extensao.nome}: a chave {chave!r} deve começar por {prefixo!r}")


def _validar_compatibilidade(extensao: Extensao, instaladas: set[str]) -> None:
    """Falha na subida, com a causa dita, em vez de quebrar no meio de uma captura."""
    if extensao.requer_api > API_EXTENSOES:
        raise ValueError(f"{extensao.nome}: exige a API de extensões {extensao.requer_api}, "
                         f"este núcleo oferece {API_EXTENSOES}")
    faltam = sorted(set(extensao.dependencias) - instaladas)
    if faltam:
        raise ValueError(f"{extensao.nome}: depende de extensões não instaladas: {faltam}")


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


def fontes() -> tuple[FonteIngestao, ...]:
    return tuple(f for e in extensoes() for f in e.fontes)


def fonte(chave: str) -> FonteIngestao | None:
    return next((f for f in fontes() if f.chave == chave), None)


def etapas() -> tuple[EtapaProcessamento, ...]:
    return tuple(t for e in extensoes() for t in e.etapas)


def etapa(chave: str) -> EtapaProcessamento | None:
    return next((t for t in etapas() if t.chave == chave), None)


def casa(etapa_: EtapaProcessamento, artefato) -> bool:
    """O artefato cai no filtro da etapa (tipo, categoria e sufixo do nome)?"""
    conteudo = artefato.content or {}
    if artefato.artifact_type not in etapa_.tipos:
        return False
    if etapa_.categorias and conteudo.get("categoria") not in etapa_.categorias:
        return False
    return not etapa_.sufixos or str(conteudo.get("nome", "")).lower().endswith(tuple(s.lower() for s in etapa_.sufixos))


def etapas_para(artefato) -> list[EtapaProcessamento]:
    return [t for t in etapas() if casa(t, artefato)]


def agendamentos() -> tuple[Agendamento, ...]:
    return tuple(a for e in extensoes() for a in e.agendamentos)


def packs() -> tuple[PackUI, ...]:
    return tuple(p for e in extensoes() for p in e.packs)


def urls_dos_manifestos() -> list[str]:
    """As URLs estáticas dos manifestos de componentes de todas as extensões, para o Agora registrar."""
    # Sem o hash do manifesto de estáticos: o precache offline lista os nomes originais, e o whitenoise serve os dois.
    from django.conf import settings

    base = "/" + settings.STATIC_URL.strip("/") + "/"
    return [base + pack.base.rstrip("/") + "/" + manifesto for pack in packs() for manifesto in pack.manifestos]


def urlpatterns_dominio() -> list:
    """Rotas de leitura de domínio das extensões (`/agora/api/v1/dominio/<nome>/…`)."""
    return [path(f"{e.nome}/", include(e.dominio_urls)) for e in extensoes() if e.dominio_urls]
