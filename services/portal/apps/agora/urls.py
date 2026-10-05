from django.urls import path

from . import views, views_dominio

app_name = "agora"

urlpatterns = [
    path("", views.AppView.as_view(), name="app"),
    path("api/v1/workspaces/", views.WorkspacesView.as_view(), name="workspaces"),
    path("api/v1/workspaces/<uuid:workspace_id>/", views.WorkspaceView.as_view(), name="workspace"),
    path("api/v1/workspaces/<uuid:workspace_id>/archive/", views.ArquivarView.as_view(), name="arquivar"),
    path("api/v1/workspaces/<uuid:workspace_id>/token/", views.TokenView.as_view(), name="token"),
    path("api/v1/workspaces/<uuid:workspace_id>/members/", views.MembrosView.as_view(), name="membros"),
    # Leitura de dados de domínio para os componentes ia-* (isolada por organização)
    path("api/v1/dominio/artefatos/", views_dominio.ArtefatosView.as_view(), name="dominio_artefatos"),
    path("api/v1/dominio/artefatos/<uuid:artifact_id>/", views_dominio.ArtefatoView.as_view(), name="dominio_artefato"),
    path("api/v1/dominio/artefatos/<uuid:artifact_id>/relacoes/", views_dominio.RelacoesView.as_view(), name="dominio_relacoes"),
]
