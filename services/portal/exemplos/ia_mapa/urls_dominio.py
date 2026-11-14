from django.urls import path

from . import views

urlpatterns = [path("pontos/", views.pontos, name="pontos")]
