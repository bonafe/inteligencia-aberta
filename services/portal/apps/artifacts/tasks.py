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
    from .models import Artifact, DocumentText

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

    # Idempotência: já tem DocumentText?
    existing = DocumentText.objects.filter(document=artifact).first()
    if existing:
        logger.info("[%s] já processado — document_text_id=%s", artifact_id, existing.id)
        fragment_text.delay(str(existing.id))
        return {"status": "already_done", "document_text_id": str(existing.id)}

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

    doc_text = DocumentText.objects.create(
        document=artifact,
        text=text,
        title=title,
        source_url=url,
        page_type=page_type,
        detection_confidence=confidence,
        detection_source=detection_source,
        url_pattern_cache_id=cache_id,
        structured_data=extracted.get("structured_data"),
        extractor_version=extracted["extractor_version"],
        char_count=len(text),
        word_count=len(text.split()),
    )

    logger.info("[%s] DocumentText criado — id=%s → despachando fragment_text", artifact_id, doc_text.id)
    fragment_text.delay(str(doc_text.id))

    return {
        "status": "success",
        "document_text_id": str(doc_text.id),
        "word_count": doc_text.word_count,
    }


# ── Etapa 2: fragmentação ─────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def fragment_text(self, document_text_id: str):
    from .models import DocumentText, DocumentFragment

    try:
        doc_text = DocumentText.objects.get(id=document_text_id)
    except DocumentText.DoesNotExist:
        return {"status": "error", "reason": f"DocumentText {document_text_id} não encontrado"}

    logger.info("[%s] fragment_text iniciado", document_text_id)

    # Idempotência: já foi fragmentado?
    existing_ids = list(doc_text.fragments.values_list("id", flat=True))
    if existing_ids:
        logger.info("[%s] já fragmentado — %d fragmentos existentes", document_text_id, len(existing_ids))
        for frag_id in existing_ids:
            frag = DocumentFragment.objects.filter(id=frag_id).first()
            if frag and not frag.qdrant_point_id:
                embed_fragment.delay(str(frag_id))
        return {"status": "already_done", "fragments": len(existing_ids)}

    text = doc_text.text
    if not text:
        logger.info("[%s] sem texto no DocumentText — ignorado", document_text_id)
        return {"status": "skipped", "reason": "sem texto no conteúdo"}

    chunk_size = getattr(settings, "FRAGMENT_CHUNK_SIZE", 1000)
    overlap = getattr(settings, "FRAGMENT_OVERLAP", 100)
    chunks = _split_text(text, chunk_size=chunk_size, overlap=overlap)

    logger.info(
        "[%s] fragmentando — %d chars → %d fragmentos (chunk=%d overlap=%d)",
        document_text_id, len(text), len(chunks), chunk_size, overlap,
    )

    for i, chunk in enumerate(chunks):
        frag = DocumentFragment.objects.create(
            document_text=doc_text,
            text=chunk,
            fragment_index=i,
            total_fragments=len(chunks),
        )
        embed_fragment.delay(str(frag.id))

    logger.info("[%s] %d fragmentos criados e despachados para embed_fragment", document_text_id, len(chunks))
    return {"status": "success", "fragments": len(chunks)}


# ── Etapa 3: embedding ────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def embed_fragment(self, fragment_id: str):
    from .models import DocumentFragment
    from .embeddings import ensure_collection, get_embedding_model, get_qdrant_client

    try:
        fragment = DocumentFragment.objects.select_related(
            "document_text__document"
        ).get(id=fragment_id)
    except DocumentFragment.DoesNotExist:
        return {"status": "error", "reason": f"DocumentFragment {fragment_id} não encontrado"}

    logger.info("[%s] embed_fragment iniciado", fragment_id)

    if fragment.qdrant_point_id:
        logger.info("[%s] embedding já existe — qdrant_point_id=%s", fragment_id, fragment.qdrant_point_id)
        return {"status": "already_done", "qdrant_point_id": fragment.qdrant_point_id}

    text = fragment.text
    if not text:
        logger.info("[%s] sem texto no fragmento — ignorado", fragment_id)
        return {"status": "skipped", "reason": "sem texto no fragmento"}

    document = fragment.document_text.document

    logger.info(
        "[%s] gerando embedding — fragmento %d/%d (%d chars): %s…",
        fragment_id,
        fragment.fragment_index + 1,
        fragment.total_fragments,
        len(text),
        text[:60].replace("\n", " "),
    )
    try:
        model = get_embedding_model()
        vector = list(model.embed([text]))[0].tolist()
    except Exception as exc:
        logger.warning("[%s] falha ao gerar embedding — tentativa %d: %s", fragment_id, self.request.retries + 1, exc)
        raise self.retry(exc=exc)

    logger.info("[%s] embedding gerado — dim=%d", fragment_id, len(vector))

    payload = {
        "fragment_id": str(fragment.id),
        "document_text_id": str(fragment.document_text_id),
        "document_artifact_id": str(document.id),
        "tenant_id": str(document.tenant_id),
        "fragment_index": fragment.fragment_index,
        "classification_level": document.classification_level,
        "source_url": fragment.document_text.source_url,
        "title": fragment.document_text.title,
        "text_preview": text[:200],
        "created_at": fragment.created_at.isoformat(),
    }

    try:
        qdrant = get_qdrant_client()
        collection = ensure_collection(qdrant, str(document.tenant_id), len(vector))
        point_id = str(uuid_lib.uuid4())

        logger.info("[%s] upsert no Qdrant — collection=%s point_id=%s", fragment_id, collection, point_id)
        from qdrant_client.models import PointStruct
        qdrant.upsert(
            collection_name=collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
    except Exception as exc:
        logger.warning("[%s] falha no Qdrant — tentativa %d: %s", fragment_id, self.request.retries + 1, exc)
        raise self.retry(exc=exc)

    fragment.qdrant_point_id = point_id
    fragment.qdrant_collection = collection
    fragment.save(update_fields=["qdrant_point_id", "qdrant_collection", "updated_at"])

    logger.info("[%s] embed_fragment concluído — point_id=%s collection=%s", fragment_id, point_id, collection)
    return {"status": "success", "qdrant_point_id": point_id, "collection": collection}


# ── Catch-up periódico (Celery Beat) ─────────────────────────────────────────

@shared_task
def scan_unprocessed_documents():
    """Varre gaps nos três estágios do pipeline e enfileira tarefas pendentes."""
    from .models import Artifact, DocumentText, DocumentFragment

    # Gap 1: documento sem DocumentText
    processed_doc_ids = DocumentText.objects.values_list("document_id", flat=True)
    gap1 = 0
    for art in Artifact.objects.filter(artifact_type=Artifact.Type.DOCUMENT).exclude(id__in=processed_doc_ids):
        if (art.content or {}).get("mhtml_path"):
            extract_text_from_mhtml.delay(str(art.id))
            gap1 += 1

    # Gap 2: DocumentText sem fragmentos
    fragmented_ids = DocumentFragment.objects.values_list("document_text_id", flat=True).distinct()
    gap2 = 0
    for dt in DocumentText.objects.exclude(id__in=fragmented_ids):
        fragment_text.delay(str(dt.id))
        gap2 += 1

    # Gap 3: fragmento sem embedding no Qdrant
    gap3 = 0
    for frag in DocumentFragment.objects.filter(qdrant_point_id=""):
        embed_fragment.delay(str(frag.id))
        gap3 += 1

    if gap1 or gap2 or gap3:
        logger.info("scan gaps — doc→texto: %d, texto→frag: %d, frag→embed: %d", gap1, gap2, gap3)

    return {"gap_extraction": gap1, "gap_fragmentation": gap2, "gap_embedding": gap3}
