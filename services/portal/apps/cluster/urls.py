from django.urls import path

from . import views, views_pares

app_name = "cluster"

urlpatterns = [
    path("", views.PainelClusterView.as_view(), name="painel"),
    path("pares/", views_pares.ParesView.as_view(), name="pares"),
    path("pares/convite/", views_pares.CriarConviteView.as_view(), name="pares_convite"),
    path("pares/convite/previsualizar/", views_pares.PreverConviteView.as_view(), name="pares_previsualizar"),
    path("pares/convite/aceitar/", views_pares.AceitarConviteView.as_view(), name="pares_aceitar"),
    path("pares/<uuid:par_id>/confirmar/", views_pares.ConfirmarParView.as_view(), name="par_confirmar"),
    path("pares/<uuid:par_id>/revogar/", views_pares.RevogarParView.as_view(), name="par_revogar"),
    path("pares/<uuid:par_id>/tipo/", views_pares.DefinirTipoParView.as_view(), name="par_tipo"),
]
