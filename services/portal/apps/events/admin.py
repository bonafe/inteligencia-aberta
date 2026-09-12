from django.contrib import admin

from .models import PipelineEvent, PipelineRun


@admin.register(PipelineEvent)
class PipelineEventAdmin(admin.ModelAdmin):
    """Somente leitura: `PipelineEvent` é append-only (ver ADR 005)."""

    list_display = ("sequence", "occurred_at", "source", "stage", "status", "hostname", "duration_ms")
    list_filter = ("source", "status", "stage")
    search_fields = ("correlation_id", "stage", "message", "celery_task_id")
    date_hierarchy = "occurred_at"
    ordering = ("-sequence",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    """Somente leitura: é projeção — para corrigir, rode reconstruir_projecoes."""

    list_display = ("iniciado_em", "status", "url", "total_eventos", "total_falhas", "duracao_ms")
    list_filter = ("status",)
    search_fields = ("correlation_id", "url", "titulo")
    date_hierarchy = "iniciado_em"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
