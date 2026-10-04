"""`did:key` para chaves Ed25519 — ADR 010.

    did:key:z<base58btc( 0xed 0x01 || chave pública de 32 bytes )>

O identificador **contém** a chave pública, então qualquer par verifica uma
assinatura só com o DID, sem consultar nada. Sem dependências além da stdlib:
são 40 linhas de base58 e não vale puxar uma biblioteca por isso.
"""

_ALFABETO = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDICE = {c: i for i, c in enumerate(_ALFABETO)}
#: multicodec `ed25519-pub` (0xed), varint de dois bytes.
_MULTICODEC_ED25519 = b"\xed\x01"
PREFIXO = "did:key:z"


def _b58_codificar(dados: bytes) -> str:
    n = int.from_bytes(dados, "big")
    saida = ""
    while n:
        n, resto = divmod(n, 58)
        saida = _ALFABETO[resto] + saida
    zeros = len(dados) - len(dados.lstrip(b"\x00"))
    return "1" * zeros + saida


def _b58_decodificar(texto: str) -> bytes:
    n = 0
    for c in texto:
        if c not in _INDICE:
            raise ValueError("caractere fora do alfabeto base58btc")
        n = n * 58 + _INDICE[c]
    zeros = len(texto) - len(texto.lstrip("1"))
    corpo = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * zeros + corpo


def did_key_de_publica(publica: bytes) -> str:
    if len(publica) != 32:
        raise ValueError("chave pública Ed25519 tem 32 bytes")
    return PREFIXO + _b58_codificar(_MULTICODEC_ED25519 + publica)


def publica_de_did_key(did: str) -> bytes:
    """Extrai a chave pública de um `did:key` Ed25519. `ValueError` se não for um."""
    if not isinstance(did, str) or not did.startswith(PREFIXO):
        raise ValueError("não é um did:key multibase base58btc")
    bruto = _b58_decodificar(did[len(PREFIXO):])
    if not bruto.startswith(_MULTICODEC_ED25519) or len(bruto) != 34:
        raise ValueError("did:key não é uma chave Ed25519")
    return bruto[2:]
