from django.urls import path

from . import views

app_name = "cluster"

urlpatterns = [
    path("", views.PainelClusterView.as_view(), name="painel"),
    # Canal máquina-a-máquina — X-Machine-Token (ver views.py).
    path("api/v1/replicacao/eventos/", views.ReplicacaoEventosAPIView.as_view(), name="api_replicacao_eventos"),
]
