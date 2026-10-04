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


from .models import RegraReplicacao  # noqa: E402


@admin.register(RegraReplicacao)
class RegraReplicacaoAdmin(admin.ModelAdmin):
    """Regras do motor de replicação (ADR 011). **Negar vence**, sem prioridade. As padrão
    se **desativam, não se apagam**. Uma regra com objeto (concessão) exige validade e par."""

    list_display = ("efeito", "sentido", "organizacao", "par_ref", "par_tipo", "nivel", "tipo_objeto",
                    "espaco_urn", "objeto_urn", "valida_ate", "ativa", "padrao")
    list_filter = ("efeito", "sentido", "ativa", "padrao", "organizacao")
    search_fields = ("par_ref", "objeto_urn", "espaco_urn", "observacao")
    readonly_fields = ("id", "padrao", "chave_padrao", "revogada_em", "criada_por", "criada_em")

    def has_delete_permission(self, request, obj=None):
        return obj is None or not obj.padrao

    def save_model(self, request, obj, form, change):
        if not change:
            obj.criada_por = request.user
        super().save_model(request, obj, form, change)
