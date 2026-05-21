from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Artifact


@receiver(post_save, sender=Artifact)
def dispatch_extraction_pipeline(sender, instance, created, **kwargs):
    if not created:
        return
    if instance.artifact_type != Artifact.Type.DOCUMENT:
        return
    content = instance.content or {}
    if not content.get("mhtml_path"):
        return

    from .tasks import extract_text_from_mhtml
    extract_text_from_mhtml.delay(str(instance.id))
