"""Auxiliares de teste: uma 'outra instância' com chave própria (a local é a `ChaveInstancia` do banco)."""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.federacao.did import did_key_de_publica


class ChaveOutraInstancia:
    def __init__(self):
        self._privada = Ed25519PrivateKey.generate()
        publica = self._privada.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        self.did = did_key_de_publica(publica)

    def assinar(self, mensagem: bytes) -> bytes:
        return self._privada.sign(mensagem)
