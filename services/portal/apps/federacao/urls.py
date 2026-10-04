from django.urls import path

from . import views_controle, views_convite

app_name = "federacao"

urlpatterns = [
    # Canal entre instâncias: autenticado pela assinatura, não pela sessão (ver middleware).
    path("convite/aceitar/", views_convite.AceitarConviteView.as_view(), name="convite_aceitar"),
    path("controle/v1/ping/", views_controle.ping, name="controle_ping"),
    path("controle/v1/estado/", views_controle.estado, name="controle_estado"),
    path("controle/v1/ollama/operacoes/", views_controle.ollama_solicitar, name="controle_ollama_solicitar"),
    path("controle/v1/ollama/operacoes/<uuid:op_id>/", views_controle.ollama_operacao, name="controle_ollama_operacao"),
    path("controle/v1/ollama/operacoes/<uuid:op_id>/cancelar/", views_controle.ollama_cancelar, name="controle_ollama_cancelar"),
]
