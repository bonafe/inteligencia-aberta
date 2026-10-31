"""Schema de tipo de página (`SchemaPagina`): gerar a partir de uma captura, listar as versões
do tipo, cancelar uma geração e a tela de revisão (editar, pré-visualizar, salvar, aprovar).

Isolado por organização: a captura e o schema têm de ser de uma organização da pessoa,
senão 404. LLM externo segue a mesma régua da estruturação manual (`permite_llm_externo`).
Qualquer membro edita um rascunho; **aprovar** é de dono/administrador, porque o parser
aprovado passa a valer para toda captura daquele tipo na organização. Só aprova o que
valida na amostra.
"""

import json

from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views import View

from apps.accounts.permissoes import eh_admin
from apps.accounts.views import orgs_do_usuario
from apps.events.context import set_correlation_id, set_tenant_id
from apps.events.emit import emit

from .extractors.schema_pagina import TIPOS
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
        "url": f"/artifacts/schemas/{s.id}/",
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


# ── revisão ─────────────────────────────────────────────────────────────────

def _schema_da_pessoa(request, schema_id) -> SchemaPagina:
    return get_object_or_404(
        SchemaPagina.objects.select_related("padrao", "amostra", "aprovado_por"),
        id=schema_id, tenant__in=orgs_do_usuario(request.user),
    )


def _html_da_amostra(registro: SchemaPagina) -> str:
    """O HTML da captura de amostra, relido do Garage. `ValueError` com a razão se não der."""
    from .tasks import _html_do_mhtml, _ler_mhtml

    content = (registro.amostra.content or {}) if registro.amostra else {}
    if not content.get("mhtml_path"):
        raise ValueError("a captura de amostra não existe mais ou não tem MHTML")
    html, _ = _html_do_mhtml(_ler_mhtml(content.get("mhtml_bucket", "inteligencia-aberta-mhtml"), content["mhtml_path"]))
    if not html:
        raise ValueError("a amostra não tem HTML")
    return html


def _schema_do_corpo(request):
    from .extractors.schema_pagina import normalizar_schema

    try:
        bruto = json.loads(request.body).get("schema")
    except (json.JSONDecodeError, AttributeError):
        return None, None
    return normalizar_schema(bruto)


def _montar_e_executar(schema, html):
    from .extractors import parser_pagina

    parser, avisos = parser_pagina.montar_parser(schema, html)
    return parser, avisos, parser_pagina.executar(parser, schema, html)


class RevisarSchemaView(View):
    def get(self, request, schema_id):
        registro = _schema_da_pessoa(request, schema_id)
        versoes = SchemaPagina.objects.filter(padrao=registro.padrao).order_by("-versao")
        titulo = ""
        if registro.amostra_id:
            titulo = DocumentText.objects.filter(document_id=registro.amostra_id).values_list("title", flat=True).first() or ""
        return render(request, "artifacts/schema_revisao.html", {
            "registro": registro,
            "versoes": versoes,
            "titulo_amostra": titulo,
            "editavel": registro.status in SchemaPagina.EDITAVEIS,
            "pode_aprovar": eh_admin(request.user, registro.tenant),
            "schema_inicial": registro.schema or {"descricao": "", "campos": [], "listas": [], "invariantes": []},
            "tipos": TIPOS,
        })


class PreviaSchemaView(View):
    """O que o parser deste schema (ainda não salvo) extrai da amostra, com a validação."""

    def post(self, request, schema_id):
        from .extractors import parser_pagina

        registro = _schema_da_pessoa(request, schema_id)
        schema, avisos = _schema_do_corpo(request)
        if schema is None:
            return JsonResponse({"error": "JSON inválido"}, status=400)
        try:
            html = _html_da_amostra(registro)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except Exception:
            return JsonResponse({"error": "não foi possível ler a amostra no armazenamento"}, status=503)
        parser, avisos_parser, resultado = _montar_e_executar(schema, html)
        return JsonResponse({
            "schema": schema,
            "avisos": avisos + avisos_parser,
            "localizados": parser_pagina.campos_localizados(parser),
            "resultado": parser_pagina.previa(resultado),
        })


class SalvarSchemaView(View):
    def post(self, request, schema_id):
        from .extractors.schema_pagina import schema_vazio

        registro = _schema_da_pessoa(request, schema_id)
        if registro.status not in SchemaPagina.EDITAVEIS:
            return JsonResponse({"error": f"schema {registro.status} não se edita; crie uma nova versão"}, status=400)
        schema, avisos = _schema_do_corpo(request)
        if schema is None:
            return JsonResponse({"error": "JSON inválido"}, status=400)
        registro.schema, registro.avisos = schema, avisos
        registro.status = SchemaPagina.Status.VAZIO if schema_vazio(schema) else SchemaPagina.Status.RASCUNHO
        registro.save(update_fields=["schema", "avisos", "status", "updated_at"])
        emit("schema_pagina.editado", "ok", subject_type="artifact", subject_id=registro.amostra_id,
             tenant_id=registro.tenant_id, user_id=request.user.id, source="portal",
             message=f"schema v{registro.versao} editado por {request.user.username}",
             payload={"schema_id": str(registro.id), "versao": registro.versao})
        return JsonResponse({"schema": schema, "avisos": avisos, "status": registro.status})


class AprovarSchemaView(View):
    """Monta o parser do schema salvo, valida na amostra e, se validar, passa a valer."""

    def post(self, request, schema_id):
        registro = _schema_da_pessoa(request, schema_id)
        if not eh_admin(request.user, registro.tenant):
            return JsonResponse({"error": "só dono ou administrador da organização aprova"}, status=403)
        if registro.status != SchemaPagina.Status.RASCUNHO:
            return JsonResponse({"error": f"só um rascunho é aprovado (este está {registro.status})"}, status=400)
        try:
            html = _html_da_amostra(registro)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except Exception:
            return JsonResponse({"error": "não foi possível ler a amostra no armazenamento"}, status=503)
        parser, avisos, resultado = _montar_e_executar(registro.schema, html)
        if not resultado["validacao"]["ok"]:
            return JsonResponse({"error": "a amostra não valida com este schema",
                                 "problemas": resultado["validacao"]["problemas"]}, status=400)
        with transaction.atomic():
            anteriores = SchemaPagina.objects.select_for_update().filter(
                padrao=registro.padrao, status=SchemaPagina.Status.APROVADO)
            substituidos = [str(a.versao) for a in anteriores]
            anteriores.update(status=SchemaPagina.Status.SUBSTITUIDO, updated_at=timezone.now())
            registro.status = SchemaPagina.Status.APROVADO
            registro.parser, registro.avisos = parser, list(registro.avisos or []) + avisos
            registro.aprovado_por, registro.aprovado_em = request.user, timezone.now()
            registro.save(update_fields=["status", "parser", "avisos", "aprovado_por", "aprovado_em", "updated_at"])
        emit("schema_pagina.aprovado", "ok", subject_type="artifact", subject_id=registro.amostra_id,
             tenant_id=registro.tenant_id, user_id=request.user.id, source="portal",
             message=f"schema v{registro.versao} de {registro.padrao.domain}{registro.padrao.path_pattern} aprovado",
             payload={"schema_id": str(registro.id), "versao": registro.versao, "substituidos": substituidos})
        return JsonResponse({"status": registro.status, "substituidos": substituidos})


class NovaVersaoSchemaView(View):
    """Copia este schema para um rascunho novo, para editar sem tocar no aprovado."""

    def post(self, request, schema_id):
        origem = _schema_da_pessoa(request, schema_id)
        if not origem.schema:
            return JsonResponse({"error": "este schema não tem conteúdo para copiar"}, status=400)
        with transaction.atomic():
            URLPatternCache.objects.select_for_update().get(pk=origem.padrao_id)
            versao = (SchemaPagina.objects.filter(padrao=origem.padrao).aggregate(m=Max("versao"))["m"] or 0) + 1
            nova = SchemaPagina.objects.create(
                tenant=origem.tenant, padrao=origem.padrao, amostra=origem.amostra, versao=versao,
                status=SchemaPagina.Status.RASCUNHO, schema=origem.schema, avisos=[],
                classification_level=origem.classification_level, provider=origem.provider,
                model_name=origem.model_name, triggered_by=request.user, derivado_de=origem,
            )
        return JsonResponse({"id": str(nova.id), "versao": versao, "url": f"/artifacts/schemas/{nova.id}/"})
