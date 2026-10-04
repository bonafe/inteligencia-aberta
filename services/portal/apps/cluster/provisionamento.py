"""Criação de `Maquina` — usada por `manage.py registrar_maquina` (registro
manual de um peer nesta instância). Gera o token e guarda só o hash.
"""

import hashlib
import secrets

from django.contrib.auth import get_user_model

from apps.accounts.models import Organization

from .models import Maquina


class ProvisionamentoError(Exception):
    """Organização ou usuário informado não existe."""


def criar_maquina(
    *, apelido: str, organizacao_slug: str,
    dono_username: str | None = None, hostname: str = "", ollama_endpoint: str = "",
    gateway_endpoint: str = "",
) -> tuple[Maquina, str]:
    """Cria a `Maquina` e devolve `(maquina, token_em_claro)`.

    O token em claro só existe neste retorno — só o hash
    (`Maquina.token_hash`) é persistido. Levanta `ProvisionamentoError` se a
    organização ou o usuário informado não existirem.
    """
    try:
        organizacao = Organization.objects.get(slug=organizacao_slug)
    except Organization.DoesNotExist:
        raise ProvisionamentoError(f"Organização '{organizacao_slug}' não encontrada.")

    if dono_username:
        User = get_user_model()
        try:
            dono = User.objects.get(username=dono_username)
        except User.DoesNotExist:
            raise ProvisionamentoError(f"Usuário '{dono_username}' não encontrado.")
    else:
        dono = organizacao.owner

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    maquina = Maquina.objects.create(
        apelido=apelido,
        hostname_declarado=hostname,
        organizacao=organizacao,
        dono=dono,
        token_hash=token_hash,
        ollama_endpoint=ollama_endpoint,
        gateway_endpoint=gateway_endpoint,
    )
    return maquina, token
