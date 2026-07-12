import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException
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


@app.get("/tools/cnpj/{cnpj}", dependencies=[Depends(require_mcp_token)])
async def cnpj(cnpj: str):
    return await consultar_cnpj(cnpj)


@app.get("/tools/processos", dependencies=[Depends(require_mcp_token)])
async def processos(termo: str):
    return await buscar_processos(termo)


@app.get("/tools/noticias", dependencies=[Depends(require_mcp_token)])
async def noticias(termo: str):
    return await buscar_noticias(termo)


@app.get("/health")
async def health():
    return {"status": "ok"}
