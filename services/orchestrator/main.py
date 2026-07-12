import os
import uuid
import json
import httpx
import jwt
from io import BytesIO
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from minio import Minio
from policy_engine import check

PORTAL_URL = os.getenv("PORTAL_URL", "http://portal:8000")

# Segredos compartilhados com o portal (ver services/portal/config/settings/base.py):
# JWT_SIGNING_KEY valida os tokens da extensão; INTERNAL_API_TOKEN autentica a
# chamada serviço-a-serviço de criação de artefato.
JWT_SIGNING_KEY = os.getenv("JWT_SIGNING_KEY", "")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")

app = FastAPI(title="Orquestrador — Inteligência Aberta")


def require_jwt(authorization: str = Header(None)) -> dict:
    """Valida o Bearer JWT emitido pelo portal e devolve as claims.

    A identidade (user_id/tenant_id) passa a vir das claims assinadas, não mais
    de campos auto-declarados pelo cliente. 401 em token ausente/inválido/expirado.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token de autenticação ausente")
    token = authorization.split(" ", 1)[1]
    try:
        claims = jwt.decode(token, JWT_SIGNING_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")
    if not claims.get("user_id") or not claims.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Token sem identidade de tenant")
    return claims

# Habilitar CORS para a extensão do Chrome
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Na produção, restringir para o ID da extensão
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuração do MinIO
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "substitua-por-senha-segura")
MHTML_BUCKET_NAME = "inteligencia-aberta-mhtml"

minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)

# Garante que o bucket existe ao iniciar
if not minio_client.bucket_exists(MHTML_BUCKET_NAME):
    minio_client.make_bucket(MHTML_BUCKET_NAME)
class InvestigationRequest(BaseModel):
    query: str
    classification: str = "restrito"


@app.post("/investigar")
async def investigar(request: InvestigationRequest, claims: dict = Depends(require_jwt)):
    # tenant_id/user_id vêm das claims autenticadas, não do corpo da requisição.
    tenant_id = claims["tenant_id"]
    policy = check(
        operation="chamar_llm_externo",
        classification=request.classification,
        tenant_id=tenant_id,
        requesting_tenant=tenant_id,
    )
    if policy["decision"] == "BLOQUEADO":
        raise HTTPException(status_code=403, detail=policy["reason"])

    # TODO Fase 0: executar grafo LangGraph
    return {"status": "em_desenvolvimento", "query": request.query}


@app.post("/api/v1/capture/mhtml")
async def capture_mhtml(
    file: UploadFile = File(...),
    url: str = Form(...),
    title: str = Form(""),
    timestamp: str = Form(...),
    classification_level: str = Form("restrito"),
    allow_external_llm: bool = Form(False),
    claims: dict = Depends(require_jwt),
):
    # Identidade autenticada — vinda do JWT, não de campos do formulário.
    user_id = claims["user_id"]
    tenant_id = claims["tenant_id"]
    try:
        # Lê o conteúdo do arquivo
        content = await file.read()
        file_size = len(content)
        
        # Gera um ID único para o artefato
        artifact_id = str(uuid.uuid4())
        object_name = f"{artifact_id}.mhtml"
        
        # Salva no MinIO
        minio_client.put_object(
            bucket_name=MHTML_BUCKET_NAME,
            object_name=object_name,
            data=BytesIO(content),
            length=file_size,
            content_type=file.content_type or "application/x-mimearchive"
        )
        
        # Registra o artefato no Portal via API Django (dispara o pipeline automaticamente).
        # X-Internal-Token autentica o canal serviço-a-serviço; user_id/tenant_id
        # vêm do JWT já validado, então o portal pode confiar neles.
        try:
            resp = httpx.post(
                f"{PORTAL_URL}/artifacts/api/v1/artefatos/",
                json={
                    "artifact_type": "documento",
                    "content": {
                        "title": title,
                        "url": url,
                        "capture_timestamp": timestamp,
                        "mhtml_bucket": MHTML_BUCKET_NAME,
                        "mhtml_path": object_name,
                    },
                    "classification_level": classification_level,
                    "allow_external_llm": allow_external_llm,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "info_type": "fato",
                    "sources": [],
                },
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
                timeout=10.0,
            )
            resp.raise_for_status()
            artifact_id = resp.json()["artifact_id"]
        except Exception as api_err:
            print(f"Erro ao registrar artefato no Portal: {api_err}")
            raise Exception(f"Salvo no MinIO, mas erro ao registrar no Portal: {api_err}")
        
        # TODO: Enviar o texto extraído para o Qdrant
        
        return {
            "status": "success",
            "artifact_id": artifact_id,
            "message": "MHTML capturado e salvo no armazenamento seguro."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}
