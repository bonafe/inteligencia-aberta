import email as email_lib
import logging
import os
import uuid as uuid_lib
from email import policy as email_policy

import trafilatura
from celery import shared_task
from django.conf import settings
from minio import Minio

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _split_text(text: str, chunk_size: int = 1000, overlap: int = 100) -> list[str]:
    """Divide texto em chunks com overlap, preferindo parágrafos e frases."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        if end < len(text):
            for sep in ["\n\n", "\n", ". ", " "]:
                idx = text.rfind(sep, start + overlap, end)
                if idx > start + overlap:
                    end = idx + len(sep)
                    break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break
        start = end - overlap

    return chunks


# ── Etapa 1: extração de texto ────────────────────────────────────────────────

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

    # Idempotência: já tem filho texto?
    existing_child = ArtifactLineage.objects.filter(
        parent=artifact, transformation="text_extraction"
    ).first()
    if existing_child:
        fragment_text.delay(str(existing_child.child_id))
        return {"status": "already_done", "child_artifact_id": str(existing_child.child_id)}

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

    fragment_text.delay(str(child.id))

    return {
        "status": "success",
        "child_artifact_id": str(child.id),
        "word_count": child.content["word_count"],
    }


# ── Etapa 2: fragmentação ─────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def fragment_text(self, texto_artifact_id: str):
    from .models import Artifact, ArtifactLineage

    try:
        artifact = Artifact.objects.get(id=texto_artifact_id)
    except Artifact.DoesNotExist:
        return {"status": "error", "reason": f"Artifact {texto_artifact_id} não encontrado"}

    # Idempotência: já foi fragmentado?
    existing_ids = list(
        ArtifactLineage.objects.filter(
            parent=artifact, transformation="fragmentation"
        ).values_list("child_id", flat=True)
    )
    if existing_ids:
        for frag_id in existing_ids:
            frag = Artifact.objects.filter(id=frag_id).first()
            if frag and not (frag.content or {}).get("qdrant_point_id"):
                embed_fragment.delay(str(frag_id))
        return {"status": "already_done", "fragments": len(existing_ids)}

    text = (artifact.content or {}).get("text", "")
    if not text:
        return {"status": "skipped", "reason": "sem texto no conteúdo"}

    chunk_size = getattr(settings, "FRAGMENT_CHUNK_SIZE", 1000)
    overlap = getattr(settings, "FRAGMENT_OVERLAP", 100)
    chunks = _split_text(text, chunk_size=chunk_size, overlap=overlap)

    source_url = (artifact.content or {}).get("source_url", "")
    title = (artifact.content or {}).get("title", "")

    for i, chunk in enumerate(chunks):
        frag = Artifact.objects.create(
            artifact_type=Artifact.Type.FRAGMENT,
            content={
                "text": chunk,
                "fragment_index": i,
                "total_fragments": len(chunks),
                "source_url": source_url,
                "title": title,
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
            child=frag,
            transformation="fragmentation",
            processor=f"split_text:chunk={chunk_size},overlap={overlap}",
            parameters={"chunk_size": chunk_size, "overlap": overlap, "fragment_index": i},
        )
        embed_fragment.delay(str(frag.id))

    return {"status": "success", "fragments": len(chunks)}


# ── Etapa 3: embedding ────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def embed_fragment(self, fragmento_artifact_id: str):
    from .models import Artifact, ArtifactLineage
    from .embeddings import ensure_collection, get_embedding_model, get_qdrant_client

    try:
        artifact = Artifact.objects.get(id=fragmento_artifact_id)
    except Artifact.DoesNotExist:
        return {"status": "error", "reason": f"Artifact {fragmento_artifact_id} não encontrado"}

    content = artifact.content or {}

    if content.get("qdrant_point_id"):
        return {"status": "already_done", "qdrant_point_id": content["qdrant_point_id"]}

    text = content.get("text", "")
    if not text:
        return {"status": "skipped", "reason": "sem texto no fragmento"}

    try:
        model = get_embedding_model()
        vector = list(model.embed([text]))[0].tolist()
    except Exception as exc:
        raise self.retry(exc=exc)

    # Monta payload com linhagem completa
    parent_lin = ArtifactLineage.objects.filter(
        child=artifact, transformation="fragmentation"
    ).first()
    texto_id = str(parent_lin.parent_id) if parent_lin else None

    avo_lin = (
        ArtifactLineage.objects.filter(
            child_id=texto_id, transformation="text_extraction"
        ).first()
        if texto_id
        else None
    )
    source_id = str(avo_lin.parent_id) if avo_lin else None

    payload = {
        "artifact_id": str(artifact.id),
        "texto_artifact_id": texto_id,
        "source_artifact_id": source_id,
        "tenant_id": str(artifact.tenant_id),
        "fragment_index": content.get("fragment_index", 0),
        "classification_level": artifact.classification_level,
        "source_url": content.get("source_url", ""),
        "title": content.get("title", ""),
        "text_preview": text[:200],
        "created_at": artifact.created_at.isoformat(),
    }

    try:
        qdrant = get_qdrant_client()
        collection = ensure_collection(qdrant, str(artifact.tenant_id), len(vector))
        point_id = str(uuid_lib.uuid4())

        from qdrant_client.models import PointStruct
        qdrant.upsert(
            collection_name=collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
    except Exception as exc:
        raise self.retry(exc=exc)

    content["qdrant_point_id"] = point_id
    content["qdrant_collection"] = collection
    artifact.content = content
    artifact.save(update_fields=["content", "updated_at"])

    return {"status": "success", "qdrant_point_id": point_id, "collection": collection}


# ── Catch-up periódico (Celery Beat) ─────────────────────────────────────────

@shared_task
def scan_unprocessed_documents():
    """Varre gaps nos três estágios do pipeline e enfileira tarefas pendentes."""
    from .models import Artifact, ArtifactLineage

    # Gap 1: documento sem texto extraído
    extracted_ids = ArtifactLineage.objects.filter(
        transformation="text_extraction"
    ).values_list("parent_id", flat=True)
    gap1 = 0
    for art in Artifact.objects.filter(artifact_type=Artifact.Type.DOCUMENT).exclude(id__in=extracted_ids):
        if (art.content or {}).get("mhtml_path"):
            extract_text_from_mhtml.delay(str(art.id))
            gap1 += 1

    # Gap 2: texto sem fragmentos
    fragmented_ids = ArtifactLineage.objects.filter(
        transformation="fragmentation"
    ).values_list("parent_id", flat=True)
    gap2 = 0
    for art in Artifact.objects.filter(artifact_type=Artifact.Type.TEXT).exclude(id__in=fragmented_ids):
        fragment_text.delay(str(art.id))
        gap2 += 1

    # Gap 3: fragmento sem embedding no Qdrant
    gap3 = 0
    for art in Artifact.objects.filter(artifact_type=Artifact.Type.FRAGMENT):
        if not (art.content or {}).get("qdrant_point_id"):
            embed_fragment.delay(str(art.id))
            gap3 += 1

    if gap1 or gap2 or gap3:
        logger.info("scan gaps — doc→texto: %d, texto→frag: %d, frag→embed: %d", gap1, gap2, gap3)

    return {"gap_extraction": gap1, "gap_fragmentation": gap2, "gap_embedding": gap3}
