from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.PainelEventosView.as_view(), name="painel"),
    path("<uuid:correlation_id>/", views.ExecucaoDetailView.as_view(), name="execucao"),
    path("<uuid:correlation_id>/reprocessar/", views.ReprocessarView.as_view(), name="reprocessar"),
    path("api/v1/eventos/", views.EventosAPIView.as_view(), name="api_eventos"),
    path("api/v1/execucoes/", views.ExecucoesAPIView.as_view(), name="api_execucoes"),
    path("api/v1/agregados/", views.AgregadosAPIView.as_view(), name="api_agregados"),
    # Canal serviço-a-serviço (orchestrator, MCP) — X-Internal-Token.
    path("api/v1/ingest/", views.EventoIngestAPIView.as_view(), name="api_ingest"),
]
