from django.urls import path
from . import views

app_name = "artifacts"

urlpatterns = [
    path("gallery/", views.ArtifactGalleryView.as_view(), name="gallery"),
    path("busca/", views.BuscaSemanticaView.as_view(), name="busca"),
    path("<uuid:artifact_id>/mhtml/", views.ServeMHTMLView.as_view(), name="serve_mhtml"),
    path("<uuid:artifact_id>/content/", views.ArtifactContentView.as_view(), name="artifact_content"),
    path("api/v1/artefatos/", views.ArtefatoCreateAPIView.as_view(), name="api_create"),
]
