import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.events.context import set_correlation_id, set_tenant_id
from apps.events.emit import emit

from .models import Artifact

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Artifact)
def dispatch_extraction_pipeline(sender, instance, created, **kwargs):
    """Dispara a extração ao criar um artefato de documento com MHTML.

    As três saídas antecipadas emitem evento em vez de retornar em silêncio: um
    artefato que nunca entra no pipeline era, até aqui, indistinguível de um que
    entrou e falhou — e este signal roda no processo web, onde os `logger.info`
    sequer aparecem (não há dicionário LOGGING configurado).
    """
    if not created:
        return

    from .tasks import correlacao_do_artefato

    correlation = correlacao_do_artefato(instance.id)
    set_correlation_id(correlation)
    set_tenant_id(instance.tenant_id)

    content = instance.content or {}

    def ignorado(motivo: str):
        emit("artefato.ignorado", "ignorado",
             correlation_id=correlation, source="portal",
             subject_type="artifact", subject_id=instance.id,
             tenant_id=instance.tenant_id,
             message=motivo,
             payload={"artifact_type": instance.artifact_type,
                      "url": content.get("url", "")})

    if instance.artifact_type != Artifact.Type.DOCUMENT:
        ignorado(f"tipo '{instance.artifact_type}' não entra no pipeline de extração")
        return
    if not content.get("mhtml_path"):
        ignorado("artefato de documento sem mhtml_path — nada a extrair")
        return

    emit("captura.registrada", "ok",
         correlation_id=correlation, source="portal",
         subject_type="artifact", subject_id=instance.id,
         tenant_id=instance.tenant_id,
         user_id=instance.classified_by_id,
         message=f"artefato criado para {content.get('url', '')}",
         payload={"url": content.get("url", ""),
                  "titulo": content.get("title", ""),
                  "artifact_id": str(instance.id),
                  "classificacao": instance.classification_level})

    logger.info(
        "signal post_save → despachando extract_text_from_mhtml — artifact_id=%s url=%s",
        instance.id, content.get("url", ""),
    )
    from .tasks import extract_text_from_mhtml
    extract_text_from_mhtml.delay(str(instance.id))
