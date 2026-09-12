import hmac
import os
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from eventos import emitir, nova_correlacao
from tools.cnpj import consultar_cnpj
from tools.processos import buscar_processos
from tools.noticias import buscar_noticias

app = FastAPI(title="MCP — Ferramentas Inteligência Aberta")

# Segredo do canal de chamada das ferramentas — distinto do INTERNAL_API_TOKEN
# (que é do canal orchestrator→portal). A porta do MCP é publicada no host para
# que /docs fique visível para fins didáticos; as ferramentas em si continuam
# exigindo esse token, então expor a documentação não expõe as chamadas reais.
MCP_API_TOKEN = os.getenv("MCP_API_TOKEN", "")


def require_mcp_token(x_mcp_token: str = Header(None)) -> None:
    if not MCP_API_TOKEN or not x_mcp_token or not hmac.compare_digest(x_mcp_token, MCP_API_TOKEN):
        raise HTTPException(status_code=401, detail="Token de ferramenta ausente ou inválido")


async def _executar(nome: str, corrotina, correlacao: str, **contexto):
    """Executa uma ferramenta registrando duração e desfecho no log central.

    O orchestrator manda sua própria correlação no header X-Correlation-Id
    quando tem uma; assim a chamada de ferramenta aparece na mesma timeline da
    investigação que a motivou, em vez de solta.
    """
    t0 = time.perf_counter()
    try:
        resultado = await corrotina
    except Exception as err:
        emitir("ferramenta.chamada", "falhou", correlation_id=correlacao,
               message=f"{nome} falhou: {err}",
               payload={"ferramenta": nome, **contexto},
               error=str(err),
               duration_ms=int((time.perf_counter() - t0) * 1000))
        raise
    emitir("ferramenta.chamada", "ok", correlation_id=correlacao,
           message=f"{nome} respondeu",
           payload={"ferramenta": nome, **contexto},
           duration_ms=int((time.perf_counter() - t0) * 1000))
    return resultado


def _correlacao(request: Request) -> str:
    return request.headers.get("X-Correlation-Id") or nova_correlacao()


@app.get("/tools/cnpj/{cnpj}", dependencies=[Depends(require_mcp_token)])
async def cnpj(cnpj: str, request: Request):
    return await _executar("consultar_cnpj", consultar_cnpj(cnpj),
                           _correlacao(request), cnpj=cnpj)


@app.get("/tools/processos", dependencies=[Depends(require_mcp_token)])
async def processos(termo: str, request: Request):
    return await _executar("buscar_processos", buscar_processos(termo),
                           _correlacao(request), termo=termo)


@app.get("/tools/noticias", dependencies=[Depends(require_mcp_token)])
async def noticias(termo: str, request: Request):
    return await _executar("buscar_noticias", buscar_noticias(termo),
                           _correlacao(request), termo=termo)


@app.get("/health")
async def health():
    return {"status": "ok"}
