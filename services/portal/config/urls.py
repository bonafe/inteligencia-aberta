from django.contrib import admin
from django.urls import include, path
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
    path("", include("apps.accounts.urls")),
    path("artifacts/", include("apps.artifacts.urls")),
]
