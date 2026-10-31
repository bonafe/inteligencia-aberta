"""Schema de tipo de página (`SchemaPagina`): gerar a partir de uma captura, listar as versões
do tipo e cancelar uma geração. A revisão do schema é outra tela.

Isolado por organização: a captura e o schema têm de ser de uma organização da pessoa,
senão 404. LLM externo segue a mesma régua da estruturação manual (`permite_llm_externo`).
"""

import json

from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View

from apps.accounts.views import orgs_do_usuario
from apps.events.context import set_correlation_id, set_tenant_id
from apps.events.emit import emit

from .models import Artifact, DocumentText, SchemaPagina, URLPatternCache
from .policy import permite_llm_externo
from .views import _maquina_escolhida


def _schema_json(s: SchemaPagina) -> dict:
    return {
        "id": str(s.id),
        "versao": s.versao,
        "status": s.status,
        "provider": s.provider,
        "model_name": s.model_name,
        "amostra": str(s.amostra_id) if s.amostra_id else None,
        "schema": s.schema,
        "avisos": s.avisos,
        "error_message": s.error_message,
        "duration_ms": s.duration_ms,
        "created_at": s.created_at.isoformat(),
        "started_at": s.started_at.isoformat() if s.started_at else None,
    }


def _padrao_da_captura(request, artifact_id):
    artifact = get_object_or_404(Artifact, id=artifact_id, tenant__in=orgs_do_usuario(request.user))
    try:
        padrao = artifact.extracted_text.url_pattern_cache
    except DocumentText.DoesNotExist:
        padrao = None
    return artifact, padrao


class GerarSchemaView(View):
    def post(self, request, artifact_id):
        from .tasks import correlacao_do_artefato, gerar_schema_pagina

        artifact, padrao = _padrao_da_captura(request, artifact_id)
        if padrao is None:
            return JsonResponse({"error": "a captura ainda não tem tipo de página (extração pendente)"}, status=400)
        if not (artifact.content or {}).get("mhtml_path"):
            return JsonResponse({"error": "a captura não tem MHTML"}, status=400)
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "JSON inválido"}, status=400)

        provider = data.get("provider")
        model_name = (data.get("model_name") or "").strip()
        if provider not in SchemaPagina.Provider.values or not model_name:
            return JsonResponse({"error": "provider e model_name são obrigatórios"}, status=400)
        if provider == SchemaPagina.Provider.ANTHROPIC and not permite_llm_externo(artifact.classification_level):
            return JsonResponse(
                {"error": f"LLM externo não permitido para classificação '{artifact.classification_level}'"},
                status=403,
            )
        maquina_id, erro = _maquina_escolhida(request, provider, model_name, data.get("maquina"))
        if erro:
            return JsonResponse({"error": erro}, status=400)

        with transaction.atomic():
            URLPatternCache.objects.select_for_update().get(pk=padrao.pk)
            versao = (SchemaPagina.objects.filter(padrao=padrao).aggregate(m=Max("versao"))["m"] or 0) + 1
            registro = SchemaPagina.objects.create(
                tenant=artifact.tenant, padrao=padrao, amostra=artifact, versao=versao,
                classification_level=artifact.classification_level, provider=provider,
                model_name=model_name, triggered_by=request.user,
            )

        correlation = correlacao_do_artefato(artifact.id)
        emit("schema_pagina.solicitado", "ok", correlation_id=correlation,
             subject_type="artifact", subject_id=artifact.id, tenant_id=artifact.tenant_id,
             user_id=request.user.id, source="portal",
             message=f"schema v{versao} de {padrao.domain}{padrao.path_pattern} pedido — {provider}:{model_name}",
             payload={"schema_id": str(registro.id), "versao": versao, "provider": provider,
                      "model_name": model_name})
        set_correlation_id(correlation)
        set_tenant_id(artifact.tenant_id)
        resultado = gerar_schema_pagina.delay(str(registro.id), maquina_id)
        registro.celery_task_id = resultado.id
        registro.save(update_fields=["celery_task_id"])
        return JsonResponse({"status": "enfileirado", "schema_id": str(registro.id), "versao": versao})


class SchemasListView(View):
    """As versões de schema do tipo de página desta captura, mais nova primeiro."""

    def get(self, request, artifact_id):
        _, padrao = _padrao_da_captura(request, artifact_id)
        if padrao is None:
            return JsonResponse({"padrao": None, "schemas": []})
        schemas = SchemaPagina.objects.filter(padrao=padrao).order_by("-versao")
        return JsonResponse({
            "padrao": {"dominio": padrao.domain, "caminho": padrao.path_pattern,
                       "impressao": padrao.structure_fingerprint},
            "schemas": [_schema_json(s) for s in schemas],
        })


class SchemaCancelarView(View):
    def post(self, request, artifact_id, schema_id):
        _, padrao = _padrao_da_captura(request, artifact_id)
        registro = get_object_or_404(SchemaPagina, id=schema_id, padrao=padrao)
        if registro.status not in (SchemaPagina.Status.PENDENTE, SchemaPagina.Status.EXECUTANDO):
            return JsonResponse({"error": "geração já finalizada"}, status=400)
        if registro.celery_task_id:
            from celery import current_app
            current_app.control.revoke(registro.celery_task_id, terminate=True, signal="SIGKILL")
        registro.status = SchemaPagina.Status.CANCELADO
        registro.error_message = f"cancelado por {request.user.username}"
        registro.save(update_fields=["status", "error_message", "updated_at"])
        emit("schema_pagina.cancelado", "ignorado", subject_type="artifact", subject_id=registro.amostra_id,
             tenant_id=registro.tenant_id, user_id=request.user.id, source="portal",
             message=registro.error_message, payload={"schema_id": str(registro.id), "versao": registro.versao})
        return JsonResponse({"status": "cancelado"})
