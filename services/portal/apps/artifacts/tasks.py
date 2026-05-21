import email as email_lib
import logging
import os
from email import policy as email_policy

import trafilatura
from celery import shared_task
from minio import Minio

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def extract_text_from_mhtml(self, artifact_id: str):
    from .models import Artifact, ArtifactLineage

    try:
        artifact = Artifact.objects.get(id=artifact_id)
    except Artifact.DoesNotExist:
        return {"status": "error", "reason": f"Artifact {artifact_id} não encontrado"}

    content = artifact.content or {}
    mhtml_path = content.get("mhtml_path")
    mhtml_bucket = content.get("mhtml_bucket", "inteligencia-aberta-mhtml")

    if not mhtml_path:
        return {"status": "skipped", "reason": "sem mhtml_path no conteúdo"}

    try:
        client = Minio(
            os.getenv("MINIO_ENDPOINT", "minio:9000"),
            access_key=os.getenv("MINIO_ROOT_USER", "minioadmin"),
            secret_key=os.getenv("MINIO_ROOT_PASSWORD", "substitua-por-senha-segura"),
            secure=False,
        )
        response = client.get_object(mhtml_bucket, mhtml_path)
        mhtml_bytes = response.read()
        response.close()
        response.release_conn()
    except Exception as exc:
        raise self.retry(exc=exc)

    # Extrai o HTML principal do pacote MHTML
    msg = email_lib.message_from_bytes(mhtml_bytes, policy=email_policy.default)
    html_content = None
    for part in msg.walk():
        if part.get_content_type() == "text/html" and html_content is None:
            charset = part.get_content_charset("utf-8") or "utf-8"
            payload = part.get_payload(decode=True)
            if payload:
                html_content = payload.decode(charset, errors="replace")
                break

    if not html_content:
        return {"status": "skipped", "reason": "HTML não encontrado no MHTML"}

    # Extração com trafilatura — tenta modo padrão, depois favor_recall
    text = trafilatura.extract(html_content, include_comments=False, include_tables=True)
    if not text:
        text = trafilatura.extract(
            html_content, include_comments=False, include_tables=True, favor_recall=True
        )

    if not text:
        return {"status": "skipped", "reason": "trafilatura não extraiu conteúdo"}

    child = Artifact.objects.create(
        artifact_type=Artifact.Type.TEXT,
        content={
            "text": text,
            "title": content.get("title", ""),
            "source_url": content.get("url", ""),
            "extraction_method": "trafilatura",
            "char_count": len(text),
            "word_count": len(text.split()),
        },
        classification_level=artifact.classification_level,
        tenant=artifact.tenant,
        allow_external_llm=artifact.allow_external_llm,
        classified_by=artifact.classified_by,
        info_type=artifact.info_type,
        sources=artifact.sources,
    )

    ArtifactLineage.objects.create(
        parent=artifact,
        child=child,
        transformation="text_extraction",
        processor=f"trafilatura:{trafilatura.__version__}",
        parameters={},
    )

    return {
        "status": "success",
        "child_artifact_id": str(child.id),
        "word_count": child.content["word_count"],
    }


@shared_task
def scan_unprocessed_documents():
    """Varre artefatos documento sem texto extraído e enfileira a extração."""
    from .models import Artifact, ArtifactLineage

    already_processed = ArtifactLineage.objects.filter(
        transformation="text_extraction"
    ).values_list("parent_id", flat=True)

    pending = Artifact.objects.filter(
        artifact_type=Artifact.Type.DOCUMENT
    ).exclude(id__in=already_processed)

    count = 0
    for artifact in pending:
        if (artifact.content or {}).get("mhtml_path"):
            extract_text_from_mhtml.delay(str(artifact.id))
            count += 1

    if count:
        logger.info("scan_unprocessed_documents: %d artefatos enfileirados", count)

    return {"enqueued": count}
