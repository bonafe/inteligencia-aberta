"""Cliente do MCP para o orchestrator (ADR 023, fase 4): descobre as ferramentas e chama uma com a política.

O MCP é a única ponte para o que está fora (docs/componentes/mcp.md). Duas camadas de política, nenhuma
dispensa a outra:

1. aqui, **antes** de sair: `policy_engine.check` (tenant e classificação válidos), decisão registrada
   no log de eventos. O `policy_engine` em si não muda;
2. no MCP: cada ferramenta declara o dado mais restrito que aceita (`classificacao_maxima`) e o MCP recusa o
   que passar disso — quem chama não precisa lembrar.

`chamar` **nunca levanta**: devolve sempre o contrato do MCP (`{"status": "ok"|"erro", ...}`), para que uma
fonte fora do ar não derrube a investigação (o coletor decide o que fazer com o erro).
"""
from __future__ import annotations

import os

import httpx

from eventos import emitir
from policy_engine import check, registrar_decisao

MCP_URL = os.getenv("MCP_URL", "http://mcp:8002").rstrip("/")
TIMEOUT_S = float(os.getenv("MCP_TIMEOUT_S", "30"))

#: Do código de erro do contrato ao status HTTP que o orchestrator devolve quando repassa a resposta.
STATUS_HTTP = {"BLOQUEADO_POR_POLITICA": 403, "PARAMETRO_INVALIDO": 422, "FERRAMENTA_DESCONHECIDA": 404,
               "TIMEOUT": 504, "FONTE_INDISPONIVEL": 502, "MCP_INDISPONIVEL": 502}


def _cabecalhos(correlacao: str | None = None) -> dict:
    # Lido a cada chamada (e não ao importar): o token pode mudar sem reimportar o módulo.
    cabecalhos = {"X-Mcp-Token": os.getenv("MCP_API_TOKEN", "")}
    if correlacao:
        cabecalhos["X-Correlation-Id"] = correlacao
    return cabecalhos


def _erro(codigo: str, mensagem: str, recuperavel: bool = False) -> dict:
    return {"status": "erro", "codigo": codigo, "mensagem": mensagem, "recuperavel": recuperavel}


async def catalogo(cliente: httpx.AsyncClient | None = None) -> list[dict]:
    """As ferramentas que o MCP oferece agora, com o esquema de cada uma. Levanta se o MCP não responde."""
    async with _cliente(cliente) as c:
        resposta = await c.get(f"{MCP_URL}/ferramentas", headers=_cabecalhos())
        resposta.raise_for_status()
        return resposta.json()["ferramentas"]


async def chamar(ferramenta: str, parametros: dict, *, classificacao: str, tenant_id: str, correlacao: str,
                 user_id: str | None = None, investigacao_id: str | None = None,
                 cliente: httpx.AsyncClient | None = None) -> dict:
    decisao = check(operation="chamar_ferramenta", classification=classificacao,
                    tenant_id=tenant_id, requesting_tenant=tenant_id)
    registrar_decisao(decisao, operation="chamar_ferramenta", classification=classificacao, tenant_id=tenant_id,
                      requesting_tenant=tenant_id, correlation_id=correlacao, user_id=user_id)
    if decisao["decision"] == "BLOQUEADO":
        return _erro("BLOQUEADO_POR_POLITICA", decisao["reason"])
    corpo = {"parametros": parametros, "contexto": {"classificacao": classificacao, "tenant_id": tenant_id,
                                                      "user_id": user_id, "investigacao_id": investigacao_id}}
    try:
        async with _cliente(cliente) as c:
            resposta = await c.post(f"{MCP_URL}/ferramentas/{ferramenta}", json=corpo, headers=_cabecalhos(correlacao))
    except httpx.HTTPError as erro:
        emitir("ferramenta.chamada", "falhou", correlation_id=correlacao, tenant_id=tenant_id,
               message=f"MCP indisponível ao chamar {ferramenta}: {erro}", payload={"ferramenta": ferramenta},
               error=str(erro))
        return _erro("MCP_INDISPONIVEL", f"o MCP não respondeu: {type(erro).__name__}", recuperavel=True)
    try:
        corpo_resposta = resposta.json()
    except ValueError:
        return _erro("MCP_INDISPONIVEL", f"resposta inesperada do MCP (HTTP {resposta.status_code})", recuperavel=True)
    if resposta.status_code == 401:
        return _erro("MCP_INDISPONIVEL", "o MCP recusou o token do orchestrator (MCP_API_TOKEN)")
    return corpo_resposta if isinstance(corpo_resposta, dict) and "status" in corpo_resposta else _erro(
        "MCP_INDISPONIVEL", f"resposta fora do contrato (HTTP {resposta.status_code})")


class _cliente:
    """`async with` sobre o cliente dado (sem fechá-lo) ou sobre um novo."""

    def __init__(self, cliente):
        self._dado = cliente
        self._proprio = None

    async def __aenter__(self):
        if self._dado is not None:
            return self._dado
        self._proprio = httpx.AsyncClient(timeout=TIMEOUT_S)
        return await self._proprio.__aenter__()

    async def __aexit__(self, *exc):
        if self._proprio is not None:
            await self._proprio.__aexit__(*exc)
