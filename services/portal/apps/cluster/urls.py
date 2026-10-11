from django.urls import path

from apps.federacao import views_regras

from . import views, views_modelos, views_pares

app_name = "cluster"

urlpatterns = [
    path("", views.PainelClusterView.as_view(), name="painel"),
    path("maquinas/<uuid:maquina_id>/modelos/", views_modelos.InventarioView.as_view(), name="modelos_inventario"),
    path("maquinas/<uuid:maquina_id>/modelos/instalar/", views_modelos.InstalarView.as_view(), name="modelos_instalar"),
    path("maquinas/<uuid:maquina_id>/modelos/remover/", views_modelos.RemoverView.as_view(), name="modelos_remover"),
    path("operacoes/<uuid:operacao_id>/", views_modelos.OperacaoView.as_view(), name="operacao"),
    path("operacoes/<uuid:operacao_id>/cancelar/", views_modelos.CancelarOperacaoView.as_view(), name="operacao_cancelar"),
    path("regras/", views_regras.RegrasView.as_view(), name="regras"),
    path("pares/", views_pares.ParesView.as_view(), name="pares"),
    path("pares/convite/", views_pares.CriarConviteView.as_view(), name="pares_convite"),
    path("pares/convite/previsualizar/", views_pares.PreverConviteView.as_view(), name="pares_previsualizar"),
    path("pares/convite/aceitar/", views_pares.AceitarConviteView.as_view(), name="pares_aceitar"),
    path("pares/<uuid:par_id>/confirmar/", views_pares.ConfirmarParView.as_view(), name="par_confirmar"),
    path("pares/<uuid:par_id>/revogar/", views_pares.RevogarParView.as_view(), name="par_revogar"),
    path("pares/<uuid:par_id>/tipo/", views_pares.DefinirTipoParView.as_view(), name="par_tipo"),
]
