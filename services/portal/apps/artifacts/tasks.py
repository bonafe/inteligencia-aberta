import email as email_lib
import logging
import os
import re
import uuid as uuid_lib
from email import policy as email_policy

from celery import shared_task
from django.conf import settings
from minio import Minio

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

_META_CHARSET_RE = re.compile(
    rb'<meta[^>]+charset=["\']?([a-zA-Z0-9_\-]+)',
    re.IGNORECASE,
)
_META_CONTENT_TYPE_RE = re.compile(
    rb'<meta[^>]+content=["\'][^"\']*charset=([a-zA-Z0-9_\-]+)',
    re.IGNORECASE,
)


def _decode_html_bytes(payload: bytes, mime_charset: str | None) -> str:
    """Decode HTML bytes using a cascade of strategies to handle any encoding."""
    candidates: list[str] = []

    if mime_charset:
        candidates.append(mime_charset)

    # Scan raw bytes for <meta charset> before full decode
    for pattern in (_META_CHARSET_RE, _META_CONTENT_TYPE_RE):
        m = pattern.search(payload[:4096])
        if m:
            detected = m.group(1).decode("ascii", errors="ignore")
            if detected.lower() not in [c.lower() for c in candidates]:
                candidates.append(detected)
            break

    for enc in candidates:
        try:
            return payload.decode(enc)
        except (UnicodeDecodeError, LookupError):
            logger.debug("charset %s falhou, tentando próximo", enc)

    # Explicit Western European fallbacks — covers all legacy Brazilian Portuguese sites.
    # cp1252 is a superset of latin-1; all common PT chars (ã ç õ á é etc.) are identical
    # in both, so this is safe even when the actual encoding is iso-8859-1.
    # We skip charset-normalizer here because it confuses cp1252/cp1250/cp1251 siblings,
    # producing ă instead of ã for Portuguese text.
    for enc in ("utf-8", "cp1252", "iso-8859-1"):
        if enc not in [c.lower() for c in candidates]:
            try:
                return payload.decode(enc)
            except (UnicodeDecodeError, LookupError):
                pass

    # latin-1 decodes any byte sequence without error
    logger.warning("charset indeterminado — usando latin-1 com replace")
    return payload.decode("latin-1", errors="replace")


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


# ── Saúde do schema de seletores (URLPatternCache) ───────────────────────────

SCHEMA_FAILURE_THRESHOLD = 2


def _schema_reproduces_data(schema: dict, html: str, url: str, title: str, artifact_id) -> bool:
    """Valida um schema recém-gerado pelo LLM contra o próprio HTML da captura.

    Se os seletores não extraem nada agora, não vão extrair nas capturas
    seguintes — melhor não gravar e deixar a próxima captura regenerar.
    """
    try:
        from .extractors.schema_extractor import schema_driven_extract
        result = schema_driven_extract(html, url, title, schema)
        return result.get("extractor_version", "").startswith("schema_driven:")
    except Exception:
        logger.exception("[%s] validação de schema falhou — schema não será gravado", artifact_id)
        return False


def _update_schema_health(cache_obj, extracted: dict, artifact_id) -> None:
    """Realimenta o URLPatternCache com o resultado real do schema de seletores.

    O extractor_version do resultado é o sinal: se não começa com
    "schema_driven:", o schema_driven_extract caiu no fallback — os seletores
    não casaram com o HTML. Após SCHEMA_FAILURE_THRESHOLD falhas consecutivas,
    o schema é descartado e a entrada marcada para revisão; a captura seguinte
    (com LLM habilitado) regenera o schema pagando o custo uma única vez.
    Um sucesso zera o contador.
    """
    from django.db import models as django_models
    from .models import URLPatternCache

    schema_worked = extracted.get("extractor_version", "").startswith("schema_driven:")

    if schema_worked:
        if cache_obj.schema_failure_count:
            URLPatternCache.objects.filter(id=cache_obj.id).update(schema_failure_count=0)
        return

    URLPatternCache.objects.filter(id=cache_obj.id).update(
        schema_failure_count=django_models.F("schema_failure_count") + 1
    )
    cache_obj.refresh_from_db(fields=["schema_failure_count"])
    logger.warning(
        "[%s] schema não extraiu dados — schema_failure_count=%d (%s%s)",
        artifact_id, cache_obj.schema_failure_count,
        cache_obj.domain, cache_obj.path_pattern,
    )

    if cache_obj.schema_failure_count >= SCHEMA_FAILURE_THRESHOLD:
        URLPatternCache.objects.filter(id=cache_obj.id).update(
            extractor_config={},
            needs_review=True,
        )
        logger.warning(
            "[%s] schema invalidado após %d falhas consecutivas — needs_review=True, "
            "será regenerado na próxima captura com LLM",
            artifact_id, cache_obj.schema_failure_count,
        )


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
            mime_charset = part.get_content_charset()
            payload = part.get_payload(decode=True)
            if payload:
                html_content = _decode_html_bytes(payload, mime_charset)
                logger.debug("[%s] charset resolvido — mime=%s bytes=%d", artifact_id, mime_charset, len(payload))
                break

    if not html_content:
        logger.info("[%s] HTML não encontrado no MHTML — ignorado", artifact_id)
        return {"status": "skipped", "reason": "HTML não encontrado no MHTML"}

    logger.info("[%s] HTML extraído do MHTML — %d chars", artifact_id, len(html_content))

    from .extractors import detect_page_type, route, extract_narrative_text

    # Texto de busca: SEMPRE via trafilatura, independente de page_type ou de LLM.
    # Roda antes de qualquer detecção/classificação — se a página não tem prosa
    # extraível, não vale a pena gastar chamadas de LLM tentando classificá-la.
    text = extract_narrative_text(html_content)

    if not text:
        logger.info("[%s] trafilatura não produziu conteúdo — ignorado", artifact_id)
        return {"status": "skipped", "reason": "trafilatura não produziu conteúdo"}

    logger.info("[%s] texto extraído via trafilatura — %d chars", artifact_id, len(text))

    url = content.get("url", "")
    title = content.get("title", "")

    logger.info("[%s] detectando tipo de página — url=%s allow_external_llm=%s", artifact_id, url, artifact.allow_external_llm)
    page_type, confidence, detection_source, cache_id, cache_obj = detect_page_type(
        html_content, url, artifact.tenant_id,
        allow_external_llm=artifact.allow_external_llm,
    )
    logger.info(
        "[%s] tipo detectado — page_type=%s confidence=%.2f source=%s cache_id=%s",
        artifact_id, page_type, confidence, detection_source, cache_id,
    )

    # Estratégia A+B+C unificada: na primeira captura com LLM habilitado, o LLM faz tudo —
    # entende a página, extrai os dados estruturados e gera o schema — em vez de usar o
    # extrator determinístico. O LLM nunca produz o texto de busca (já extraído acima);
    # sua responsabilidade é só structured_data + schema. Resultado usado imediatamente
    # (não só na próxima captura). Fallback para route() se o LLM falhar, não estiver
    # disponível, ou não encontrar nenhum dado estruturado.
    first_capture_with_llm = (
        artifact.allow_external_llm
        and cache_obj is not None
        and not cache_obj.extractor_config
    )

    extracted = None

    if first_capture_with_llm:
        logger.info("[%s] primeira captura com LLM — extraindo dados e gerando schema", artifact_id)
        try:
            from .extractors.skeleton import compress_html_skeleton
            from .extractors.llm_classifier import llm_extract_and_schema
            from .models import URLPatternCache

            skeleton = compress_html_skeleton(html_content)
            llm_result = llm_extract_and_schema(skeleton, url, page_type_hint=page_type)

            if llm_result and llm_result.get("structured_data"):
                extracted = {
                    "structured_data": llm_result["structured_data"],
                    "extractor_version": "llm_direct:1.0",
                }
                # Refine page_type if LLM disagrees with structural analysis
                if llm_result.get("page_type") and llm_result["page_type"] != "desconhecido":
                    page_type = llm_result["page_type"]

                schema = llm_result.get("schema") or {}
                if schema and _schema_reproduces_data(schema, html_content, url, title, artifact_id):
                    URLPatternCache.objects.filter(id=cache_obj.id).update(
                        extractor_config=schema,
                        page_type=page_type,
                        schema_failure_count=0,
                        needs_review=False,
                    )
                    logger.info(
                        "[%s] schema validado e gravado — categoria='%s' campos=%d tabelas=%d",
                        artifact_id, schema.get("categoria", ""),
                        len(schema.get("fields", {})), len(schema.get("tables", [])),
                    )
                elif schema:
                    logger.warning(
                        "[%s] schema gerado pelo LLM não reproduz dados no próprio HTML — "
                        "não gravado; esta captura usa o structured_data direto do LLM",
                        artifact_id,
                    )
            else:
                logger.warning("[%s] LLM não produziu dados estruturados — fallback para extrator determinístico", artifact_id)
        except Exception:
            logger.exception("[%s] llm_extract_and_schema falhou — fallback para extrator determinístico", artifact_id)

    if extracted is None:
        logger.info("[%s] extraindo structured_data com extrator=%s", artifact_id, page_type)
        extracted = route(page_type, html_content, url, title, cache_obj=cache_obj)

        # Realimentação do cache: se havia schema mas o resultado não veio dele,
        # os seletores não casaram — a estrutura da página provavelmente mudou.
        if cache_obj is not None and cache_obj.extractor_config:
            _update_schema_health(cache_obj, extracted, artifact_id)

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


# ── Reprocessamento de artefatos com encoding corrompido ──────────────────────

@shared_task
def reprocess_garbled_documents():
    """Apaga DocumentText/fragmentos com caracteres de substituição e reextrai."""
    from .models import DocumentText, DocumentFragment

    garbled = DocumentText.objects.filter(text__contains="�")
    count = garbled.count()
    if not count:
        logger.info("reprocess_garbled_documents: nenhum documento corrompido encontrado")
        return {"requeued": 0}

    artifact_ids = list(garbled.values_list("document_id", flat=True))

    # Apaga fragmentos e DocumentText — deixa o catch-up reimportar
    DocumentFragment.objects.filter(document_text__in=garbled).delete()
    garbled.delete()

    for art_id in artifact_ids:
        extract_text_from_mhtml.delay(str(art_id))

    logger.info("reprocess_garbled_documents: %d artefatos reenfileirados", count)
    return {"requeued": count}
