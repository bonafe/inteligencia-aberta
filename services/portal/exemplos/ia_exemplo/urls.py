from django.http import HttpResponse
from django.urls import path

app_name = "ia_exemplo"


def inicio(request):
    return HttpResponse("Extensão de exemplo ativa.")


urlpatterns = [path("", inicio, name="inicio")]
