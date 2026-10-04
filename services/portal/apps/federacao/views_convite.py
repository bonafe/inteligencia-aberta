"""Endpoint público do enrolamento: `POST /federacao/convite/aceitar/`.

Autenticado só pela **assinatura** da instância que chama (o DID é autocertificante) e pelo
**token** do convite. Qualquer recusa responde o mesmo `400`, sem dizer o motivo: o código
fica no log local. Não é alcançável pela internet: o Caddy o bloqueia (só VPN/LAN).
"""

import json
import logging

from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.events.emit import emit

from . import canal

logger = logging.getLogger(__name__)

TAMANHO_MAXIMO = 8192


@method_decorator(csrf_exempt, name="dispatch")
class AceitarConviteView(View):
    http_method_names = ["post"]

    def post(self, request):
        from apps.cluster import pares

        recusa = JsonResponse({"erro": "convite inválido"}, status=400)
        try:
            if int(request.headers.get("Content-Length") or 0) > TAMANHO_MAXIMO:
                return recusa
        except ValueError:
            return recusa
        corpo = request.body[: TAMANHO_MAXIMO + 1]
        if len(corpo) > TAMANHO_MAXIMO:
            return recusa

        try:
            resposta = pares.receber_convite(corpo, request.get_full_path(), request.headers)
        except pares.ConviteRecusado as exc:
            logger.warning("convite de enrolamento recusado — %s", exc.codigo)
            emit("federacao.canal", "ignorado", source="portal", message="convite de enrolamento recusado",
                 payload={"rota": "convite/aceitar", "codigo": exc.codigo})
            return recusa

        corpo_resposta = json.dumps(resposta, separators=(",", ":")).encode()
        http = HttpResponse(corpo_resposta, content_type="application/json")
        http[canal.H_ASSINATURA_RESPOSTA] = canal.assinar_resposta(corpo_resposta, request.headers[canal.H_NONCE])
        return http
