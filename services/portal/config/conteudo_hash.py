"""Hash de conteúdo no formato RFC 6920 (`ni:`) — ADR 010.

    ni:///sha-256;<base64url sem padding>

O nome do algoritmo viaja junto com o hash, para que uma troca futura não exija
reinterpretar valores antigos. Hoje só `sha-256` é emitido; `verificar` aceita
apenas o que sabe calcular e devolve False para o resto, nunca levanta.

Este arquivo tem cópia em `orchestrator/` (cada serviço tem o seu contexto de
build, como `segredos.py`): alterar um exige alterar o outro.
"""

import base64
import hashlib
import re

ALGORITMO = "sha-256"
PREFIXO = f"ni:///{ALGORITMO};"
#: SHA-256 = 32 bytes = 43 caracteres em base64url sem padding.
_PADRAO = re.compile(r"^ni:///sha-256;[A-Za-z0-9_-]{43}$")


def hash_ni(dados: bytes) -> str:
    resumo = hashlib.sha256(dados).digest()
    return PREFIXO + base64.urlsafe_b64encode(resumo).rstrip(b"=").decode("ascii")


def formato_valido(valor: object) -> bool:
    return isinstance(valor, str) and _PADRAO.match(valor) is not None


def verificar(dados: bytes, esperado: str) -> bool:
    return formato_valido(esperado) and hash_ni(dados) == esperado
