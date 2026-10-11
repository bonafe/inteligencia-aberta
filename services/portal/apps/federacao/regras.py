"""Motor de regras de replicação — ADR 011. **Função pura**: sem banco, sem rede, sem LLM.

Dada uma decisão a tomar — enviar (ou receber) um objeto a (de) um par, num espaço —,
devolve `permitido` ou `negado`, a **regra que decidiu** e o motivo. Três camadas:

1. **Pisos** (não editáveis; só ao *enviar*): `interno` nunca sai para terceiro;
   `restrito`/`confidencial` só saem para terceiro por **concessão** — regra de
   *permitir* com escopo de objeto e de par, **com validade** e ainda vigente; rótulo
   desconhecido vale como `confidencial`.
2. **Regras**, as padrão e as do usuário, todas do mesmo tipo (`RegraDados`).
3. **Precedência:** se alguma regra de *negar* casa, nega (**negar vence, sem prioridade
   nem ordem**); senão, se alguma de *permitir* casa, permite; senão, **nega**.

Separado do `policy_engine` (que decide o uso de LLM e não é tocado). As regras vêm do
banco por `apps.federacao.politica`; aqui só entram dados, o que torna o motor
inteiramente testável.
"""

from dataclasses import dataclass, field
from datetime import datetime

PERMITIR = "permitir"
NEGAR = "negar"
ENVIAR = "enviar"
RECEBER = "receber"
PROPRIO = "proprio"
TERCEIRO = "terceiro"

#: Os mesmos valores de `Artifact.ClassificationLevel` (um teste confere a igualdade).
NIVEIS = ("publico", "interno", "restrito", "confidencial")
#: O que vale para um rótulo que o motor não conhece (seção 10 de `federacao.md`).
NIVEL_DESCONHECIDO = "confidencial"
#: Níveis que só saem para terceiro por concessão.
NIVEIS_SO_POR_CONCESSAO = ("restrito", "confidencial")


@dataclass(frozen=True)
class RegraDados:
    """Uma regra, sem nada de banco. `None` numa condição = "qualquer"."""

    id: str
    efeito: str
    sentido: str
    par_ref: str | None = None
    par_tipo: str | None = None
    nivel: str | None = None
    tipo_objeto: str | None = None
    espaco_urn: str | None = None
    objeto_urn: str | None = None
    valida_ate: datetime | None = None
    ativa: bool = True
    padrao: bool = False


@dataclass(frozen=True)
class Contexto:
    sentido: str
    par_tipo: str
    nivel: str
    par_ref: str | None = None
    tipo_objeto: str | None = None
    espaco_urn: str | None = None
    objeto_urn: str | None = None
    #: `espaco_urn` é um espaço **explícito** (com linha no banco) em que o objeto está.
    #: O espaço padrão ("tudo da organização") nunca vale como concessão em lote (ADR 018, item 2).
    espaco_explicito: bool = False


@dataclass(frozen=True)
class Decisao:
    permitido: bool
    motivo: str
    origem: str                       # "piso" | "regra" | "padrao-negar"
    nivel_efetivo: str
    regra_id: str | None = None
    regra_padrao: bool = False
    casaram: tuple = field(default_factory=tuple)   # ids das regras que casaram


def nivel_efetivo(nivel: str) -> str:
    """O próprio nível, ou `confidencial` se o rótulo for desconhecido."""
    return nivel if nivel in NIVEIS else NIVEL_DESCONHECIDO


def _vigente(regra: RegraDados, agora: datetime) -> bool:
    return regra.ativa and (regra.valida_ate is None or agora < regra.valida_ate)


def casa(regra: RegraDados, ctx: Contexto, nivel: str, agora: datetime) -> bool:
    """A regra se aplica a este contexto? Toda condição preenchida precisa bater."""
    return (
        _vigente(regra, agora)
        and regra.sentido == ctx.sentido
        and regra.par_ref in (None, ctx.par_ref)
        and regra.par_tipo in (None, ctx.par_tipo)
        and regra.nivel in (None, nivel)
        and regra.tipo_objeto in (None, ctx.tipo_objeto)
        and regra.espaco_urn in (None, ctx.espaco_urn)
        and regra.objeto_urn in (None, ctx.objeto_urn)
    )


def _eh_concessao(regra: RegraDados, ctx: Contexto, agora: datetime) -> bool:
    """Concessão: *permitir* para este par, com validade ainda vigente, por **objeto** ou por **espaço**.

    - por objeto: `objeto_urn` é o deste objeto;
    - em lote por espaço (ADR 018): `espaco_urn` é o de um espaço **explícito** em que o
      objeto está (`ctx.espaco_explicito`) e a regra não é por objeto.

    Nos dois casos o par é nomeado e a validade é obrigatória.
    """
    if not (
        regra.efeito == PERMITIR
        and regra.par_ref is not None and regra.par_ref == ctx.par_ref
        and regra.valida_ate is not None
    ):
        return False
    if regra.objeto_urn is not None:
        return regra.objeto_urn == ctx.objeto_urn
    return regra.espaco_urn is not None and regra.espaco_urn == ctx.espaco_urn and ctx.espaco_explicito


def avaliar(regras, ctx: Contexto, agora: datetime) -> Decisao:
    """Decide. `regras` é qualquer iterável de `RegraDados`; `agora` vem de fora (pureza)."""
    regras = sorted(regras, key=lambda r: r.id)          # ordem estável só para relatar
    nivel = nivel_efetivo(ctx.nivel)
    casaram = [r for r in regras if casa(r, ctx, nivel, agora)]
    ids = tuple(r.id for r in casaram)

    if ctx.sentido == ENVIAR and ctx.par_tipo == TERCEIRO:
        if nivel == "interno":
            return Decisao(False, "piso: o nível interno nunca sai para terceiro", "piso", nivel, casaram=ids)
        if nivel in NIVEIS_SO_POR_CONCESSAO:
            concessao = next((r for r in casaram if _eh_concessao(r, ctx, agora)), None)
            if concessao is None:
                return Decisao(
                    False,
                    f"piso: o nível {nivel} só sai para terceiro por concessão explícita "
                    "(permitir para este objeto ou este espaço e este par, com validade)",
                    "piso", nivel, casaram=ids,
                )

    negar = next((r for r in casaram if r.efeito == NEGAR), None)
    if negar:
        return Decisao(False, f"regra {negar.id}: negar", "regra", nivel, negar.id, negar.padrao, ids)
    permitir = next((r for r in casaram if r.efeito == PERMITIR), None)
    if permitir:
        return Decisao(True, f"regra {permitir.id}: permitir", "regra", nivel, permitir.id, permitir.padrao, ids)
    return Decisao(False, "nenhuma regra permite (negação por padrão)", "padrao-negar", nivel, casaram=ids)
