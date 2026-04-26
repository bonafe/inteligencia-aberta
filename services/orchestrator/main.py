from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from policy_engine import check

app = FastAPI(title="Orquestrador — Inteligência Aberta")


class InvestigationRequest(BaseModel):
    query: str
    tenant_id: str
    classification: str = "restrito"
    user_id: str


@app.post("/investigar")
async def investigar(request: InvestigationRequest):
    policy = check(
        operation="chamar_llm_externo",
        classification=request.classification,
        tenant_id=request.tenant_id,
        requesting_tenant=request.tenant_id,
    )
    if policy["decision"] == "BLOQUEADO":
        raise HTTPException(status_code=403, detail=policy["reason"])

    # TODO Fase 0: executar grafo LangGraph
    return {"status": "em_desenvolvimento", "query": request.query}


@app.get("/health")
async def health():
    return {"status": "ok"}
