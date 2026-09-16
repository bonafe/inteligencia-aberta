import base64
import email
import json
import logging
import os
from email import policy

from django.conf import settings
from django.http import HttpResponse, Http404, JsonResponse

logger = logging.getLogger(__name__)
from django.shortcuts import render, get_object_or_404
from django.utils.crypto import constant_time_compare
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from minio import Minio
from minio.error import S3Error

from apps.accounts.models import Membership, Organization, User
from apps.accounts.views import orgs_do_usuario
from apps.events.context import set_correlation_id, set_tenant_id
from apps.events.emit import emit
from .graph import artifacts_para_mapa, montar_grafo
from .models import Artifact, Comparacao, DocumentText, EstruturacaoLLM
from .policy import permite_llm_externo

@method_decorator(csrf_exempt, name="dispatch")
class ArtefatoCreateAPIView(View):
    """Endpoint interno para criação de artefatos via ORM (dispara signal → pipeline)."""

    def post(self, request):
        # Canal serviço-a-serviço: só o orchestrator (portador do INTERNAL_API_TOKEN)
        # pode criar artefatos por aqui. constant_time_compare evita timing attack.
        expected = getattr(settings, "INTERNAL_API_TOKEN", "")
        provided = request.headers.get("X-Internal-Token", "")
        if not expected or not constant_time_compare(provided, expected):
            logger.warning("artefato recusado — X-Internal-Token ausente ou inválido")
            return JsonResponse({"error": "Não autorizado"}, status=403)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "JSON inválido"}, status=400)

        content = data.get("content", {})
        logger.info(
            "artefato recebido via API — tipo=%s url=%s user_id=%s tenant_id=%s",
            data.get("artifact_type"), content.get("url", ""),
            data.get("user_id"), data.get("tenant_id"),
        )

        # user_id/tenant_id agora vêm de um JWT já validado pelo orchestrator —
        # são obrigatórios e devem ser consistentes (o usuário pertence ao tenant).
        # Sem fallback "primeiro usuário": ele mascarava erros e permitia escrita
        # em tenant arbitrário.
        user_id = data.get("user_id")
        tenant_id = data.get("tenant_id")
        if not user_id or not tenant_id:
            return JsonResponse({"error": "user_id e tenant_id são obrigatórios"}, status=400)

        user = User.objects.filter(id=user_id).first()
        if user is None:
            return JsonResponse({"error": "Usuário não encontrado"}, status=400)

        org = Organization.objects.filter(id=tenant_id).first()
        if org is None:
            return JsonResponse({"error": "Organização não encontrada"}, status=400)

        if not Membership.objects.filter(user=user, organization=org).exists():
            logger.warning("artefato recusado — usuário %s não pertence ao tenant %s", user_id, tenant_id)
            return JsonResponse({"error": "Usuário não pertence à organização"}, status=403)

        logger.info("usuário/organização validados — user=%s org=%s", user.id, org.id)

        # Validate allow_external_llm against classification level (mirrors policy_engine logic)
        requested_llm = bool(data.get("allow_external_llm", False))
        level = data.get("classification_level", Artifact.ClassificationLevel.RESTRICTED)
        allow_llm = requested_llm and level not in (
            Artifact.ClassificationLevel.RESTRICTED,
            Artifact.ClassificationLevel.CONFIDENTIAL,
        )

        # A correlação nasce no orchestrator (ou na extensão) e é guardada no
        # próprio artefato, para que a timeline da captura comece no clique do
        # usuário e não na criação do registro. O signal post_save e as tasks
        # subsequentes a recuperam daqui.
        correlation_id = data.get("correlation_id")
        if correlation_id:
            content = {**content, "correlation_id": str(correlation_id)}

        artifact = Artifact.objects.create(
            artifact_type=data.get("artifact_type", Artifact.Type.DOCUMENT),
            content=content,
            classification_level=level,
            tenant=org,
            allow_external_llm=allow_llm,
            classified_by=user,
            info_type=data.get("info_type", Artifact.InfoType.FACT),
            sources=data.get("sources", []),
        )
        logger.info(
            "artefato criado — id=%s tipo=%s classificacao=%s allow_external_llm=%s",
            artifact.id, artifact.artifact_type, artifact.classification_level, artifact.allow_external_llm,
        )

        return JsonResponse({"artifact_id": str(artifact.id)}, status=201)


@method_decorator(login_required, name="dispatch")
class BuscaSemanticaView(View):
    def get(self, request):
        return render(request, "artifacts/busca.html", {"results": None, "query": ""})

    def post(self, request):
        query = request.POST.get("query", "").strip()
        if not query:
            return render(request, "artifacts/busca.html", {"results": [], "query": query})
        results = self._search(query, request.user)
        return render(request, "artifacts/busca.html", {"results": results, "query": query})

    def _search(self, query: str, user):
        from apps.accounts.models import Membership
        from .embeddings import get_embedding_model, get_qdrant_client
        from .models import Artifact

        membership = Membership.objects.filter(user=user).select_related("organization").first()
        if not membership:
            return []

        tenant_id = str(membership.organization.id)
        collection = f"ia_{tenant_id.replace('-', '')}"

        try:
            model = get_embedding_model()
            vector = list(model.embed([query]))[0].tolist()
            qdrant = get_qdrant_client()
            existing = {c.name for c in qdrant.get_collections().collections}
            if collection not in existing:
                return []
            hits = qdrant.search(
                collection_name=collection,
                query_vector=vector,
                limit=10,
                with_payload=True,
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Erro na busca semântica: %s", exc)
            return []

        results = []
        for hit in hits:
            p = hit.payload or {}
            artifact = Artifact.objects.filter(id=p.get("artifact_id")).first()
            full_text = (artifact.content or {}).get("text", "") if artifact else ""
            results.append({
                "score": round(hit.score * 100),
                "title": p.get("title") or "Sem título",
                "source_url": p.get("source_url", ""),
                "fragment_index": p.get("fragment_index", 0),
                "text_preview": p.get("text_preview", full_text[:200]),
                "full_text": full_text,
                "source_artifact_id": p.get("source_artifact_id"),
            })
        return results


class ArtifactGalleryView(View):
    def get(self, request):
        # Isolamento de tenant: só documentos das organizações do usuário.
        orgs = orgs_do_usuario(request.user)
        artifacts_qs = (
            Artifact.objects.filter(artifact_type="documento", tenant__in=orgs)
            .order_by("-created_at")
        )

        valid_artifacts = []
        for artifact in artifacts_qs:
            content = artifact.content or {}
            # Filtra apenas os que possuem MHTML
            if content.get("mhtml_path"):
                valid_artifacts.append(artifact)

        context = {
            "artifacts": valid_artifacts,
            "llm_extractor_model": getattr(settings, "LLM_EXTRACTOR_MODEL", "claude-sonnet-5"),
        }
        return render(request, "artifacts/gallery.html", context)

class ServeMHTMLView(View):
    @method_decorator(xframe_options_sameorigin)
    def get(self, request, artifact_id):
        # Isolamento de tenant: 404 (não 403) para artefato de outra org — não vaza existência.
        artifact = get_object_or_404(Artifact, id=artifact_id, tenant__in=orgs_do_usuario(request.user))
        content = artifact.content or {}
        
        mhtml_path = content.get("mhtml_path")
        mhtml_bucket = content.get("mhtml_bucket", "inteligencia-aberta-mhtml")
        
        if not mhtml_path:
            raise Http404("Caminho do MHTML não encontrado no artefato.")
            
        # Conexão com MinIO
        MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
        MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
        MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "substitua-por-senha-segura")
        
        try:
            client = Minio(
                MINIO_ENDPOINT,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                secure=False
            )
            
            # Pega o objeto inteiro na memória para conversão
            response = client.get_object(mhtml_bucket, mhtml_path)
            mhtml_bytes = response.read()
            response.close()
            response.release_conn()
            
            # Converte o formato MIME (MHTML) para um HTML único com Base64
            msg = email.message_from_bytes(mhtml_bytes, policy=policy.default)
            html_part = None
            resources = {}
            
            for part in msg.walk():
                content_type = part.get_content_type()
                content_id = part.get("Content-ID")
                content_location = part.get("Content-Location")
                
                # Encontra o arquivo HTML principal
                if content_type == "text/html" and not html_part:
                    from .tasks import _decode_html_bytes
                    mime_charset = part.get_content_charset()
                    payload = part.get_payload(decode=True)
                    if payload:
                        html_part = _decode_html_bytes(payload, mime_charset)
                # O resto são assets (imagens, css, etc)
                else:
                    payload = part.get_payload(decode=True)
                    if payload:
                        b64_payload = base64.b64encode(payload).decode('ascii')
                        data_uri = f"data:{content_type};base64,{b64_payload}"
                        
                        if content_location:
                            resources[content_location] = data_uri
                        if content_id:
                            cid = content_id.strip('<>')
                            resources[f"cid:{cid}"] = data_uri
                            
            if not html_part:
                raise Http404("Arquivo HTML não encontrado dentro do MHTML.")
                
            # Substitui as URLs originais pelas imagens em base64 no HTML
            for loc, data_uri in resources.items():
                html_part = html_part.replace(loc, data_uri)
                
            # Retorna o HTML processado nativamente
            return HttpResponse(html_part, content_type="text/html; charset=utf-8")
            
        except S3Error as e:
            print(f"Erro no MinIO: {e}")
            raise Http404("Arquivo não encontrado no MinIO")
        except Exception as e:
            print(f"Erro ao processar MHTML: {e}")
            raise Http404("Erro ao converter MHTML para visualização.")


class ArtifactFaviconView(View):
    """Favicon salvo em Artifact.content (usado pelo Mapa Vivo para hidratar
    nós criados ao vivo via WebSocket).

    O evento `captura.registrada` não carrega o favicon no payload — payload
    de evento tem teto de 8 KB e não é lugar para guardar uma imagem; o nó
    nasce sem imagem e busca aqui logo em seguida."""

    def get(self, request, artifact_id):
        artifact = get_object_or_404(Artifact, id=artifact_id, tenant__in=orgs_do_usuario(request.user))
        favicon = (artifact.content or {}).get("favicon_data_uri", "")
        if not favicon:
            raise Http404("Sem favicon para este artefato.")
        return JsonResponse({"favicon_data_uri": favicon})


class ArtifactContentView(View):
    """Retorna texto extraído e dados estruturados de um artefato (usado via AJAX pelo visualizador)."""

    def get(self, request, artifact_id):
        # Isolamento de tenant: 404 para artefato de outra org.
        artifact = get_object_or_404(Artifact, id=artifact_id, tenant__in=orgs_do_usuario(request.user))
        try:
            doc_text = artifact.extracted_text
        except DocumentText.DoesNotExist:
            raise Http404("Texto ainda não extraído para este artefato.")

        return JsonResponse({
            "page_type": doc_text.page_type,
            "detection_confidence": doc_text.detection_confidence,
            "detection_source": doc_text.detection_source,
            "char_count": doc_text.char_count,
            "word_count": doc_text.word_count,
            "extractor_version": doc_text.extractor_version,
            "text": doc_text.text,
            "structured_data": doc_text.structured_data,
            "dados_estruturados_dom2parser": doc_text.dados_estruturados_dom2parser,
            "dados_estruturados_extruct": doc_text.dados_estruturados_extruct,
            "dom_representation": doc_text.dom_representation,
        })


def _estruturacao_json(e: EstruturacaoLLM) -> dict:
    return {
        "id": str(e.id),
        "provider": e.provider,
        "model_name": e.model_name,
        "status": e.status,
        "categoria": e.categoria,
        "structured_data": e.structured_data,
        "error_message": e.error_message,
        "duration_ms": e.duration_ms,
        "created_at": e.created_at.isoformat(),
        "started_at": e.started_at.isoformat() if e.started_at else None,
    }


class EstruturarLLMView(View):
    """Dispara manualmente uma estruturação via LLM (Claude ou Ollama) para um
    artefato já capturado. Estritamente aditivo: acumula uma execução por
    disparo, nunca sobrescreve DocumentText."""

    def post(self, request, artifact_id):
        from .tasks import correlacao_do_artefato, estruturar_llm_manual

        artifact = get_object_or_404(Artifact, id=artifact_id, tenant__in=orgs_do_usuario(request.user))
        try:
            doc_text = artifact.extracted_text
        except DocumentText.DoesNotExist:
            return JsonResponse({"error": "Texto ainda não extraído para este artefato."}, status=400)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "JSON inválido"}, status=400)

        provider = data.get("provider")
        model_name = (data.get("model_name") or "").strip()
        if provider not in EstruturacaoLLM.Provider.values or not model_name:
            return JsonResponse({"error": "provider e model_name são obrigatórios"}, status=400)

        if provider == EstruturacaoLLM.Provider.ANTHROPIC and not permite_llm_externo(artifact.classification_level):
            return JsonResponse(
                {"error": f"LLM externo não permitido para classificação '{artifact.classification_level}'"},
                status=403,
            )

        execucao = EstruturacaoLLM.objects.create(
            document_text=doc_text,
            tenant=artifact.tenant,
            provider=provider,
            model_name=model_name,
            triggered_by=request.user,
        )

        correlation = correlacao_do_artefato(artifact.id)
        emit(
            "estruturacao_llm.solicitada", "ok",
            correlation_id=correlation,
            subject_type="artifact", subject_id=artifact.id,
            tenant_id=artifact.tenant_id, user_id=request.user.id,
            source="portal",
            message=f"estruturação manual pedida por {request.user.username} — {provider}:{model_name}",
            payload={"provider": provider, "model_name": model_name, "estruturacao_id": str(execucao.id)},
        )
        # O contextvar precisa estar definido ANTES do .delay() — mesmo padrão
        # de ReprocessarView (apps/events/views.py).
        set_correlation_id(correlation)
        set_tenant_id(artifact.tenant_id)
        async_result = estruturar_llm_manual.delay(str(execucao.id))
        # Guardado para permitir cancelamento (revoke) a partir da tela de execuções.
        execucao.celery_task_id = async_result.id
        execucao.save(update_fields=["celery_task_id"])

        return JsonResponse({"status": "enfileirado", "estruturacao_id": str(execucao.id)})


class EstruturacoesListView(View):
    """Lista as execuções de estruturação manual de um artefato, mais recentes primeiro."""

    def get(self, request, artifact_id):
        artifact = get_object_or_404(Artifact, id=artifact_id, tenant__in=orgs_do_usuario(request.user))
        execucoes = EstruturacaoLLM.objects.filter(
            document_text__document=artifact
        ).order_by("-created_at")
        return JsonResponse({"execucoes": [_estruturacao_json(e) for e in execucoes]})


class EstruturacaoCancelarView(View):
    """Interrompe uma execução na fila ou em andamento.

    revoke(terminate=True) manda SIGKILL no processo do worker que está preso
    na chamada HTTP ao provider — é a única forma confiável de interromper uma
    requisição síncrona e bloqueante no meio do caminho. O worker prefork sobe
    um processo novo automaticamente para as próximas tasks."""

    def post(self, request, artifact_id, estruturacao_id):
        from .tasks import correlacao_do_artefato

        artifact = get_object_or_404(Artifact, id=artifact_id, tenant__in=orgs_do_usuario(request.user))
        execucao = get_object_or_404(
            EstruturacaoLLM, id=estruturacao_id, document_text__document=artifact
        )
        if execucao.status not in (EstruturacaoLLM.Status.PENDENTE, EstruturacaoLLM.Status.EXECUTANDO):
            return JsonResponse({"error": "execução já finalizada"}, status=400)

        if execucao.celery_task_id:
            from celery import current_app
            current_app.control.revoke(execucao.celery_task_id, terminate=True, signal="SIGKILL")

        execucao.status = EstruturacaoLLM.Status.CANCELADO
        execucao.error_message = f"cancelado por {request.user.username}"
        execucao.save(update_fields=["status", "error_message", "updated_at"])

        emit(
            "estruturacao_llm.cancelada", "ignorado",
            correlation_id=correlacao_do_artefato(artifact.id),
            subject_type="artifact", subject_id=artifact.id,
            tenant_id=artifact.tenant_id, user_id=request.user.id,
            source="portal", message=execucao.error_message,
            payload={"provider": execucao.provider, "model_name": execucao.model_name,
                     "estruturacao_id": str(execucao.id)},
        )
        return JsonResponse({"status": "cancelado"})


class OllamaModelosView(View):
    """Proxy leve de GET /api/tags do Ollama — degrada graciosamente se o
    Ollama estiver offline (a UI precisa desabilitar a opção, não quebrar)."""

    def get(self, request):
        from .extractors.ollama_client import listar_modelos

        modelos = listar_modelos()
        return JsonResponse({"disponivel": bool(modelos), "modelos": modelos})


_CAMPOS_LEGADOS_PERMITIDOS = {"structured_data", "dados_estruturados_dom2parser", "dados_estruturados_extruct"}


def _comparacao_json(c: Comparacao) -> dict:
    return {
        "id": str(c.id),
        "referencias": c.referencias,
        "modelo_juiz_provider": c.modelo_juiz_provider,
        "modelo_juiz_model_name": c.modelo_juiz_model_name,
        "status": c.status,
        "resultado": c.resultado,
        "error_message": c.error_message,
        "duration_ms": c.duration_ms,
        "created_at": c.created_at.isoformat(),
        "started_at": c.started_at.isoformat() if c.started_at else None,
    }


class CompararView(View):
    """Dispara uma comparação entre 2+ seções de dado estruturado, julgada por
    um LLM-juiz escolhido no momento."""

    def post(self, request, artifact_id):
        from .tasks import comparar_llm, correlacao_do_artefato

        artifact = get_object_or_404(Artifact, id=artifact_id, tenant__in=orgs_do_usuario(request.user))

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "JSON inválido"}, status=400)

        referencias = data.get("referencias") or []
        if not isinstance(referencias, list) or len(referencias) < 2:
            return JsonResponse({"error": "selecione ao menos 2 seções para comparar"}, status=400)

        referencias_validadas = []
        for ref in referencias:
            tipo = ref.get("tipo") if isinstance(ref, dict) else None
            if tipo == "campo_legado":
                if ref.get("campo") not in _CAMPOS_LEGADOS_PERMITIDOS:
                    return JsonResponse({"error": f"campo legado inválido: {ref.get('campo')}"}, status=400)
                referencias_validadas.append({
                    "tipo": "campo_legado", "campo": ref["campo"], "label": ref.get("label", ref["campo"]),
                })
            elif tipo == "estruturacao_llm":
                execucao = EstruturacaoLLM.objects.filter(
                    id=ref.get("id"),
                    document_text__document=artifact,
                    status=EstruturacaoLLM.Status.CONCLUIDO,
                ).first()
                if not execucao:
                    return JsonResponse(
                        {"error": f"execução LLM inválida ou não concluída: {ref.get('id')}"}, status=400
                    )
                referencias_validadas.append({
                    "tipo": "estruturacao_llm", "id": str(execucao.id),
                    "label": ref.get("label") or f"{execucao.provider}:{execucao.model_name}",
                })
            else:
                return JsonResponse({"error": f"referência inválida: {ref}"}, status=400)

        modelo_juiz = data.get("modelo_juiz") or {}
        juiz_provider = modelo_juiz.get("provider")
        juiz_model = (modelo_juiz.get("model_name") or "").strip()
        if juiz_provider not in EstruturacaoLLM.Provider.values or not juiz_model:
            return JsonResponse({"error": "modelo_juiz.provider e model_name são obrigatórios"}, status=400)

        if juiz_provider == EstruturacaoLLM.Provider.ANTHROPIC and not permite_llm_externo(artifact.classification_level):
            return JsonResponse(
                {"error": f"LLM externo não permitido para classificação '{artifact.classification_level}'"},
                status=403,
            )

        comparacao = Comparacao.objects.create(
            artifact=artifact,
            tenant=artifact.tenant,
            referencias=referencias_validadas,
            modelo_juiz_provider=juiz_provider,
            modelo_juiz_model_name=juiz_model,
            triggered_by=request.user,
        )

        correlation = correlacao_do_artefato(artifact.id)
        emit(
            "comparacao.solicitada", "ok",
            correlation_id=correlation,
            subject_type="artifact", subject_id=artifact.id,
            tenant_id=artifact.tenant_id, user_id=request.user.id,
            source="portal",
            message=f"comparação pedida por {request.user.username} — juiz {juiz_provider}:{juiz_model}",
            payload={"comparacao_id": str(comparacao.id), "secoes": len(referencias_validadas)},
        )
        set_correlation_id(correlation)
        set_tenant_id(artifact.tenant_id)
        async_result = comparar_llm.delay(str(comparacao.id))
        comparacao.celery_task_id = async_result.id
        comparacao.save(update_fields=["celery_task_id"])

        return JsonResponse({"status": "enfileirado", "comparacao_id": str(comparacao.id)})


class ComparacoesListView(View):
    """Histórico de comparações de um artefato, mais recentes primeiro."""

    def get(self, request, artifact_id):
        artifact = get_object_or_404(Artifact, id=artifact_id, tenant__in=orgs_do_usuario(request.user))
        comparacoes = Comparacao.objects.filter(artifact=artifact).order_by("-created_at")
        return JsonResponse({"comparacoes": [_comparacao_json(c) for c in comparacoes]})


class ComparacaoCancelarView(View):
    """Interrompe uma comparação na fila ou em andamento — ver EstruturacaoCancelarView."""

    def post(self, request, artifact_id, comparacao_id):
        from .tasks import correlacao_do_artefato

        artifact = get_object_or_404(Artifact, id=artifact_id, tenant__in=orgs_do_usuario(request.user))
        comparacao = get_object_or_404(Comparacao, id=comparacao_id, artifact=artifact)
        if comparacao.status not in (Comparacao.Status.PENDENTE, Comparacao.Status.EXECUTANDO):
            return JsonResponse({"error": "comparação já finalizada"}, status=400)

        if comparacao.celery_task_id:
            from celery import current_app
            current_app.control.revoke(comparacao.celery_task_id, terminate=True, signal="SIGKILL")

        comparacao.status = Comparacao.Status.CANCELADO
        comparacao.error_message = f"cancelado por {request.user.username}"
        comparacao.save(update_fields=["status", "error_message"])

        emit(
            "comparacao.cancelada", "ignorado",
            correlation_id=correlacao_do_artefato(artifact.id),
            subject_type="artifact", subject_id=artifact.id,
            tenant_id=artifact.tenant_id, user_id=request.user.id,
            source="portal", message=comparacao.error_message,
            payload={"comparacao_id": str(comparacao.id)},
        )
        return JsonResponse({"status": "cancelado"})


class MapaVivoView(View):
    """Tela do mapa vivo — grafo inicial embutido, atualizações via /ws/eventos/."""

    def get(self, request):
        orgs = orgs_do_usuario(request.user)
        limite = getattr(settings, "MAPA_VIVO_LIMITE_ARTEFATOS", 150)
        artifacts = artifacts_para_mapa(orgs, limite=limite)
        return render(request, "artifacts/mapa_vivo.html", {"dados_iniciais": montar_grafo(artifacts)})


class MapaVivoGrafoView(View):
    """GET .../grafo/?antes=<iso>&limite=<n> — carga inicial via fetch, paginação
    'carregar mais antigos' e full-resync do fallback de polling do mapa vivo."""

    def get(self, request):
        orgs = orgs_do_usuario(request.user)
        limite = min(int(request.GET.get("limite", 150)), 300)
        artifacts = artifacts_para_mapa(orgs, limite=limite, antes=request.GET.get("antes") or None)
        return JsonResponse(montar_grafo(artifacts))
