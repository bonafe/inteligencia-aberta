from django.contrib import admin

from .models import CategoriaGasto, DescritorEstabelecimento, Estabelecimento, Lancamento


@admin.register(CategoriaGasto)
class CategoriaGastoAdmin(admin.ModelAdmin):
    list_display = ("nome", "tenant")
    list_filter = ("tenant",)


class DescritorInline(admin.TabularInline):
    model = DescritorEstabelecimento
    extra = 0


@admin.register(Estabelecimento)
class EstabelecimentoAdmin(admin.ModelAdmin):
    list_display = ("nome", "categoria", "empresa_ref", "tenant")
    list_filter = ("tenant", "categoria")
    search_fields = ("nome", "descritores__descritor")
    inlines = [DescritorInline]


@admin.register(Lancamento)
class LancamentoAdmin(admin.ModelAdmin):
    """Só leitura: é projeção das alegações (`reconstruir_lancamentos`)."""

    list_display = ("data", "descricao", "valor", "tipo", "estabelecimento", "tenant")
    list_filter = ("tenant", "tipo")
    search_fields = ("descricao",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
