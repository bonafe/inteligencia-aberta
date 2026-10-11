"""Política de replicação: o motor puro (`regras`) ligado ao banco — ADR 011.

- `decidir` / `decidir_artefato`: carregam as regras **da organização dona do dado** e
  chamam o motor. Não registram nada: simular uma decisão não deixa rastro.
- `registrar_decisao`: o rastro (P8). `PipelineEvent` para toda decisão e `AuditLog`
  só para os níveis `restrito`/`confidencial`. **Nunca grava conteúdo**, só
  identificadores.
- `garantir_regras_padrao`: semeia as regras padrão da organização.
- `conceder` / `revogar`: concessões a um terceiro (a única porta de saída de
  `restrito`/`confidencial`).
"""

from django.utils import timezone

from apps.events.emit import emit

from . import regras
from .models import RegraReplicacao

#: Regras padrão por organização: (chave, sentido, par_tipo, nivel). Todas *permitir*.
#: Quem manda mais é o piso: ao enviar, terceiro nunca recebe `interno` e só recebe
#: `restrito`/`confidencial` por concessão, mesmo que uma regra diga o contrário.
PADROES = (
    ("enviar-proprio-tudo", "enviar", "proprio", None),
    ("enviar-terceiro-publico", "enviar", "terceiro", "publico"),
    ("receber-proprio-tudo", "receber", "proprio", None),
    ("receber-terceiro-tudo", "receber", "terceiro", None),
)


def garantir_regras_padrao(organizacao, *, completo: bool = False) -> int:
    """Semeia as regras padrão e devolve quantas criou.

    Sem `completo`, só semeia se a organização **não tem nenhuma** regra padrão —
    assim uma padrão desativada ou ausente nunca ressurge sozinha. Com `completo`,
    cria só as que faltam (é o que o comando `--semear` pede, de propósito).
    """
    existentes = set(
        RegraReplicacao.objects.filter(organizacao=organizacao, padrao=True)
        .values_list("chave_padrao", flat=True)
    )
    if existentes and not completo:
        return 0
    criadas = 0
    for chave, sentido, par_tipo, nivel in PADROES:
        if chave in existentes:
            continue
        RegraReplicacao.objects.create(
            organizacao=organizacao, efeito=regras.PERMITIR, sentido=sentido, par_tipo=par_tipo,
            nivel=nivel, padrao=True, chave_padrao=chave,
            observacao="Regra padrão — desative em vez de apagar.",
        )
        criadas += 1
    return criadas


def decidir(organizacao, ctx: regras.Contexto, *, agora=None) -> regras.Decisao:
    """Decide com as regras ativas da organização (semeando as padrão se ela não tem nenhuma)."""
    garantir_regras_padrao(organizacao)
    carregadas = [
        r.como_dados() for r in RegraReplicacao.objects.filter(organizacao=organizacao, ativa=True)
    ]
    return regras.avaliar(carregadas, ctx, agora or timezone.now())


def contexto_do_artefato(artefato, *, sentido: str, par_tipo: str, par_ref: str | None = None,
                         espaco_urn: str | None = None, espaco_explicito: bool = False) -> regras.Contexto:
    return regras.Contexto(
        sentido=sentido, par_tipo=par_tipo, par_ref=par_ref, nivel=artefato.classification_level,
        tipo_objeto=artefato.artifact_type, espaco_urn=espaco_urn, objeto_urn=artefato.urn,
        espaco_explicito=espaco_explicito,
    )


def decidir_artefato(artefato, *, sentido: str, par_tipo: str, par_ref: str | None = None,
                     espaco_urn: str | None = None, espaco_explicito: bool = False,
                     agora=None) -> regras.Decisao:
    """Decisão para um `Artifact`, usando as regras da organização dona dele.

    `espaco_explicito` só deve ser `True` se `espaco_urn` for um `Space` com linha no
    banco **em que o artefato está**: é o que habilita a concessão em lote por espaço.
    """
    from apps.accounts.models import Organization

    organizacao = Organization.objects.get(pk=artefato.tenant_id)
    ctx = contexto_do_artefato(
        artefato, sentido=sentido, par_tipo=par_tipo, par_ref=par_ref, espaco_urn=espaco_urn,
        espaco_explicito=espaco_explicito,
    )
    return decidir(organizacao, ctx, agora=agora)


def registrar_decisao(decisao: regras.Decisao, ctx: regras.Contexto, *, organizacao, artefato=None) -> None:
    """Deixa o rastro da decisão (P8). Nunca levanta e nunca grava conteúdo."""
    payload = {
        "sentido": ctx.sentido, "par_tipo": ctx.par_tipo, "par_ref": ctx.par_ref,
        "nivel": decisao.nivel_efetivo, "tipo_objeto": ctx.tipo_objeto, "espaco_urn": ctx.espaco_urn,
        "objeto_urn": ctx.objeto_urn, "permitido": decisao.permitido, "origem": decisao.origem,
        "regra_id": decisao.regra_id, "regra_padrao": decisao.regra_padrao,
    }
    verbo = "enviar" if ctx.sentido == regras.ENVIAR else "receber"
    try:
        emit(
            "federacao.decisao", "ok" if decisao.permitido else "ignorado",
            source="portal", subject_type="artifact" if artefato is not None else "",
            subject_id=artefato.id if artefato is not None else None, tenant_id=organizacao.id,
            message=f"{'permitido' if decisao.permitido else 'negado'} {verbo}: {decisao.motivo}",
            payload=payload,
        )
        if decisao.nivel_efetivo in regras.NIVEIS_SO_POR_CONCESSAO:
            from apps.artifacts.models import AuditLog

            AuditLog.objects.create(
                artifact=artefato, organization=organizacao, operation=f"replicacao.{verbo}",
                outcome="permitido" if decisao.permitido else "bloqueado", reason=decisao.motivo,
                metadata=payload,
            )
    except Exception:
        import logging

        logging.getLogger(__name__).exception("falha ao registrar a decisão de replicação")


def conceder(organizacao, artefato, *, par_ref: str, valida_ate, criada_por=None, observacao: str = "") -> RegraReplicacao:
    """Concessão: deixa `artefato` sair para o par `par_ref` até `valida_ate`.

    É uma regra de *permitir* com escopo de objeto e de par, **com validade**. Só ela
    faz `restrito`/`confidencial` sair para um terceiro. `ValueError` se faltar algo.
    """
    from django.core.exceptions import ValidationError

    if artefato.tenant_id != organizacao.id:
        raise ValueError("o artefato é de outra organização")
    if valida_ate is None or valida_ate <= timezone.now():
        raise ValueError("a concessão exige uma validade no futuro")
    try:
        return RegraReplicacao.objects.create(
            organizacao=organizacao, efeito=regras.PERMITIR, sentido=regras.ENVIAR, par_ref=par_ref,
            objeto_urn=artefato.urn, valida_ate=valida_ate, criada_por=criada_por, observacao=observacao,
        )
    except ValidationError as exc:
        raise ValueError("; ".join(m for msgs in exc.message_dict.values() for m in msgs)) from None


#: Teto da validade de uma concessão em lote (ADR 018): renovável, mas nunca "para sempre".
VALIDADE_MAXIMA_LOTE_DIAS = 366


def conceder_espaco(organizacao, espaco, *, par_ref: str, valida_ate, criada_por=None,
                    observacao: str = "") -> RegraReplicacao:
    """Concessão **em lote**: tudo o que estiver no `espaco` pode sair para `par_ref` até `valida_ate`.

    Só vale para um espaço **explícito** da própria organização (nunca o padrão, que
    contém tudo), com par nomeado e validade futura de no máximo
    `VALIDADE_MAXIMA_LOTE_DIAS`. Continua sendo um ato humano, revogável (`revogar`).
    Vale para o que estiver no espaço **no momento do envio**. `ValueError` se faltar algo.
    """
    from datetime import timedelta

    from django.core.exceptions import ValidationError

    from .models import Space

    if not isinstance(espaco, Space):
        raise ValueError("só um espaço explícito pode receber uma concessão em lote (o padrão contém tudo)")
    if espaco.organizacao_id != organizacao.id:
        raise ValueError("o espaço é de outra organização")
    if not (par_ref or "").strip():
        raise ValueError("a concessão em lote precisa dizer para qual par")
    agora = timezone.now()
    if valida_ate is None or valida_ate <= agora:
        raise ValueError("a concessão exige uma validade no futuro")
    if valida_ate > agora + timedelta(days=VALIDADE_MAXIMA_LOTE_DIAS):
        raise ValueError(f"a validade de uma concessão em lote é de no máximo {VALIDADE_MAXIMA_LOTE_DIAS} dias")
    try:
        return RegraReplicacao.objects.create(
            organizacao=organizacao, efeito=regras.PERMITIR, sentido=regras.ENVIAR, par_ref=par_ref.strip(),
            espaco_urn=espaco.urn, valida_ate=valida_ate, criada_por=criada_por, observacao=observacao,
        )
    except ValidationError as exc:
        raise ValueError("; ".join(m for msgs in exc.message_dict.values() for m in msgs)) from None


def revogar(regra: RegraReplicacao) -> RegraReplicacao:
    """Desativa a regra (concessão incluída). Não recolhe o que já foi copiado. Idempotente."""
    if regra.ativa:
        regra.ativa = False
        regra.revogada_em = timezone.now()
        regra.save(update_fields=["ativa", "revogada_em"])
    return regra
