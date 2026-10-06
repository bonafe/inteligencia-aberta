from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("entrar/", views.EntrarView.as_view(), name="login"),
    path("sair/", LogoutView.as_view(), name="logout"),
    path("registro/", views.registro, name="registro"),
    path("membros/", views.membros, name="membros"),
    path("membros/<uuid:org_id>/", views.membros, name="membros"),
    path("", views.dashboard, name="dashboard"),
]
