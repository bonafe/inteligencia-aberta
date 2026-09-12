"""Cliente de emissão de eventos para o log central do portal.

Cópia deliberada do cliente do orchestrator: os dois serviços não
compartilham código (cada um é uma imagem independente, ver ADR 002), e um
pacote comum por 80 linhas custaria mais do que resolve.

O portal é o dono do log; os demais serviços despacham por HTTP no mesmo canal
serviço-a-serviço já usado para criar artefatos (`X-Internal-Token`).

Duas regras, ambas deliberadas:

1. **Nunca levanta.** Uma falha de observabilidade não pode derrubar uma
   captura. Erro aqui vira `print` e segue.
2. **Não bloqueia a resposta.** O envio roda em uma thread curta; o cliente da
   extensão não espera pelo log.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone

import httpx

PORTAL_URL = os.getenv("PORTAL_URL", "http://portal:8000")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
INGEST_URL = f"{PORTAL_URL}/eventos/api/v1/ingest/"
TIMEOUT_S = 3.0

SOURCE = "mcp"


def nova_correlacao() -> str:
    """Id da unidade de trabalho — uma captura, do clique ao ponto no Qdrant."""
    return str(uuid.uuid4())


def emitir(
    stage: str,
    status: str,
    *,
    correlation_id: str,
    message: str = "",
    payload: dict | None = None,
    error: str = "",
    duration_ms: int | None = None,
    subject_type: str = "",
    subject_id: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    source: str = SOURCE,
    sincrono: bool = False,
) -> None:
    """Despacha um evento. Silencioso em caso de falha."""
    corpo = {
        "stage": stage,
        "status": status,
        "correlation_id": correlation_id,
        "source": source,
        "message": message,
        "payload": payload or {},
        "error": error,
        "duration_ms": duration_ms,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }

    if sincrono:
        _enviar(corpo)
        return
    threading.Thread(target=_enviar, args=(corpo,), daemon=True).start()


def _enviar(corpo: dict) -> None:
    try:
        httpx.post(
            INGEST_URL,
            json=corpo,
            headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            timeout=TIMEOUT_S,
        )
    except Exception as err:  # noqa: BLE001 — observabilidade nunca propaga erro
        print(f"[eventos] falha ao emitir '{corpo.get('stage')}': {err}")
