from django.urls import path

from . import views

app_name = "cluster"

urlpatterns = [
    path("", views.PainelClusterView.as_view(), name="painel"),
    # Canal máquina-a-máquina — X-Machine-Token (ver views.py).
    path("api/v1/replicacao/eventos/", views.ReplicacaoEventosAPIView.as_view(), name="api_replicacao_eventos"),
    # Descoberta automática (scripts/entrar_no_cluster.py) — sem auth, só diz o papel desta máquina.
    path("api/v1/status/", views.StatusPublicoView.as_view(), name="api_status"),
    # Autorregistro — X-Cluster-Join-Secret (ver views.py).
    path("api/v1/join/", views.JoinAPIView.as_view(), name="api_join"),
]
