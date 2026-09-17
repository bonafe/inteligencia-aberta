from django.contrib import admin
from .models import (
    Artifact, ArtifactLineage, AuditLog, Comparacao, DocumentFragment, DocumentText,
    EstruturacaoLLM, Sharing, URLPatternCache,
)


@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    list_display = ("id", "artifact_type", "classification_level", "tenant", "info_type", "created_at")
    list_filter = ("artifact_type", "classification_level", "info_type")
    search_fields = ("id",)
    readonly_fields = ("id", "classified_at", "created_at", "updated_at")


@admin.register(ArtifactLineage)
class ArtifactLineageAdmin(admin.ModelAdmin):
    list_display = ("transformation", "processor", "parent", "child", "created_at")
    list_filter = ("transformation",)
    readonly_fields = ("id", "parent", "child", "transformation", "processor", "parameters", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("operation", "outcome", "user", "artifact", "timestamp")
    list_filter = ("outcome", "operation")
    readonly_fields = (
        "id", "artifact", "user", "organization",
        "operation", "outcome", "reason", "timestamp", "metadata",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Sharing)
class SharingAdmin(admin.ModelAdmin):
    list_display = ("artifact", "shared_by", "recipient_type", "status", "created_at", "expires_at")
    list_filter = ("status", "recipient_type")
    readonly_fields = ("id", "created_at")


@admin.register(DocumentText)
class DocumentTextAdmin(admin.ModelAdmin):
    list_display = ("id", "document", "page_type", "word_count", "char_count", "detection_source", "created_at")
    list_filter = ("page_type", "detection_source")
    search_fields = ("document__id",)
    readonly_fields = ("id", "document", "created_at", "updated_at")


@admin.register(DocumentFragment)
class DocumentFragmentAdmin(admin.ModelAdmin):
    list_display = ("id", "document_text", "fragment_index", "total_fragments", "qdrant_point_id", "created_at")
    list_filter = ("total_fragments",)
    search_fields = ("document_text__document__id", "qdrant_point_id")
    readonly_fields = ("id", "document_text", "fragment_index", "total_fragments", "created_at", "updated_at")


@admin.register(URLPatternCache)
class URLPatternCacheAdmin(admin.ModelAdmin):
    list_display = ("domain", "path_pattern", "page_type", "confidence", "hit_count", "divergence_count", "needs_review", "tenant", "last_seen_at")
    list_filter = ("page_type", "needs_review", "tenant")
    search_fields = ("domain", "path_pattern")
    readonly_fields = ("id", "hit_count", "divergence_count", "last_seen_at", "created_at")
    ordering = ("-last_seen_at",)


def _chamada_llm_do_artefato(artifact_id):
    """Última ChamadaLLM ligada a este artefato (subject_type/subject_id —
    ver apps.events.llm_telemetria). Sem FK direta de propósito: os 4 pontos
    de chamada do sistema se linkam todos do mesmo jeito, sem exceção."""
    from apps.events.models import ChamadaLLM

    return (
        ChamadaLLM.objects.filter(subject_type="artifact", subject_id=artifact_id)
        .order_by("-ocorreu_em").first()
    )


@admin.register(EstruturacaoLLM)
class EstruturacaoLLMAdmin(admin.ModelAdmin):
    list_display = (
        "id", "document_text", "provider", "model_name", "status",
        "tokens_chamada", "duration_ms_chamada", "triggered_by", "created_at",
    )
    list_filter = ("provider", "status", "tenant")
    search_fields = ("document_text__document__id", "model_name")
    readonly_fields = (
        "id", "document_text", "tenant", "provider", "model_name", "status",
        "categoria", "structured_data", "error_message", "triggered_by",
        "celery_task_id", "started_at", "duration_ms", "created_at", "updated_at",
    )

    @admin.display(description="tokens (chamada)")
    def tokens_chamada(self, obj):
        chamada = _chamada_llm_do_artefato(obj.document_text.document_id)
        if not chamada:
            return "—"
        return f"{chamada.tokens_entrada or 0} → {chamada.tokens_saida or 0}"

    @admin.display(description="duração da chamada (ms)")
    def duration_ms_chamada(self, obj):
        chamada = _chamada_llm_do_artefato(obj.document_text.document_id)
        return chamada.duration_ms if chamada else "—"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Comparacao)
class ComparacaoAdmin(admin.ModelAdmin):
    list_display = (
        "id", "artifact", "modelo_juiz_provider", "modelo_juiz_model_name", "status",
        "tokens_chamada", "duration_ms_chamada", "triggered_by", "created_at",
    )
    list_filter = ("modelo_juiz_provider", "status", "tenant")
    search_fields = ("artifact__id",)
    readonly_fields = (
        "id", "artifact", "tenant", "referencias", "modelo_juiz_provider",
        "modelo_juiz_model_name", "status", "resultado", "error_message",
        "triggered_by", "celery_task_id", "started_at", "duration_ms", "created_at",
    )

    @admin.display(description="tokens (chamada)")
    def tokens_chamada(self, obj):
        chamada = _chamada_llm_do_artefato(obj.artifact_id)
        if not chamada:
            return "—"
        return f"{chamada.tokens_entrada or 0} → {chamada.tokens_saida or 0}"

    @admin.display(description="duração da chamada (ms)")
    def duration_ms_chamada(self, obj):
        chamada = _chamada_llm_do_artefato(obj.artifact_id)
        return chamada.duration_ms if chamada else "—"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
