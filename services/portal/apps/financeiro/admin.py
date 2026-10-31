from django.contrib import admin

from .models import CategoriaGasto, Conta, DescritorEstabelecimento, Estabelecimento, Movimento


class SoLeitura:
    """Projeção: categorias, estabelecimentos e contas vêm das decisões da pessoa (`decisoes.py`)
    e dos documentos; editar aqui seria desfeito na próxima projeção."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CategoriaGasto)
class CategoriaGastoAdmin(SoLeitura, admin.ModelAdmin):
    list_display = ("nome", "tenant")
    list_filter = ("tenant",)


class DescritorInline(SoLeitura, admin.TabularInline):
    model = DescritorEstabelecimento
    extra = 0


@admin.register(Estabelecimento)
class EstabelecimentoAdmin(SoLeitura, admin.ModelAdmin):
    list_display = ("nome", "categoria", "empresa_ref", "tenant")
    list_filter = ("tenant", "categoria")
    search_fields = ("nome", "descritores__descritor")
    inlines = [DescritorInline]


@admin.register(Conta)
class ContaAdmin(SoLeitura, admin.ModelAdmin):
    list_display = ("nome", "tipo", "instituicao", "titular", "propria", "tenant")
    list_filter = ("tenant", "tipo")


@admin.register(Movimento)
class MovimentoAdmin(admin.ModelAdmin):
    """Só leitura: é projeção das alegações (`reconstruir_lancamentos`)."""

    list_display = ("competencia", "data", "descricao", "valor", "natureza", "conta", "estabelecimento", "tenant")
    list_filter = ("tenant", "natureza", "conta")
    search_fields = ("descricao",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
