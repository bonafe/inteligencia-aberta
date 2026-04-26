from fastapi import FastAPI
from tools.cnpj import consultar_cnpj
from tools.processos import buscar_processos
from tools.noticias import buscar_noticias

app = FastAPI(title="MCP — Ferramentas Inteligência Aberta")


@app.get("/tools/cnpj/{cnpj}")
async def cnpj(cnpj: str):
    return await consultar_cnpj(cnpj)


@app.get("/tools/processos")
async def processos(termo: str):
    return await buscar_processos(termo)


@app.get("/tools/noticias")
async def noticias(termo: str):
    return await buscar_noticias(termo)


@app.get("/health")
async def health():
    return {"status": "ok"}
