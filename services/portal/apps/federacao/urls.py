from django.urls import path

from . import views_convite

app_name = "federacao"

urlpatterns = [
    # Canal entre instâncias: autenticado pela assinatura, não pela sessão (ver middleware).
    path("convite/aceitar/", views_convite.AceitarConviteView.as_view(), name="convite_aceitar"),
]
