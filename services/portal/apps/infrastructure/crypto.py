"""Cifra em repouso das chaves de API cadastradas por organização.

Fernet (AES-128-CBC + HMAC). A chave vem de `FIELD_ENCRYPTION_KEY`; sem ela,
é derivada de `SECRET_KEY` (sha256 → base64 urlsafe). Nunca registre nem
devolva o valor em claro fora de `LLMProvider.get_api_key()`.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class ChaveIlegivel(Exception):
    """O valor guardado não decifra com a chave atual (chave de cifra trocada
    ou dado corrompido). A chave de API precisa ser cadastrada de novo."""


def _fernet() -> Fernet:
    chave = getattr(settings, "FIELD_ENCRYPTION_KEY", "")
    if not chave:
        chave = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()
    return Fernet(chave.encode())


def cifrar(texto: str) -> str:
    return _fernet().encrypt(texto.encode()).decode()


def decifrar(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise ChaveIlegivel("chave de API não pôde ser decifrada") from exc
