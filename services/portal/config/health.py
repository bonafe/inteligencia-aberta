import os

from django.db import connection
from django.http import JsonResponse


def health(request):
    """Prontidão do portal, para healthcheck do compose e para a automação de deploy.

    Sem autenticação e sem dado de negócio: só diz se o portal responde e alcança
    o banco, e qual instância/versão está rodando. 503 quando o banco não responde.
    """
    corpo = {
        "status": "ok",
        "servico": "portal",
        "instancia": os.environ.get("INSTANCIA_NOME", ""),
        "versao": os.environ.get("IA_VERSION", ""),
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        corpo["status"] = "falhou"
        corpo["erro"] = "banco indisponível"
        return JsonResponse(corpo, status=503)
    return JsonResponse(corpo)
