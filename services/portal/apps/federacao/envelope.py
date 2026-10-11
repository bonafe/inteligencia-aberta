"""Envelope assinado da federação — ADR 010.

    {v, id, space, actor, author, type, prev, at, must_understand, payload, sig}

- `id` = `sha256:<hex>` dos bytes canônicos do envelope **sem** `id` e `sig`;
- `sig` = `ed25519:<base64url>` sobre os bytes canônicos do envelope **com** `id`, sem `sig`;
- `author` é o `did:key` que assina (hoje o da instância; o chaveiro por usuário vem na F4).

**Bytes canônicos:** JSON com chaves ordenadas, sem espaços e UTF-8 — um subconjunto do
JCS (RFC 8785) que coincide com ele enquanto o envelope **não tiver números de ponto
flutuante** (por isso as confianças viajam como texto). Quem emite e quem verifica usam
esta mesma função; uma implementação em outra linguagem deve seguir o JCS completo.

`verificar` **nunca confia** em nada do envelope antes de conferir: id recalculado,
assinatura contra o `did:key` do próprio autor e `must_understand` (falha fechada).
"""

import base64
import hashlib
import json
from datetime import datetime, timezone

from .chaves import assinar_com, verificar_assinatura
from .ids import eh_urn_valida

VERSAO = 1
CAMPOS = {"v", "id", "space", "actor", "author", "type", "prev", "at", "must_understand", "payload", "sig"}
TAMANHO_MAXIMO = 8 * 1024 * 1024  # um envelope; o texto extraído de uma página cabe com folga


class ErroEnvelope(ValueError):
    """Envelope malformado, adulterado ou que esta instância não sabe aplicar."""


def canonico(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


def _des_b64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _id_de(corpo: dict) -> str:
    return "sha256:" + hashlib.sha256(canonico(corpo)).hexdigest()


def criar(chave, *, espaco_urn: str, tipo: str, payload: dict, prev: str | None, agora: datetime | None = None) -> dict:
    """Monta e assina um envelope com a chave (`ChaveInstancia`) da instância."""
    if not eh_urn_valida(espaco_urn):
        raise ErroEnvelope("espaço inválido")
    corpo = {
        "v": VERSAO,
        "space": espaco_urn,
        "actor": chave.did,
        "author": chave.did,
        "type": tipo,
        "prev": prev,
        "at": (agora or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "must_understand": [],
        "payload": payload,
    }
    corpo["id"] = _id_de(corpo)
    corpo["sig"] = "ed25519:" + _b64(assinar_com(chave, canonico(corpo)))
    return corpo


def verificar(bruto: bytes | str) -> dict:
    """Confere o envelope e devolve o dicionário. `ErroEnvelope` para qualquer problema."""
    if isinstance(bruto, str):
        bruto = bruto.encode("utf-8")
    if len(bruto) > TAMANHO_MAXIMO:
        raise ErroEnvelope("envelope grande demais")
    try:
        env = json.loads(bruto)
    except (ValueError, UnicodeDecodeError):
        raise ErroEnvelope("não é JSON") from None
    if not isinstance(env, dict) or set(env) != CAMPOS:
        raise ErroEnvelope("campos do envelope diferentes do esperado")
    if env["v"] != VERSAO:
        raise ErroEnvelope("versão do envelope desconhecida")
    if env["must_understand"]:
        # Falha fechada: o emissor exige algo que esta instância não entende.
        raise ErroEnvelope("o envelope exige entendimento de extensões que esta instância não tem")
    for campo in ("id", "space", "actor", "author", "type", "at", "sig"):
        if not isinstance(env[campo], str) or not env[campo]:
            raise ErroEnvelope(f"campo {campo} inválido")
    if env["prev"] is not None and not isinstance(env["prev"], str):
        raise ErroEnvelope("campo prev inválido")
    if not isinstance(env["payload"], dict):
        raise ErroEnvelope("payload inválido")
    if not eh_urn_valida(env["space"]):
        raise ErroEnvelope("espaço inválido")
    corpo = {k: v for k, v in env.items() if k not in ("id", "sig")}
    if _id_de(corpo) != env["id"]:
        raise ErroEnvelope("o id não confere com o conteúdo")
    if not env["sig"].startswith("ed25519:"):
        raise ErroEnvelope("assinatura de tipo desconhecido")
    try:
        assinatura = _des_b64(env["sig"][len("ed25519:"):])
    except ValueError:
        raise ErroEnvelope("assinatura malformada") from None
    sem_sig = {k: v for k, v in env.items() if k != "sig"}
    if not verificar_assinatura(env["author"], canonico(sem_sig), assinatura):
        raise ErroEnvelope("assinatura inválida")
    return env
