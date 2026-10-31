from django.urls import path
from . import views, views_schema

app_name = "artifacts"

urlpatterns = [
    path("gallery/", views.ArtifactGalleryView.as_view(), name="gallery"),
    path("busca/", views.BuscaSemanticaView.as_view(), name="busca"),
    path("<uuid:artifact_id>/mhtml/", views.ServeMHTMLView.as_view(), name="serve_mhtml"),
    path("<uuid:artifact_id>/content/", views.ArtifactContentView.as_view(), name="artifact_content"),
    path("<uuid:artifact_id>/favicon/", views.ArtifactFaviconView.as_view(), name="artifact_favicon"),
    path("<uuid:artifact_id>/estruturar-llm/", views.EstruturarLLMView.as_view(), name="estruturar_llm"),
    path("<uuid:artifact_id>/estruturacoes/", views.EstruturacoesListView.as_view(), name="estruturacoes_list"),
    path(
        "<uuid:artifact_id>/estruturacoes/<uuid:estruturacao_id>/cancelar/",
        views.EstruturacaoCancelarView.as_view(), name="estruturacao_cancelar",
    ),
    path("<uuid:artifact_id>/schema-pagina/", views_schema.GerarSchemaView.as_view(), name="gerar_schema"),
    path("<uuid:artifact_id>/schemas-pagina/", views_schema.SchemasListView.as_view(), name="schemas_list"),
    path(
        "<uuid:artifact_id>/schemas-pagina/<uuid:schema_id>/cancelar/",
        views_schema.SchemaCancelarView.as_view(), name="schema_cancelar",
    ),
    path("<uuid:artifact_id>/comparar/", views.CompararView.as_view(), name="comparar"),
    path("<uuid:artifact_id>/comparacoes/", views.ComparacoesListView.as_view(), name="comparacoes_list"),
    path(
        "<uuid:artifact_id>/comparacoes/<uuid:comparacao_id>/cancelar/",
        views.ComparacaoCancelarView.as_view(), name="comparacao_cancelar",
    ),
    path("api/v1/ollama/modelos/", views.OllamaModelosView.as_view(), name="ollama_modelos"),
    path("api/v1/artefatos/", views.ArtefatoCreateAPIView.as_view(), name="api_create"),
    path("mapa/", views.MapaVivoView.as_view(), name="mapa_vivo"),
    path("mapa/api/v1/grafo/", views.MapaVivoGrafoView.as_view(), name="mapa_vivo_grafo"),
]
