"""Elevar a classificação de um artefato que já existe — só para cima, sempre auditado.

A regra por domínio (`classificacao_dominio`) vale só na criação; reclassificar o que existe é um
ato explícito, e este é o único caminho para ele. Quem chama hoje: `registrar_alegacao`, quando a
alegação é de um tipo de dado de extensão com `eleva_fonte` (ADR 022: o laudo de onde se extraiu
um exame de saúde passa a ser confidencial também). Subir para `restrito` ou `confidencial` fecha
o LLM externo, como na criação. Nunca rebaixa e não toca o `policy_engine`.
"""

from django.db import transaction

from apps.extensoes.contrato import NIVEIS

from .models import Artifact, AuditLog, ExtracaoPagina

_SEM_LLM_EXTERNO = (Artifact.ClassificationLevel.RESTRICTED, Artifact.ClassificationLevel.CONFIDENTIAL)


def elevar(artefato: Artifact, nivel: str, *, motivo: str, user_id=None) -> bool:
    """Sobe `artefato` (e as extrações de página dele) a `nivel`, se estiver abaixo. Devolve se subiu."""
    if nivel not in NIVEIS:
        raise ValueError(f"nível desconhecido: {nivel!r}")
    if NIVEIS.index(nivel) <= NIVEIS.index(artefato.classification_level):
        return False
    anterior = artefato.classification_level
    with transaction.atomic():
        artefato.classification_level = nivel
        campos = ["classification_level"]
        if nivel in _SEM_LLM_EXTERNO and artefato.allow_external_llm:
            artefato.allow_external_llm = False
            campos.append("allow_external_llm")
        artefato.save(update_fields=campos)
        for extracao in ExtracaoPagina.objects.filter(document_text__document=artefato):
            if NIVEIS.index(extracao.classification_level) < NIVEIS.index(nivel):
                ExtracaoPagina.objects.filter(pk=extracao.pk).update(classification_level=nivel)
        AuditLog.objects.create(
            artifact=artefato, user_id=user_id, organization_id=artefato.tenant_id,
            operation="classificacao.elevada", outcome="permitido", reason=motivo,
            metadata={"de": anterior, "para": nivel},
        )
    return True
