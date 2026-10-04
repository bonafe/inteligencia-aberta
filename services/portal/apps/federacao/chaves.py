"""Geração, uso e verificação da chave Ed25519 da instância — ADR 010."""

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from django.db import IntegrityError, transaction

from apps.infrastructure.crypto import cifrar, decifrar

from .did import did_key_de_publica, publica_de_did_key
from .models import ChaveInstancia


def _publica_bruta(privada: Ed25519PrivateKey) -> bytes:
    return privada.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
    )


def _semente_bruta(privada: Ed25519PrivateKey) -> bytes:
    return privada.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def chave_ativa() -> ChaveInstancia | None:
    return ChaveInstancia.objects.filter(estado=ChaveInstancia.Estado.ATIVA).first()


def garantir_chave_ativa() -> tuple[ChaveInstancia, bool]:
    """Devolve a chave ativa, criando-a se não houver. `(chave, criada)`.

    Idempotente e seguro sob concorrência: a restrição de uma-ativa-por-instância
    faz o perdedor de uma corrida receber `IntegrityError` e reler a vencedora.
    """
    existente = chave_ativa()
    if existente:
        return existente, False

    privada = Ed25519PrivateKey.generate()
    try:
        with transaction.atomic():
            chave = ChaveInstancia.objects.create(
                did=did_key_de_publica(_publica_bruta(privada)),
                privada_cifrada=cifrar(base64.urlsafe_b64encode(_semente_bruta(privada)).decode()),
            )
    except IntegrityError:
        return chave_ativa(), False
    return chave, True


def assinar_com(chave: ChaveInstancia, mensagem: bytes) -> bytes:
    semente = base64.urlsafe_b64decode(decifrar(chave.privada_cifrada))
    return Ed25519PrivateKey.from_private_bytes(semente).sign(mensagem)


def verificar_assinatura(did: str, mensagem: bytes, assinatura: bytes) -> bool:
    """Confere uma assinatura só com o `did:key` do autor — sem banco, sem rede.

    Nunca levanta: DID malformado, assinatura de tamanho errado ou adulterada
    dão `False`, para o chamador tratar tudo como "não confere".
    """
    try:
        Ed25519PublicKey.from_public_bytes(publica_de_did_key(did)).verify(assinatura, mensagem)
    except (ValueError, InvalidSignature):
        return False
    return True
