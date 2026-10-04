"""Requisições assinadas entre instâncias — ADR 011, canal de controle.

Cada requisição entre instâncias leva a assinatura Ed25519 da instância de origem
(`ChaveInstancia`), feita sobre uma mensagem canônica que cobre **método, rota,
corpo, janela de tempo, nonce e destinatário**:

    ia-ctrl-v1 ⏎ MÉTODO ⏎ caminho+query ⏎ timestamp ⏎ nonce ⏎ DID_origem ⏎ DID_destino ⏎ sha256(corpo)

- o **destino** assinado impede reaproveitar uma requisição de A→B contra C;
- o **timestamp** (±`FEDERACAO_JANELA_RELOGIO_S`) e o **nonce** (guardado no cache
  compartilhado) impedem o replay;
- o prefixo de **versão** impede confusão com assinaturas de outros contextos.

Este módulo só **verifica a assinatura** (o DID de origem é autocertificante: contém a
chave pública). Saber se aquele DID é um par confirmado, de que tipo, e se pode fazer o
que pede é a camada de cima (`views_controle`). O motivo de uma rejeição fica em
`AssinaturaInvalida.codigo`, para o log local; **nunca** vai para quem chamou.
"""

import base64
import hashlib
import secrets
import time

from django.conf import settings
from django.core.cache import cache

from .chaves import chave_ativa, verificar_assinatura
from .did import publica_de_did_key

VERSAO = "ia-ctrl-v1"
VERSAO_RESPOSTA = "ia-ctrl-v1-resp"

H_DID = "X-IA-DID"
H_TIMESTAMP = "X-IA-Timestamp"
H_NONCE = "X-IA-Nonce"
H_DESTINO = "X-IA-Destino"
H_ASSINATURA = "X-IA-Assinatura"
H_ASSINATURA_RESPOSTA = "X-IA-Assinatura-Resposta"

_PREFIXO_NONCE = "ia-nonce:"


class AssinaturaInvalida(Exception):
    """A requisição (ou resposta) não passou na verificação. `codigo` diz por quê, só para o log."""

    def __init__(self, codigo: str):
        super().__init__(codigo)
        self.codigo = codigo


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


def _b64_decodifica(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def mensagem_assinada(metodo: str, caminho_e_query: str, timestamp: int | str, nonce: str,
                      did_origem: str, did_destino: str, corpo: bytes) -> bytes:
    partes = [VERSAO, metodo.upper(), caminho_e_query, str(timestamp), nonce, did_origem, did_destino,
              hashlib.sha256(corpo or b"").hexdigest()]
    return "\n".join(partes).encode("utf-8")


def assinar_requisicao(metodo: str, caminho_e_query: str, corpo: bytes, did_destino: str, *,
                       chave=None, agora: float | None = None, nonce: str | None = None) -> dict:
    """Cabeçalhos de uma requisição assinada pela instância local (ou por `chave`, nos testes)."""
    chave = chave or chave_ativa()
    if chave is None:
        raise RuntimeError("a instância não tem chave ativa (rode `manage.py chave_instancia --criar`)")
    timestamp = int(agora if agora is not None else time.time())
    nonce = nonce or secrets.token_urlsafe(16)
    assinatura = chave.assinar(mensagem_assinada(metodo, caminho_e_query, timestamp, nonce, chave.did, did_destino, corpo))
    return {H_DID: chave.did, H_TIMESTAMP: str(timestamp), H_NONCE: nonce, H_DESTINO: did_destino,
            H_ASSINATURA: _b64(assinatura)}


def _cabecalho(headers, nome: str) -> str:
    valor = headers.get(nome)
    if valor is None:  # dict comum, não case-insensitive
        valor = next((v for k, v in headers.items() if k.lower() == nome.lower()), None)
    return (valor or "").strip()


def verificar_requisicao(metodo: str, caminho_e_query: str, headers, corpo: bytes, *, did_local: str,
                         agora: float | None = None) -> str:
    """Verifica a assinatura e devolve o `did:key` de origem. `AssinaturaInvalida` se algo falhar.

    Ordem: do mais barato ao mais caro — cabeçalhos, janela, destino, assinatura e, por último,
    o nonce (que escreve no cache; só queima um nonce de requisição **autêntica**).
    """
    did_origem = _cabecalho(headers, H_DID)
    timestamp = _cabecalho(headers, H_TIMESTAMP)
    nonce = _cabecalho(headers, H_NONCE)
    destino = _cabecalho(headers, H_DESTINO)
    assinatura_b64 = _cabecalho(headers, H_ASSINATURA)
    if not (did_origem and timestamp and nonce and destino and assinatura_b64) or len(nonce) > 128:
        raise AssinaturaInvalida("cabecalhos")

    try:
        ts = int(timestamp)
    except ValueError:
        raise AssinaturaInvalida("timestamp") from None
    janela = int(getattr(settings, "FEDERACAO_JANELA_RELOGIO_S", 60))
    if abs((agora if agora is not None else time.time()) - ts) > janela:
        raise AssinaturaInvalida("janela")

    if destino != did_local:
        raise AssinaturaInvalida("destino")

    try:
        publica_de_did_key(did_origem)
        assinatura = _b64_decodifica(assinatura_b64)
    except (ValueError, TypeError):
        raise AssinaturaInvalida("did") from None
    mensagem = mensagem_assinada(metodo, caminho_e_query, ts, nonce, did_origem, destino, corpo)
    if not verificar_assinatura(did_origem, mensagem, assinatura):
        raise AssinaturaInvalida("assinatura")

    # `add` só grava se a chave não existe — atômico no Redis. Se já existia, é replay.
    if not cache.add(f"{_PREFIXO_NONCE}{did_origem}:{nonce}", 1, timeout=2 * janela + 30):
        raise AssinaturaInvalida("replay")
    return did_origem


def _mensagem_resposta(nonce_requisicao: str, did_resposta: str, corpo: bytes) -> bytes:
    return "\n".join([VERSAO_RESPOSTA, nonce_requisicao, did_resposta, hashlib.sha256(corpo or b"").hexdigest()]).encode()


def assinar_resposta(corpo: bytes, nonce_requisicao: str, *, chave=None) -> str:
    """Assinatura (base64url) da resposta, vinculada ao nonce da requisição que ela responde."""
    chave = chave or chave_ativa()
    return _b64(chave.assinar(_mensagem_resposta(nonce_requisicao, chave.did, corpo)))


def verificar_resposta(corpo: bytes, nonce_requisicao: str, did_esperado: str, assinatura_b64: str) -> None:
    """`AssinaturaInvalida` se a resposta não foi assinada por `did_esperado` para este nonce."""
    try:
        assinatura = _b64_decodifica((assinatura_b64 or "").strip())
    except (ValueError, TypeError):
        raise AssinaturaInvalida("resposta") from None
    if not verificar_assinatura(did_esperado, _mensagem_resposta(nonce_requisicao, did_esperado, corpo), assinatura):
        raise AssinaturaInvalida("resposta")
