from django.urls import path

from . import views

app_name = "financeiro"

urlpatterns = [
    path("", views.GastosView.as_view(), name="gastos"),
    path("registrar/<uuid:estruturacao_id>/", views.RegistrarLancamentosView.as_view(), name="registrar"),
]
