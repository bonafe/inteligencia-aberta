"""Integração com o serviço `agora-sync` (tempo real): tokens curtos e aviso de mudança de papel.

O portal é o dono de autenticação e autorização (R-BE-2). O `agora-sync` só confia em tokens assinados por
aqui (HS256, segredo `AGORA_SYNC_SECRET`) e aplica o papel que eles carregam.
"""

import logging
import time

import jwt
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TOKEN_TTL_S = 300


class SyncNaoConfigurado(RuntimeError):
    """Não há segredo para assinar tokens: a colaboração em tempo real não está ligada."""


def emitir_token(user, workspace, papel: str) -> str:
    if not settings.AGORA_SYNC_SECRET:
        raise SyncNaoConfigurado("AGORA_SYNC_SECRET não definido")
    agora = int(time.time())
    return jwt.encode(
        {
            "sub": str(user.pk),
            "name": user.get_full_name() or user.get_username(),
            "workspace": str(workspace.pk),
            "role": papel,
            "iat": agora,
            "exp": agora + TOKEN_TTL_S,
        },
        settings.AGORA_SYNC_SECRET, algorithm="HS256",
    )


def avisar_papel(workspace, user, papel: str) -> bool:
    """Diz ao `agora-sync` que o papel de alguém mudou, para valer nas conexões já abertas (R-PERM-4).

    Melhor esforço: se o serviço estiver fora, o novo papel vale no próximo token (no máximo 5 minutos).
    """
    url, token = settings.AGORA_SYNC_ADMIN_URL, settings.AGORA_SYNC_ADMIN_TOKEN
    if not url or not token:
        return False
    try:
        resposta = requests.post(
            f"{url.rstrip('/')}/admin/role", timeout=2, headers={"Authorization": f"Bearer {token}"},
            json={"workspace": str(workspace.pk), "user": str(user.pk), "role": papel},
        )
        return resposta.ok
    except requests.RequestException as erro:
        logger.warning("agora-sync indisponível ao avisar mudança de papel: %s", erro)
        return False
