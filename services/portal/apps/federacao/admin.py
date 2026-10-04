from django.contrib import admin

from .models import EspacoArtefato, Space


class EspacoArtefatoInline(admin.TabularInline):
    model = EspacoArtefato
    extra = 0
    raw_id_fields = ("artefato",)
    readonly_fields = ("incluido_por", "incluido_em")


@admin.register(Space)
class SpaceAdmin(admin.ModelAdmin):
    """Só estrutura: o espaço não concede acesso (o acesso segue por organização).
    O espaço padrão de cada organização é implícito e não aparece aqui."""

    list_display = ("nome", "organizacao", "arquivado", "criado_por", "criado_em")
    list_filter = ("arquivado", "organizacao")
    search_fields = ("nome",)
    readonly_fields = ("id", "criado_por", "criado_em")
    inlines = [EspacoArtefatoInline]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.criado_por = request.user
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        for item in formset.save(commit=False):
            if item.pk is None:
                item.incluido_por = request.user
            item.save()
        formset.save_m2m()
        for removido in formset.deleted_objects:
            removido.delete()
