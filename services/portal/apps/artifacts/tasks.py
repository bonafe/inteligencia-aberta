import email as email_lib
import logging
import os
import re
import uuid as uuid_lib
from email import policy as email_policy
from time import perf_counter

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from minio import Minio

from apps.events.context import correlacao_de_artefato, set_correlation_id, set_tenant_id
from apps.events.emit import emit, etapa

logger = logging.getLogger(__name__)


def correlacao_do_artefato(artifact_id) -> "uuid_lib.UUID":
    """A correlação da captura à qual este artefato pertence.

    Prefere o id que o orchestrator gerou no momento da captura (guardado em
    `Artifact.content["correlation_id"]`), para que a timeline comece no clique
    da extensão. Cai para um uuid5 derivado do id do artefato quando ele não
    existe — capturas anteriores a este log, artefatos criados pelo
    `sync_minio_postgres.py` — mantendo mesmo assim uma correlação estável
    entre reprocessamentos do mesmo artefato.
    """
    from .models import Artifact

    try:
        artefato = Artifact.objects.filter(id=artifact_id).only("content").first()
        declarada = (artefato.content or {}).get("correlation_id") if artefato else None
        if declarada:
            return uuid_lib.UUID(str(declarada))
    except (ValueError, TypeError, AttributeError):
        pass
    return correlacao_de_artefato(artifact_id)


def _uuid_ou_none(valor):
    try:
        return uuid_lib.UUID(str(valor))
    except (ValueError, TypeError, AttributeError):
        return None


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


# ── Etapa 1: extração de texto ────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def extract_text_from_mhtml(self, artifact_id: str, forcar: bool = False):
    """Extrai texto e dados estruturados de um MHTML capturado.

    Com `forcar=True`, apaga o DocumentText (e seus fragmentos) e refaz a
    extração do zero. É o caminho para diagnosticar uma captura antiga: o
    reprocessamento emite a mesma trilha de eventos de uma captura nova, e o
    painel passa a dizer em que etapa e por que o resultado saiu vazio.
    """
    from .models import Artifact, DocumentText

    correlation = correlacao_do_artefato(artifact_id)
    set_correlation_id(correlation)

    try:
        artifact = Artifact.objects.get(id=artifact_id)
    except Artifact.DoesNotExist:
        logger.warning("[%s] artefato não encontrado — extração abortada", artifact_id)
        emit("extracao.ignorada", "ignorado",
             subject_type="artifact", subject_id=_uuid_ou_none(artifact_id),
             message="artefato não encontrado no banco")
        return {"status": "error", "reason": f"Artifact {artifact_id} não encontrado"}

    tenant_id = artifact.tenant_id
    # A partir daqui todo evento deste processo — inclusive os automáticos dos
    # signals do Celery, ao despachar as tasks seguintes — herda o tenant.
    set_tenant_id(tenant_id)
    logger.info("[%s] extract_text_from_mhtml iniciado (forcar=%s)", artifact_id, forcar)

    content = artifact.content or {}
    mhtml_path = content.get("mhtml_path")
    mhtml_bucket = content.get("mhtml_bucket", "inteligencia-aberta-mhtml")
    url = content.get("url", "")
    title = content.get("title", "")

    def evento(stage, status, **kw):
        """Atalho: todo evento desta task fala do mesmo artefato e tenant."""
        kw.setdefault("subject_type", "artifact")
        kw.setdefault("subject_id", artifact.id)
        kw.setdefault("tenant_id", tenant_id)
        return emit(stage, status, **kw)

    evento("extracao.iniciada", "iniciado",
           message=f"extração iniciada para {url or artifact_id}",
           payload={"url": url, "titulo": title, "forcar": forcar,
                    "allow_external_llm": artifact.allow_external_llm})

    if not mhtml_path:
        logger.info("[%s] sem mhtml_path — ignorado", artifact_id)
        evento("extracao.ignorada", "ignorado", message="artefato sem mhtml_path no conteúdo")
        return {"status": "skipped", "reason": "sem mhtml_path no conteúdo"}

    # Idempotência: já tem DocumentText?
    existing = DocumentText.objects.filter(document=artifact).first()
    if existing and not forcar:
        logger.info("[%s] já processado — document_text_id=%s", artifact_id, existing.id)
        evento("extracao.ignorada", "ignorado",
               message="já existe DocumentText; use forcar=True para reextrair",
               payload={"document_text_id": str(existing.id),
                        "tem_dom_representation": bool(existing.dom_representation),
                        "tem_dados_dom2parser": existing.dados_estruturados_dom2parser is not None,
                        "tem_dados_extruct": existing.dados_estruturados_extruct is not None})
        fragment_text.delay(str(existing.id))
        return {"status": "already_done", "document_text_id": str(existing.id)}

    if existing and forcar:
        from .models import DocumentFragment
        n_frags = DocumentFragment.objects.filter(document_text=existing).count()
        DocumentFragment.objects.filter(document_text=existing).delete()
        existing.delete()
        logger.info("[%s] reprocessamento forçado — DocumentText e %d fragmentos apagados", artifact_id, n_frags)
        evento("extracao.reiniciada", "ok",
               message="DocumentText anterior descartado para reextração",
               payload={"fragmentos_apagados": n_frags})

    logger.info("[%s] buscando MHTML no MinIO — bucket=%s path=%s", artifact_id, mhtml_bucket, mhtml_path)
    try:
        with etapa("extracao.minio", subject_type="artifact", subject_id=artifact.id,
                   tenant_id=tenant_id) as e:
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
            e.ok(f"MHTML lido ({len(mhtml_bytes)} bytes)",
                 bytes=len(mhtml_bytes), bucket=mhtml_bucket, path=mhtml_path)
    except Exception as exc:
        logger.warning("[%s] falha ao buscar MHTML — tentativa %d: %s", artifact_id, self.request.retries + 1, exc)
        raise self.retry(exc=exc)

    logger.info("[%s] MHTML lido — %d bytes", artifact_id, len(mhtml_bytes))

    msg = email_lib.message_from_bytes(mhtml_bytes, policy=email_policy.default)
    html_content = None
    charset_usado = None
    for part in msg.walk():
        if part.get_content_type() == "text/html" and html_content is None:
            mime_charset = part.get_content_charset()
            payload = part.get_payload(decode=True)
            if payload:
                html_content = _decode_html_bytes(payload, mime_charset)
                charset_usado = mime_charset
                logger.debug("[%s] charset resolvido — mime=%s bytes=%d", artifact_id, mime_charset, len(payload))
                break

    if not html_content:
        logger.info("[%s] HTML não encontrado no MHTML — ignorado", artifact_id)
        evento("extracao.mhtml", "vazio", message="nenhuma parte text/html dentro do MHTML")
        return {"status": "skipped", "reason": "HTML não encontrado no MHTML"}

    logger.info("[%s] HTML extraído do MHTML — %d chars", artifact_id, len(html_content))
    evento("extracao.mhtml", "ok",
           message=f"HTML extraído do MHTML ({len(html_content)} chars)",
           payload={"chars_html": len(html_content), "charset": charset_usado or "indeterminado"})

    from .extractors import detect_page_type, route, extract_narrative_text

    # Texto de busca: SEMPRE via trafilatura, independente de page_type ou de LLM.
    # Roda antes de qualquer detecção/classificação — se a página não tem prosa
    # extraível, não vale a pena gastar chamadas de LLM tentando classificá-la.
    with etapa("extracao.trafilatura", subject_type="artifact", subject_id=artifact.id,
               tenant_id=tenant_id) as e:
        text = extract_narrative_text(html_content)
        if not text:
            e.vazio("trafilatura não encontrou prosa extraível nesta página")
        else:
            e.ok(f"texto extraído ({len(text)} chars)",
                 chars=len(text), palavras=len(text.split()))

    if not text:
        logger.info("[%s] trafilatura não produziu conteúdo — ignorado", artifact_id)
        evento("extracao.ignorada", "ignorado",
               message="sem texto extraível — o restante do pipeline não roda")
        return {"status": "skipped", "reason": "trafilatura não produziu conteúdo"}

    logger.info("[%s] texto extraído via trafilatura — %d chars", artifact_id, len(text))

    # dom2parser roda uma única vez: produz a representação compacta (persistida em
    # DocumentText.dom_representation e enviada ao LLM quando necessário) e o parser
    # verificado desta página (seletores medidos contra o próprio HTML, sem LLM).
    from .extractors import dom_parser

    dom_representation = None
    parser_spec = None
    diag_d2p: dict = {}
    try:
        with etapa("extracao.dom2parser", subject_type="artifact", subject_id=artifact.id,
                   tenant_id=tenant_id) as e:
            import dom2parser
            compressed = dom2parser.compress(html_content)
            dom_representation = compressed.text
            parser_spec = compressed.parser
            reduction_pct = (compressed.reduction or {}).get("reduction_pct", {})
            logger.info(
                "[%s] dom2parser — %.1f%% redução de chars (%d chars finais), %d registros verificados, %d falhas",
                artifact_id, reduction_pct.get("chars", 0.0), len(dom_representation),
                len(parser_spec.records), len(parser_spec.failures),
            )

            # O parser sintetizado é executado aqui mesmo; o resultado alimenta
            # tanto `dados_estruturados_dom2parser` quanto a cascata abaixo.
            dom2parser_extraction = dom_parser.extract_with_spec(
                parser_spec, html_content, diagnostico=diag_d2p
            )
            diag_d2p["reducao_pct_chars"] = round(reduction_pct.get("chars", 0.0), 2)
            diag_d2p["chars_finais"] = len(dom_representation)

            if dom2parser_extraction:
                e.ok("parser verificado extraiu registros", **diag_d2p)
            else:
                # A distinção que faltava: rodou, mas não produziu — e agora o
                # motivo fica gravado no banco em vez de morrer no stdout.
                e.vazio(diag_d2p.get("motivo", "parser não produziu registros"), **diag_d2p)
    except Exception:
        logger.exception("[%s] dom2parser falhou — representação não será persistida", artifact_id)
        dom2parser_extraction = None
        if dom_representation is None:
            parser_spec = None

    dados_estruturados_dom2parser = (
        dom2parser_extraction["structured_data"] if dom2parser_extraction else None
    )

    # Extração bruta de cada biblioteca de "dados estruturados de página web", persistida
    # à parte de `structured_data` (que carrega só a vencedora da cascata abaixo) — para
    # comparar cobertura/qualidade entre estratégias sem precisar reprocessar o MHTML.
    diag_extruct: dict = {}
    try:
        from .extractors import extruct_extractor
        with etapa("extracao.extruct", subject_type="artifact", subject_id=artifact.id,
                   tenant_id=tenant_id) as e:
            dados_estruturados_extruct = extruct_extractor.extract(
                html_content, url, diagnostico=diag_extruct
            )
            if dados_estruturados_extruct:
                e.ok("metadados embutidos encontrados", **diag_extruct)
            else:
                e.vazio(diag_extruct.get("motivo", "nenhum metadado embutido"), **diag_extruct)
    except Exception:
        logger.exception("[%s] extruct falhou — seguindo sem dados_estruturados_extruct", artifact_id)
        dados_estruturados_extruct = None

    logger.info("[%s] detectando tipo de página — url=%s allow_external_llm=%s", artifact_id, url, artifact.allow_external_llm)
    with etapa("deteccao.page_type", subject_type="artifact", subject_id=artifact.id,
               tenant_id=tenant_id) as e:
        page_type, confidence, detection_source, cache_id, cache_obj = detect_page_type(
            html_content, url, artifact.tenant_id,
            allow_external_llm=artifact.allow_external_llm,
            dom_representation=dom_representation,
            artifact_id=artifact.id,
        )
        e.ok(f"{page_type} ({detection_source}, confiança {confidence:.2f})",
             page_type=page_type, confidence=round(confidence, 3),
             detection_source=detection_source,
             cache_id=str(cache_id) if cache_id else None,
             # cache_obj None significa que URLPatternCache não pôde ser criado:
             # sem ele, o parser não é gravado e a Estratégia C nunca dispara.
             tem_cache=cache_obj is not None)
    logger.info(
        "[%s] tipo detectado — page_type=%s confidence=%.2f source=%s cache_id=%s",
        artifact_id, page_type, confidence, detection_source, cache_id,
    )

    # Ordem de extração de structured_data (o texto de busca já foi definido acima).
    # Sem cache: cada captura recalcula tudo do zero, porque o dado da página pode
    # ter mudado desde a última vez — um schema salvo só sabe extrair os campos que
    # existiam (com valor) na captura que o gerou, e ficaria preso a isso para sempre.
    #   1. parser verificado que o dom2parser sintetizou para ESTA página (sem LLM)
    #   2. LLM — toda vez que o dom2parser não encontrar estruturas repetidas
    #      (páginas de campos soltos: ficha de CNPJ), se allow_external_llm permitir
    #   3. extrator determinístico por page_type
    extracted = None
    # Trilha da cascata: por que cada estratégia foi (ou não) usada. Vira o
    # payload de `extracao.cascata` e responde "por que o dado veio daqui".
    cascata: list[dict] = []

    if dom2parser_extraction is not None:
        # Já calculado acima — reaproveita sem reexecutar.
        extracted = dom2parser_extraction
        registros = extracted["structured_data"]["registros"]
        logger.info(
            "[%s] parser dom2parser extraiu %d registros — %s",
            artifact_id, len(registros), {k: len(v) for k, v in registros.items()},
        )
        cascata.append({"estrategia": "dom2parser", "usada": True,
                        "registros": {k: len(v) for k, v in registros.items()}})
    else:
        logger.info("[%s] dom2parser não sintetizou parser utilizável para esta página", artifact_id)
        cascata.append({"estrategia": "dom2parser", "usada": False,
                        "motivo": diag_d2p.get("motivo", "sem parser utilizável")})

    # LLM: quando o dom2parser não achou estrutura repetida, o LLM entende a página
    # e extrai os dados estruturados diretamente. Roda em toda captura que precisar
    # dele — não só na primeira — porque não há mais schema para reaproveitar depois.
    if extracted is None and artifact.allow_external_llm:
        logger.info("[%s] extraindo dados estruturados via LLM", artifact_id)
        try:
            with etapa("extracao.llm", subject_type="artifact", subject_id=artifact.id,
                       tenant_id=tenant_id) as e:
                from .extractors.llm_classifier import llm_extract_and_schema

                skeleton = dom_representation
                if skeleton is None:
                    import dom2parser
                    skeleton = dom2parser.compress(html_content).text
                llm_result = llm_extract_and_schema(
                    skeleton, url, page_type_hint=page_type,
                    artifact_id=artifact.id, tenant_id=tenant_id,
                )

                if llm_result and llm_result.get("structured_data"):
                    extracted = {
                        "structured_data": llm_result["structured_data"],
                        "extractor_version": "llm_direct:1.0",
                    }
                    # Refine page_type if LLM disagrees with structural analysis
                    if llm_result.get("page_type") and llm_result["page_type"] != "desconhecido":
                        page_type = llm_result["page_type"]

                    e.ok("LLM extraiu dados estruturados",
                         modelo=llm_result.get("model", ""),
                         page_type=page_type,
                         chars_enviados=len(skeleton or ""))
                    cascata.append({"estrategia": "llm", "usada": True})
                else:
                    logger.warning("[%s] LLM não produziu dados estruturados — fallback para extrator determinístico", artifact_id)
                    e.vazio("LLM não produziu dados estruturados",
                            chars_enviados=len(skeleton or ""))
                    cascata.append({"estrategia": "llm", "usada": False,
                                    "motivo": "LLM não produziu dados estruturados"})
        except Exception:
            logger.exception("[%s] llm_extract_and_schema falhou — fallback para extrator determinístico", artifact_id)
            cascata.append({"estrategia": "llm", "usada": False, "motivo": "exceção na chamada"})
    elif extracted is None:
        cascata.append({
            "estrategia": "llm", "usada": False,
            "motivo": "LLM externo não permitido para esta classificação",
        })

    if extracted is None:
        logger.info("[%s] extraindo structured_data com extrator determinístico=%s", artifact_id, page_type)
        extracted = route(page_type, html_content, url, title)
        cascata.append({"estrategia": "deterministico", "usada": True,
                        "extractor_version": extracted["extractor_version"]})

    evento("extracao.cascata",
           "ok" if extracted.get("structured_data") else "vazio",
           message=f"structured_data via {extracted['extractor_version']}",
           payload={"extractor_version": extracted["extractor_version"],
                    "page_type": page_type, "trilha": cascata})

    logger.info(
        "[%s] extração concluída — chars=%d words=%d structured_data=%s extractor=%s "
        "dom2parser=%s extruct=%s",
        artifact_id, len(text), len(text.split()),
        "sim" if extracted.get("structured_data") else "não",
        extracted["extractor_version"],
        "sim" if dados_estruturados_dom2parser else "não",
        "sim" if dados_estruturados_extruct else "não",
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
        dados_estruturados_dom2parser=dados_estruturados_dom2parser,
        dados_estruturados_extruct=dados_estruturados_extruct,
        dom_representation=dom_representation,
        extractor_version=extracted["extractor_version"],
        char_count=len(text),
        word_count=len(text.split()),
    )

    evento("extracao.concluida", "ok",
           message=f"DocumentText criado ({doc_text.word_count} palavras)",
           payload={"document_text_id": str(doc_text.id),
                    "extractor_version": extracted["extractor_version"],
                    "page_type": page_type,
                    "palavras": doc_text.word_count,
                    "tem_structured_data": bool(extracted.get("structured_data")),
                    "tem_dom2parser": dados_estruturados_dom2parser is not None,
                    "tem_extruct": dados_estruturados_extruct is not None,
                    "tem_dom_representation": dom_representation is not None})

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
        doc_text = DocumentText.objects.select_related("document").get(id=document_text_id)
    except DocumentText.DoesNotExist:
        emit("fragmentacao.ignorada", "ignorado",
             subject_type="document_text", subject_id=_uuid_ou_none(document_text_id),
             message="DocumentText não encontrado")
        return {"status": "error", "reason": f"DocumentText {document_text_id} não encontrado"}

    set_correlation_id(correlacao_do_artefato(doc_text.document_id))
    tenant_id = doc_text.document.tenant_id
    set_tenant_id(tenant_id)
    logger.info("[%s] fragment_text iniciado", document_text_id)

    # Idempotência: já foi fragmentado?
    existing_ids = list(doc_text.fragments.values_list("id", flat=True))
    if existing_ids:
        logger.info("[%s] já fragmentado — %d fragmentos existentes", document_text_id, len(existing_ids))
        emit("fragmentacao.ignorada", "ignorado",
             subject_type="document_text", subject_id=doc_text.id, tenant_id=tenant_id,
             message=f"já havia {len(existing_ids)} fragmentos",
             payload={"n": len(existing_ids)})
        for frag_id in existing_ids:
            frag = DocumentFragment.objects.filter(id=frag_id).first()
            if frag and not frag.qdrant_point_id:
                embed_fragment.delay(str(frag_id))
        return {"status": "already_done", "fragments": len(existing_ids)}

    text = doc_text.text
    if not text:
        logger.info("[%s] sem texto no DocumentText — ignorado", document_text_id)
        emit("fragmentacao.ignorada", "ignorado",
             subject_type="document_text", subject_id=doc_text.id, tenant_id=tenant_id,
             message="DocumentText sem texto")
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
    emit("fragmentacao.concluida", "ok",
         subject_type="document_text", subject_id=doc_text.id, tenant_id=tenant_id,
         message=f"{len(chunks)} fragmentos criados",
         payload={"n": len(chunks), "chars": len(text),
                  "chunk_size": chunk_size, "overlap": overlap})
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
        emit("embedding.ignorado", "ignorado",
             subject_type="fragment", subject_id=_uuid_ou_none(fragment_id),
             message="fragmento não encontrado")
        return {"status": "error", "reason": f"DocumentFragment {fragment_id} não encontrado"}

    set_correlation_id(correlacao_do_artefato(fragment.document_text.document_id))
    set_tenant_id(fragment.document_text.document.tenant_id)
    logger.info("[%s] embed_fragment iniciado", fragment_id)

    if fragment.qdrant_point_id:
        logger.info("[%s] embedding já existe — qdrant_point_id=%s", fragment_id, fragment.qdrant_point_id)
        # Emite mesmo assim: o fato relevante para a projeção é "este fragmento
        # está indexado", não "foi esta execução que o indexou". Sem este evento
        # a trilha ficava presa em 8/9 quando uma task era despachada duas vezes.
        emit("embedding.concluido", "ok",
             subject_type="fragment", subject_id=fragment.id,
             tenant_id=fragment.document_text.document.tenant_id,
             message=f"fragmento {fragment.fragment_index + 1}/{fragment.total_fragments} já estava indexado",
             payload={"indice": fragment.fragment_index,
                      "total": fragment.total_fragments,
                      "ja_existia": True,
                      "collection": fragment.qdrant_collection,
                      "document_text_id": str(fragment.document_text_id)})
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
    emit("embedding.concluido", "ok",
         subject_type="fragment", subject_id=fragment.id, tenant_id=document.tenant_id,
         message=f"fragmento {fragment.fragment_index + 1}/{fragment.total_fragments} indexado",
         payload={"indice": fragment.fragment_index,
                  "total": fragment.total_fragments,
                  "collection": collection,
                  "dimensao": len(vector),
                  "document_text_id": str(fragment.document_text_id)})
    return {"status": "success", "qdrant_point_id": point_id, "collection": collection}


# ── Catch-up periódico (Celery Beat) ─────────────────────────────────────────

@shared_task
def scan_unprocessed_documents():
    """Varre gaps nos três estágios do pipeline e enfileira tarefas pendentes.

    A identificação dos gaps vive em `apps.events.agregados.contar_gaps` para
    que o painel exiba exatamente a mesma contagem que esta varredura usa para
    decidir o que reenfileirar — número divergente entre o que se vê e o que o
    sistema faz é pior do que não mostrar número nenhum.
    """
    from apps.events.agregados import contar_gaps

    gaps = contar_gaps()

    def despachar(itens, task):
        for alvo_id, artifact_id, tenant_id in itens:
            # Sem isto, os eventos de ciclo de vida das tasks redespachadas pelo
            # catch-up nasceriam sem correlação e sem tenant — ficariam fora da
            # linha do tempo da captura e invisíveis no painel.
            set_correlation_id(correlacao_do_artefato(artifact_id))
            set_tenant_id(tenant_id)
            task.delay(str(alvo_id))
        set_correlation_id(None)
        set_tenant_id(None)

    despachar(gaps["extracao"], extract_text_from_mhtml)
    despachar(gaps["fragmentacao"], fragment_text)
    despachar(gaps["embedding"], embed_fragment)

    gap1, gap2, gap3 = (len(gaps["extracao"]), len(gaps["fragmentacao"]), len(gaps["embedding"]))

    if gap1 or gap2 or gap3:
        logger.info("scan gaps — doc→texto: %d, texto→frag: %d, frag→embed: %d", gap1, gap2, gap3)
        # Só emite quando há algo a fazer: a varredura roda a cada 2 minutos e
        # um evento por ciclo ocioso afogaria a timeline em ruído.
        emit("catchup.varredura", "ok",
             source="beat",
             message=f"catch-up reenfileirou {gap1 + gap2 + gap3} tarefas",
             payload={"extracao": gap1, "fragmentacao": gap2, "embedding": gap3})

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


# ── Estruturação manual via LLM (Claude/Ollama) e comparação ─────────────────

def _gravar_se_nao_cancelado(modelo, id_, status_cancelado, **campos):
    """Grava campos só se o registro não foi cancelado nesse meio-tempo.

    Cancelamento manda SIGKILL no processo do worker (ver EstruturacaoCancelarView/
    ComparacaoCancelarView em views.py) —
    a task quase nunca chega a executar Python de novo depois disso (está bloqueada
    dentro da chamada HTTP quando o sinal chega), mas na corrida rara em que o
    resultado volta antes do sinal ser entregue, este guard evita sobrescrever
    o cancelamento que o usuário pediu.
    """
    return modelo.objects.filter(id=id_).exclude(status=status_cancelado).update(**campos)


@shared_task(bind=True, max_retries=1)
def estruturar_llm_manual(self, estruturacao_id: str):
    """Executa uma EstruturacaoLLM(status=pendente): chama o provider escolhido
    e grava o resultado.

    Estritamente aditiva — nunca sobrescreve DocumentText. Falha de LLM não é
    transiente como MinIO: em vez de `self.retry()`, captura e grava `falhou`,
    para o usuário ver o erro na hora em vez de esperar 3 tentativas. Não há
    timeout que aborte a chamada automaticamente — a interrupção é sempre
    manual (botão "Parar" na tela de execuções, que revoga esta task).
    """
    from .extractors.estruturacao_manual import estruturar_manual
    from .models import EstruturacaoLLM
    from .policy import permite_llm_externo

    try:
        execucao = EstruturacaoLLM.objects.select_related("document_text__document").get(id=estruturacao_id)
    except EstruturacaoLLM.DoesNotExist:
        logger.warning("[%s] EstruturacaoLLM não encontrada", estruturacao_id)
        return {"status": "error", "reason": "não encontrada"}

    artifact = execucao.document_text.document
    set_correlation_id(correlacao_do_artefato(artifact.id))
    set_tenant_id(execucao.tenant_id)

    def evento(stage, status, **kw):
        kw.setdefault("subject_type", "artifact")
        kw.setdefault("subject_id", artifact.id)
        kw.setdefault("tenant_id", execucao.tenant_id)
        kw.setdefault("payload", {}).setdefault("provider", execucao.provider)
        kw["payload"].setdefault("model_name", execucao.model_name)
        kw["payload"].setdefault("estruturacao_id", str(execucao.id))
        return emit(stage, status, **kw)

    CANCELADO = EstruturacaoLLM.Status.CANCELADO

    # Defesa em profundidade: a view já checa isto, mas esta task pode em tese
    # ser invocada fora dela (replay, shell) — nunca chama provider externo
    # para um artefato restrito/confidencial.
    if execucao.provider == EstruturacaoLLM.Provider.ANTHROPIC and not permite_llm_externo(artifact.classification_level):
        msg = f"LLM externo não permitido para classificação '{artifact.classification_level}'"
        _gravar_se_nao_cancelado(EstruturacaoLLM, execucao.id, CANCELADO,
                                  status=EstruturacaoLLM.Status.FALHOU, error_message=msg,
                                  updated_at=timezone.now())
        evento("estruturacao_llm.bloqueada", "ignorado", message=msg)
        return {"status": "blocked"}

    # A partir daqui a task está de fato rodando — a UI passa a mostrar "rodando
    # há Xs" em vez de "na fila", medido a partir de started_at.
    if not _gravar_se_nao_cancelado(EstruturacaoLLM, execucao.id, CANCELADO,
                                     status=EstruturacaoLLM.Status.EXECUTANDO,
                                     started_at=timezone.now(), updated_at=timezone.now()):
        logger.info("[%s] EstruturacaoLLM cancelada antes de iniciar", estruturacao_id)
        return {"status": "cancelled"}

    evento("estruturacao_llm.iniciada", "iniciado",
           message=f"estruturação manual iniciada — {execucao.provider}:{execucao.model_name}")

    doc_text = execucao.document_text
    url = doc_text.source_url or (artifact.content or {}).get("url", "")
    t0 = perf_counter()
    try:
        resultado = estruturar_manual(
            execucao.provider, execucao.model_name,
            doc_text.dom_representation or "", url, page_type_hint=doc_text.page_type,
            subject_id=artifact.id, tenant_id=execucao.tenant_id,
        )
    except Exception as exc:
        duration_ms = int((perf_counter() - t0) * 1000)
        _gravar_se_nao_cancelado(EstruturacaoLLM, execucao.id, CANCELADO,
                                  status=EstruturacaoLLM.Status.FALHOU, error_message=str(exc),
                                  duration_ms=duration_ms, updated_at=timezone.now())
        evento("estruturacao_llm.falhou", "falhou", message=str(exc), duration_ms=duration_ms)
        return {"status": "error", "reason": str(exc)}

    duration_ms = int((perf_counter() - t0) * 1000)
    categoria = resultado.get("categoria", "")
    structured_data = resultado.get("structured_data")
    maquina_id = resultado.get("maquina_id")

    if structured_data:
        gravou = _gravar_se_nao_cancelado(
            EstruturacaoLLM, execucao.id, CANCELADO,
            status=EstruturacaoLLM.Status.CONCLUIDO, categoria=categoria,
            structured_data=structured_data, duration_ms=duration_ms, updated_at=timezone.now(),
            maquina_id=maquina_id,
        )
        if gravou:
            evento("estruturacao_llm.concluida", "ok", message="estruturação concluída",
                   duration_ms=duration_ms, payload={"campos": len(structured_data)})
    else:
        gravou = _gravar_se_nao_cancelado(
            EstruturacaoLLM, execucao.id, CANCELADO,
            status=EstruturacaoLLM.Status.VAZIO, categoria=categoria,
            duration_ms=duration_ms, updated_at=timezone.now(),
            maquina_id=maquina_id,
        )
        if gravou:
            evento("estruturacao_llm.vazio", "vazio",
                   message="LLM respondeu mas não produziu dado estruturado utilizável",
                   duration_ms=duration_ms)

    return {"status": "done", "estruturacao_id": str(execucao.id)}


@shared_task(bind=True, max_retries=1)
def comparar_llm(self, comparacao_id: str):
    """Executa uma Comparacao(status=pendente): resolve as referências, monta o
    snapshot das seções e chama o LLM-juiz.

    O snapshot das seções fica embutido em `resultado` — a comparação sobrevive
    a um reprocessamento que apague o DocumentText de origem (extract_text_from_mhtml
    com forcar=True apaga e recria DocumentText, derrubando em cascata as
    EstruturacaoLLM referenciadas).
    """
    from .extractors.comparador import julgar_comparacao
    from .models import Comparacao, DocumentText, EstruturacaoLLM
    from .policy import permite_llm_externo

    try:
        comparacao = Comparacao.objects.select_related("artifact").get(id=comparacao_id)
    except Comparacao.DoesNotExist:
        logger.warning("[%s] Comparacao não encontrada", comparacao_id)
        return {"status": "error", "reason": "não encontrada"}

    artifact = comparacao.artifact
    set_correlation_id(correlacao_do_artefato(artifact.id))
    set_tenant_id(comparacao.tenant_id)

    def evento(stage, status, **kw):
        kw.setdefault("subject_type", "artifact")
        kw.setdefault("subject_id", artifact.id)
        kw.setdefault("tenant_id", comparacao.tenant_id)
        kw.setdefault("payload", {}).setdefault("comparacao_id", str(comparacao.id))
        return emit(stage, status, **kw)

    CANCELADO = Comparacao.Status.CANCELADO

    if comparacao.modelo_juiz_provider == EstruturacaoLLM.Provider.ANTHROPIC and not permite_llm_externo(artifact.classification_level):
        msg = f"LLM externo não permitido para classificação '{artifact.classification_level}'"
        _gravar_se_nao_cancelado(Comparacao, comparacao.id, CANCELADO,
                                  status=Comparacao.Status.FALHOU, error_message=msg)
        evento("comparacao.bloqueada", "ignorado", message=msg,
               payload={"modelo_juiz": comparacao.modelo_juiz_model_name})
        return {"status": "blocked"}

    if not _gravar_se_nao_cancelado(Comparacao, comparacao.id, CANCELADO,
                                     status=Comparacao.Status.EXECUTANDO, started_at=timezone.now()):
        logger.info("[%s] Comparacao cancelada antes de iniciar", comparacao_id)
        return {"status": "cancelled"}

    evento("comparacao.iniciada", "iniciado",
           message=f"comparação iniciada — juiz {comparacao.modelo_juiz_provider}:{comparacao.modelo_juiz_model_name}",
           payload={"secoes_solicitadas": len(comparacao.referencias)})

    try:
        doc_text = artifact.extracted_text
    except DocumentText.DoesNotExist:
        doc_text = None

    secoes = []
    for ref in comparacao.referencias:
        if ref.get("tipo") == "campo_legado":
            if not doc_text:
                continue
            dados = getattr(doc_text, ref.get("campo", ""), None)
            if dados is None:
                continue
            secoes.append({"label": ref.get("label") or ref["campo"], "origem": ref, "dados": dados})
        elif ref.get("tipo") == "estruturacao_llm":
            execucao = EstruturacaoLLM.objects.filter(
                id=ref.get("id"), status=EstruturacaoLLM.Status.CONCLUIDO
            ).first()
            if not execucao:
                continue
            secoes.append({
                "label": ref.get("label") or f"{execucao.provider}:{execucao.model_name}",
                "origem": ref, "dados": execucao.structured_data,
            })

    if len(secoes) < 2:
        msg = "menos de 2 seções válidas para comparar (referências ausentes ou incompletas)"
        _gravar_se_nao_cancelado(Comparacao, comparacao.id, CANCELADO,
                                  status=Comparacao.Status.FALHOU, error_message=msg)
        evento("comparacao.falhou", "falhou", message=msg)
        return {"status": "error", "reason": msg}

    t0 = perf_counter()
    try:
        veredito = julgar_comparacao(
            comparacao.modelo_juiz_provider, comparacao.modelo_juiz_model_name,
            [{"label": s["label"], "dados": s["dados"]} for s in secoes],
            subject_id=artifact.id, tenant_id=comparacao.tenant_id,
        )
    except Exception as exc:
        duration_ms = int((perf_counter() - t0) * 1000)
        _gravar_se_nao_cancelado(Comparacao, comparacao.id, CANCELADO,
                                  status=Comparacao.Status.FALHOU, error_message=str(exc),
                                  duration_ms=duration_ms)
        evento("comparacao.falhou", "falhou", message=str(exc), duration_ms=duration_ms)
        return {"status": "error", "reason": str(exc)}

    duration_ms = int((perf_counter() - t0) * 1000)
    # maquina_id vira coluna própria (Comparacao.maquina) — não fica duplicado
    # dentro do JSON de resultado.
    maquina_id = veredito.pop("maquina_id", None)
    gravou = _gravar_se_nao_cancelado(
        Comparacao, comparacao.id, CANCELADO,
        status=Comparacao.Status.CONCLUIDO, resultado={"veredito": veredito, "secoes": secoes},
        duration_ms=duration_ms, maquina_id=maquina_id,
    )
    if gravou:
        evento("comparacao.concluida", "ok", message="comparação concluída",
               duration_ms=duration_ms, payload={"secoes": len(secoes)})
    return {"status": "done", "comparacao_id": str(comparacao.id)}
