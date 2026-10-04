"""Referências tipadas usadas por `Claim` (sujeito, objeto e autor) — ADR 010.

Como as entidades ainda não existem como objetos, uma alegação aponta para uma
**referência com tipo**, no formato `<tipo>:<valor>`. A resolução de entidades e
o `sameAs` ficam para depois: quem correlacionar vai ligar referências
diferentes ao mesmo `Artifact`.

| Tipo | Valor | Para quê |
|---|---|---|
| `urn:uuid:` | UUID canônico | uma entidade (`Artifact`) já existente |
| `cnpj:` | 14 dígitos com DV válido | empresa |
| `url:` | URL http(s) | página, perfil ou documento |
| `dominio:` | domínio normalizado | autor que é um site |
| `mencao:` | `ni:…#localizador` | algo citado numa captura, sem identificador estável |
"""

import re
from urllib.parse import urlsplit

from apps.federacao.ids import normalizar as normalizar_urn
from config.conteudo_hash import formato_valido as hash_valido

from .classificacao_dominio import normalizar_dominio

TAMANHO_MAXIMO = 500


def cnpj_valido(digitos: str) -> bool:
    """14 dígitos, não todos iguais, com os dois dígitos verificadores corretos."""
    if not (isinstance(digitos, str) and len(digitos) == 14 and digitos.isdigit()):
        return False
    if len(set(digitos)) == 1:
        return False

    def dv(base: str, pesos: list[int]) -> int:
        resto = sum(int(d) * p for d, p in zip(base, pesos)) % 11
        return 0 if resto < 2 else 11 - resto

    d1 = dv(digitos[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = dv(digitos[:12] + str(d1), [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return digitos[12:] == f"{d1}{d2}"


def so_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def normalizar_referencia(ref: str) -> str:
    """Forma canônica da referência. `ValueError` se não for uma referência válida."""
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("referência vazia")
    ref = ref.strip()
    if len(ref) > TAMANHO_MAXIMO:
        raise ValueError("referência longa demais")

    if ref.lower().startswith("urn:uuid:"):
        return normalizar_urn(ref)

    tipo, _, valor = ref.partition(":")
    tipo = tipo.lower()
    if tipo == "cnpj":
        digitos = so_digitos(valor)
        if not cnpj_valido(digitos):
            raise ValueError("CNPJ inválido")
        return f"cnpj:{digitos}"
    if tipo == "url":
        partes = urlsplit(valor)
        if partes.scheme not in ("http", "https") or not partes.netloc:
            raise ValueError("url: exige uma URL http(s)")
        return f"url:{valor}"
    if tipo == "dominio":
        return f"dominio:{normalizar_dominio(valor)}"
    if tipo == "mencao":
        blob, sep, localizador = valor.partition("#")
        if not sep or not localizador or not hash_valido(blob):
            raise ValueError("mencao: exige `ni:///sha-256;…#localizador`")
        return f"mencao:{blob}#{localizador}"
    raise ValueError(f"tipo de referência desconhecido: {tipo!r}")
