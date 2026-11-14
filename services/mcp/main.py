import asyncio
import hmac
import os
import time
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from eventos import emitir, nova_correlacao
from ferramentas import ParametroInvalido, bloqueio_por_politica, carregar, validar_parametros
from segredos import validar_segredos
from tools import EMBUTIDAS

validar_segredos(["MCP_API_TOKEN", "INTERNAL_API_TOKEN"])

app = FastAPI(title="MCP — Ferramentas Inteligência Aberta")

# O catálogo: as ferramentas do projeto + as de plugins listados em FERRAMENTAS_MCP (ADR 023).
FERRAMENTAS = carregar(EMBUTIDAS, os.getenv("FERRAMENTAS_MCP", ""))

# Segredo do canal de chamada das ferramentas — distinto do INTERNAL_API_TOKEN
# (que é do canal orchestrator→portal). A porta do MCP é publicada no host para
# que /docs fique visível para fins didáticos; as ferramentas em si continuam
# exigindo esse token, então expor a documentação não expõe as chamadas reais.
MCP_API_TOKEN = os.getenv("MCP_API_TOKEN", "")


def require_mcp_token(x_mcp_token: str = Header(None)) -> None:
    if not MCP_API_TOKEN or not x_mcp_token or not hmac.compare_digest(x_mcp_token, MCP_API_TOKEN):
        raise HTTPException(status_code=401, detail="Token de ferramenta ausente ou inválido")


def _correlacao(request: Request) -> str:
    return request.headers.get("X-Correlation-Id") or nova_correlacao()


class Contexto(BaseModel):
    classificacao: str | None = None      # nível do dado enviado; ausente = confidencial (o mais restrito)
    tenant_id: str | None = None
    investigacao_id: str | None = None
    user_id: str | None = None


class Chamada(BaseModel):
    parametros: dict = {}
    contexto: Contexto = Contexto()


def _erro(status: int, codigo: str, mensagem: str, recuperavel: bool = False) -> JSONResponse:
    return JSONResponse({"status": "erro", "codigo": codigo, "mensagem": mensagem, "recuperavel": recuperavel},
                        status_code=status)


@app.get("/ferramentas", dependencies=[Depends(require_mcp_token)])
async def listar_ferramentas():
    """O catálogo, com o esquema de cada ferramenta (o que o orchestrator e os agentes descobrem em runtime)."""
    return {"ferramentas": [f.esquema() for f in FERRAMENTAS.values()]}


@app.post("/ferramentas/{ferramenta_id}", dependencies=[Depends(require_mcp_token)])
async def chamar_ferramenta(ferramenta_id: str, chamada: Chamada, request: Request):
    """Contrato de docs/componentes/mcp.md: valida, aplica a política da ferramenta, executa e registra."""
    correlacao = _correlacao(request)
    contexto = chamada.contexto
    ferramenta = FERRAMENTAS.get(ferramenta_id)
    if ferramenta is None:
        return _erro(404, "FERRAMENTA_DESCONHECIDA", f"ferramenta não registrada: {ferramenta_id}")
    base = {"ferramenta": ferramenta.id, "classificacao": contexto.classificacao or "ausente"}
    try:
        parametros = validar_parametros(ferramenta, chamada.parametros)
    except ParametroInvalido as erro:
        return _erro(422, "PARAMETRO_INVALIDO", str(erro))
    motivo = bloqueio_por_politica(ferramenta, contexto.classificacao)
    if motivo:
        emitir("ferramenta.chamada", "ignorado", correlation_id=correlacao, tenant_id=contexto.tenant_id,
               user_id=contexto.user_id, message=f"{ferramenta.id} bloqueada por política: {motivo}",
               payload={**base, "decisao": "BLOQUEADO"})
        return _erro(403, "BLOQUEADO_POR_POLITICA", motivo)
    t0 = time.perf_counter()
    try:
        resultado = await asyncio.wait_for(ferramenta.executar(**parametros), timeout=ferramenta.timeout_s)
    except asyncio.TimeoutError:
        codigo, status, mensagem = "TIMEOUT", 504, f"a ferramenta passou de {ferramenta.timeout_s:g} s"
    except Exception as err:  # noqa: BLE001 — qualquer falha da fonte vira erro do contrato, nunca 500 cru
        codigo, status, mensagem = "FONTE_INDISPONIVEL", 502, f"{type(err).__name__}: {err}"
    else:
        emitir("ferramenta.chamada", "ok", correlation_id=correlacao, tenant_id=contexto.tenant_id,
               user_id=contexto.user_id, message=f"{ferramenta.id} respondeu", payload=base,
               duration_ms=int((time.perf_counter() - t0) * 1000))
        return {"status": "ok", "resultado": resultado,
                "fonte": {"origem": ferramenta.fonte, "data_coleta": datetime.now(timezone.utc).isoformat()},
                "confianca": ferramenta.confianca}
    emitir("ferramenta.chamada", "falhou", correlation_id=correlacao, tenant_id=contexto.tenant_id,
           user_id=contexto.user_id, message=f"{ferramenta.id} falhou: {mensagem}", payload={**base, "codigo": codigo},
           error=mensagem, duration_ms=int((time.perf_counter() - t0) * 1000))
    return _erro(status, codigo, mensagem, recuperavel=True)


def _versao_do_build() -> str:
    """Commit do build, gravado na imagem pelo Dockerfile (não vem do ambiente)."""
    try:
        with open("/etc/ia-version") as f:
            return f.read().strip()
    except OSError:
        return ""


IA_VERSION = _versao_do_build()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "servico": "mcp",
        "instancia": os.getenv("INSTANCIA_NOME", ""),
        "versao": IA_VERSION,
    }
