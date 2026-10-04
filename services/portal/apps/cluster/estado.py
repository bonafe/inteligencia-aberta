"""O que esta instância diz de si mesma a um par próprio (`GET /federacao/controle/v1/estado`)."""

import os

from django.conf import settings

from .models import Maquina

VERSAO_PROTOCOLO = 1


def montar_estado_local() -> dict:
    """Capacidades, recursos e modelos desta instância, para um par **próprio**.

    Nunca expõe o endereço do Ollama nem segredos. O único endereço que sai é o do **gateway** que esta
    instância já anuncia aos pares (`LLM_GATEWAY_ENDPOINT_ANUNCIADO`), e só se o gateway estiver ligado.
    """
    maquina = Maquina.objects.filter(eh_local=True).select_related("status").first()
    status = getattr(maquina, "status", None) if maquina else None
    estado = {
        "v": VERSAO_PROTOCOLO,
        "nome": maquina.apelido if maquina else (os.environ.get("INSTANCIA_NOME") or ""),
        "versao": os.environ.get("IA_VERSION", ""),
        "ollama": {
            "configurado": bool(getattr(settings, "OLLAMA_HOST", "")),
            "disponivel": status.ollama_disponivel if status else None,
            "versao": status.ollama_versao if status else "",
        },
        "recursos": {},
        "modelos": [],
    }
    if getattr(settings, "LLM_GATEWAY_TOKEN", "") and getattr(settings, "LLM_GATEWAY_ENDPOINT_ANUNCIADO", ""):
        estado["gateway_endpoint"] = settings.LLM_GATEWAY_ENDPOINT_ANUNCIADO
    if maquina is None:
        return estado
    if status is not None:
        estado["recursos"] = {
            "cpu_percent": status.cpu_percent, "cpu_count": status.cpu_count,
            "ram_total_mb": status.ram_total_mb, "ram_disponivel_mb": status.ram_disponivel_mb,
            "disco_disponivel_gb": status.disco_disponivel_gb, "disco_ollama_livre_gb": status.disco_ollama_livre_gb,
            "ultimo_heartbeat_em": status.ultimo_heartbeat_em.isoformat() if status.ultimo_heartbeat_em else None,
        }
    estado["modelos"] = [
        {"nome": m.nome_modelo, "tamanho_bytes": m.tamanho_bytes, "digest": m.digest, "familia": m.familia,
         "parametros": m.parametros, "quantizacao": m.quantizacao, "carregado": m.carregado,
         "tokens_por_segundo": m.tokens_por_segundo_medio}
        for m in maquina.modelos_ollama.order_by("nome_modelo")
    ]
    return estado
