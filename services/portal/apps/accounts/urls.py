from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("entrar/", LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("sair/", LogoutView.as_view(), name="logout"),
    path("registro/", views.registro, name="registro"),
    path("", views.dashboard, name="dashboard"),
]
