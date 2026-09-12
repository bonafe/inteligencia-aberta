from django.urls import path

from .consumers import EventosConsumer

websocket_urlpatterns = [
    path("ws/eventos/", EventosConsumer.as_asgi()),
]
