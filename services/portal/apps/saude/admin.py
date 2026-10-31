from django.contrib import admin

from .models import ResultadoExame


@admin.register(ResultadoExame)
class ResultadoExameAdmin(admin.ModelAdmin):
    """Só leitura: é projeção das alegações (`exames.projetar_artefato`)."""

    list_display = ("data", "analito", "resultado", "unidade", "tenant")
    list_filter = ("tenant",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
