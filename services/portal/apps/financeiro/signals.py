"""Gastos ouvem o parser de página: extração válida de um schema com `alimenta_gastos` vira
lançamentos sozinha, sem LLM. Do lado de cá de propósito: `apps.artifacts` não conhece os Gastos.

Nunca levanta — a extração roda dentro da captura, e uma falha aqui não pode derrubá-la; o
motivo vai para o log e para o evento `financeiro.lancamentos`.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.artifacts.models import ExtracaoPagina

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ExtracaoPagina)
def registrar_lancamentos_do_parser(sender, instance, created, **kwargs):
    if not created or instance.status != ExtracaoPagina.Status.VALIDO or not instance.schema.alimenta_gastos:
        return
    from .lancamentos import registrar_extracao

    try:
        registrar_extracao(instance)
    except Exception as exc:
        logger.exception("[%s] lançamentos da extração %s não registrados", instance.document_text_id, instance.id)
        from apps.events.emit import emit

        emit("financeiro.lancamentos", "falhou", subject_type="artifact",
             subject_id=instance.document_text.document_id, tenant_id=instance.tenant_id,
             message=f"registro automático falhou: {exc}", payload={"extracao_id": str(instance.id)})
