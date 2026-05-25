import email as email_lib
import logging
import os
import uuid as uuid_lib
from email import policy as email_policy

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

    logger.info("[%s] extract_text_from_mhtml iniciado", artifact_id)

    content = artifact.content or {}
    mhtml_path = content.get("mhtml_path")
    mhtml_bucket = content.get("mhtml_bucket", "inteligencia-aberta-mhtml")

    if not mhtml_path:
        logger.info("[%s] sem mhtml_path — ignorado", artifact_id)
        return {"status": "skipped", "reason": "sem mhtml_path no conteúdo"}

    # Idempotência: já tem filho texto?
    existing_child = ArtifactLineage.objects.filter(
        parent=artifact, transformation="text_extraction"
    ).first()
    if existing_child:
        logger.info("[%s] já processado — child_id=%s", artifact_id, existing_child.child_id)
        fragment_text.delay(str(existing_child.child_id))
        return {"status": "already_done", "child_artifact_id": str(existing_child.child_id)}

    logger.info("[%s] buscando MHTML no MinIO — bucket=%s path=%s", artifact_id, mhtml_bucket, mhtml_path)
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
        logger.warning("[%s] falha ao buscar MHTML — tentativa %d: %s", artifact_id, self.request.retries + 1, exc)
        raise self.retry(exc=exc)

    logger.info("[%s] MHTML lido — %d bytes", artifact_id, len(mhtml_bytes))

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
        logger.info("[%s] HTML não encontrado no MHTML — ignorado", artifact_id)
        return {"status": "skipped", "reason": "HTML não encontrado no MHTML"}

    logger.info("[%s] HTML extraído do MHTML — %d chars", artifact_id, len(html_content))

    from .extractors import detect_page_type, route

    url = content.get("url", "")
    title = content.get("title", "")

    logger.info("[%s] detectando tipo de página — url=%s", artifact_id, url)
    page_type, confidence, detection_source, cache_id = detect_page_type(
        html_content, url, artifact.tenant_id
    )
    logger.info(
        "[%s] tipo detectado — page_type=%s confidence=%.2f source=%s cache_id=%s",
        artifact_id, page_type, confidence, detection_source, cache_id,
    )

    logger.info("[%s] extraindo conteúdo com extrator=%s", artifact_id, page_type)
    extracted = route(page_type, html_content, url, title)
    text = extracted["text"]

    if not text:
        logger.info("[%s] extrator não produziu conteúdo — ignorado", artifact_id)
        return {"status": "skipped", "reason": "extrator não produziu conteúdo"}

    logger.info(
        "[%s] extração concluída — chars=%d words=%d structured_data=%s extractor=%s",
        artifact_id, len(text), len(text.split()),
        "sim" if extracted.get("structured_data") else "não",
        extracted["extractor_version"],
    )

    child = Artifact.objects.create(
        artifact_type=Artifact.Type.TEXT,
        content={
            "text": text,
            "title": title,
            "source_url": url,
            "page_type": page_type,
            "detection_confidence": confidence,
            "detection_source": detection_source,
            "url_pattern_cache_id": cache_id,
            "structured_data": extracted.get("structured_data"),
            "extractor_version": extracted["extractor_version"],
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
        processor=f"extractor:{page_type}:{extracted['extractor_version'].split(':')[-1]}",
        parameters={"page_type": page_type, "detection_source": detection_source},
    )

    logger.info("[%s] artefato TEXT criado — child_id=%s → despachando fragment_text", artifact_id, child.id)
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

    logger.info("[%s] fragment_text iniciado", texto_artifact_id)

    # Idempotência: já foi fragmentado?
    existing_ids = list(
        ArtifactLineage.objects.filter(
            parent=artifact, transformation="fragmentation"
        ).values_list("child_id", flat=True)
    )
    if existing_ids:
        logger.info("[%s] já fragmentado — %d fragmentos existentes", texto_artifact_id, len(existing_ids))
        for frag_id in existing_ids:
            frag = Artifact.objects.filter(id=frag_id).first()
            if frag and not (frag.content or {}).get("qdrant_point_id"):
                embed_fragment.delay(str(frag_id))
        return {"status": "already_done", "fragments": len(existing_ids)}

    text = (artifact.content or {}).get("text", "")
    if not text:
        logger.info("[%s] sem texto no artefato — ignorado", texto_artifact_id)
        return {"status": "skipped", "reason": "sem texto no conteúdo"}

    chunk_size = getattr(settings, "FRAGMENT_CHUNK_SIZE", 1000)
    overlap = getattr(settings, "FRAGMENT_OVERLAP", 100)
    chunks = _split_text(text, chunk_size=chunk_size, overlap=overlap)

    logger.info(
        "[%s] fragmentando — %d chars → %d fragmentos (chunk=%d overlap=%d)",
        texto_artifact_id, len(text), len(chunks), chunk_size, overlap,
    )

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

    logger.info("[%s] %d fragmentos criados e despachados para embed_fragment", texto_artifact_id, len(chunks))
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

    logger.info("[%s] embed_fragment iniciado", fragmento_artifact_id)

    content = artifact.content or {}

    if content.get("qdrant_point_id"):
        logger.info("[%s] embedding já existe — qdrant_point_id=%s", fragmento_artifact_id, content["qdrant_point_id"])
        return {"status": "already_done", "qdrant_point_id": content["qdrant_point_id"]}

    text = content.get("text", "")
    if not text:
        logger.info("[%s] sem texto no fragmento — ignorado", fragmento_artifact_id)
        return {"status": "skipped", "reason": "sem texto no fragmento"}

    logger.info(
        "[%s] gerando embedding — fragmento %d/%d (%d chars): %s…",
        fragmento_artifact_id,
        content.get("fragment_index", 0) + 1,
        content.get("total_fragments", "?"),
        len(text),
        text[:60].replace("\n", " "),
    )
    try:
        model = get_embedding_model()
        vector = list(model.embed([text]))[0].tolist()
    except Exception as exc:
        logger.warning("[%s] falha ao gerar embedding — tentativa %d: %s", fragmento_artifact_id, self.request.retries + 1, exc)
        raise self.retry(exc=exc)

    logger.info("[%s] embedding gerado — dim=%d", fragmento_artifact_id, len(vector))

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

        logger.info("[%s] upsert no Qdrant — collection=%s point_id=%s", fragmento_artifact_id, collection, point_id)
        from qdrant_client.models import PointStruct
        qdrant.upsert(
            collection_name=collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
    except Exception as exc:
        logger.warning("[%s] falha no Qdrant — tentativa %d: %s", fragmento_artifact_id, self.request.retries + 1, exc)
        raise self.retry(exc=exc)

    content["qdrant_point_id"] = point_id
    content["qdrant_collection"] = collection
    artifact.content = content
    artifact.save(update_fields=["content", "updated_at"])

    logger.info("[%s] embed_fragment concluído — point_id=%s collection=%s", fragmento_artifact_id, point_id, collection)
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
