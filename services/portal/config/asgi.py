"""Ponto de entrada ASGI — HTTP e WebSocket.

`setdefault` (e não atribuição direta) porque o módulo de settings vem do
ambiente: o docker-compose de desenvolvimento roda com
`DJANGO_SETTINGS_MODULE=config.settings.development`, e fixar `production` aqui
fazia o processo ASGI subir com as settings erradas.

`get_asgi_application()` é chamado antes de importar o routing porque o
`AuthMiddlewareStack` toca no app registry, que precisa estar carregado.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from apps.events.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # A sessão do Django autentica o WebSocket; o consumer recusa anônimo e
    # restringe os grupos às organizações do usuário.
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
    ),
})
