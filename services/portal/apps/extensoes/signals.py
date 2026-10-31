"""O núcleo entrega a extração válida do parser de página aos consumidores ligados no schema
(ADR 022): cada extensão declara `Consumidor.ao_validar` e não precisa escutar o núcleo sozinha.

Nunca levanta — a extração roda dentro da captura, e a falha de uma extensão não pode derrubá-la
nem impedir as outras; o motivo vai para o log e para o evento `extensao.consumidor`.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.module_loading import import_string

from apps.artifacts.models import ExtracaoPagina

from . import registro

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ExtracaoPagina)
def entregar_aos_consumidores(sender, instance, created, **kwargs):
    if not created or instance.status != ExtracaoPagina.Status.VALIDO:
        return
    for chave in instance.schema.consumidores or []:
        consumidor = registro.consumidor(chave)
        if consumidor is None or not consumidor.ao_validar:
            continue                    # extensão desinstalada: a chave fica gravada e espera
        try:
            import_string(consumidor.ao_validar)(instance)
        except Exception as exc:
            logger.exception("[%s] consumidor %s falhou na extração %s", instance.document_text_id, chave, instance.id)
            from apps.events.emit import emit

            emit("extensao.consumidor", "falhou", subject_type="artifact",
                 subject_id=instance.document_text.document_id, tenant_id=instance.tenant_id,
                 message=f"{consumidor.rotulo}: registro automático falhou: {exc}",
                 payload={"extracao_id": str(instance.id), "consumidor": chave})
