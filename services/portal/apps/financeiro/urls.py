from django.urls import path

from . import views

app_name = "financeiro"

urlpatterns = [
    path("", views.GastosView.as_view(), name="gastos"),
    path("registrar/<uuid:estruturacao_id>/", views.RegistrarLancamentosView.as_view(), name="registrar"),
    path("registrar-extracao/<uuid:extracao_id>/", views.RegistrarExtracaoView.as_view(), name="registrar_extracao"),
]
