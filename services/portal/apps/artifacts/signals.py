import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Artifact

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Artifact)
def dispatch_extraction_pipeline(sender, instance, created, **kwargs):
    if not created:
        return
    if instance.artifact_type != Artifact.Type.DOCUMENT:
        return
    content = instance.content or {}
    if not content.get("mhtml_path"):
        return

    logger.info(
        "signal post_save → despachando extract_text_from_mhtml — artifact_id=%s url=%s",
        instance.id, content.get("url", ""),
    )
    from .tasks import extract_text_from_mhtml
    extract_text_from_mhtml.delay(str(instance.id))
