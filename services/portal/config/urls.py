from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.accounts.serializers import TenantTokenObtainPairSerializer

urlpatterns = [
    path("admin/", admin.site.urls),
    # Emissão de JWT para clientes externos (extensão Chrome). O access token
    # carrega tenant_id/username nas claims (ver TenantTokenObtainPairSerializer).
    path(
        "api/v1/token/",
        TokenObtainPairView.as_view(serializer_class=TenantTokenObtainPairSerializer),
        name="token_obtain_pair",
    ),
    path("api/v1/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # Documentação da API (drf-spectacular) — pública por design, ver SPECTACULAR_SETTINGS.
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("", include("apps.accounts.urls")),
    path("artifacts/", include("apps.artifacts.urls")),
]
