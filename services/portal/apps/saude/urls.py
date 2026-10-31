from django.urls import path

from . import views

app_name = "saude"

urlpatterns = [
    path("", views.ExamesView.as_view(), name="exames"),
    path("registrar/<uuid:estruturacao_id>/", views.RegistrarEstruturacaoView.as_view(), name="registrar"),
    path("registrar-extracao/<uuid:extracao_id>/", views.RegistrarExtracaoView.as_view(), name="registrar_extracao"),
]
