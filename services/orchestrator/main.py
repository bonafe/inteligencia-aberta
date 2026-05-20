import os
import uuid
import json
import psycopg2
from datetime import datetime
from io import BytesIO
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from minio import Minio
from policy_engine import check

app = FastAPI(title="Orquestrador — Inteligência Aberta")

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


@app.post("/api/v1/capture/mhtml")
async def capture_mhtml(
    file: UploadFile = File(...),
    url: str = Form(...),
    title: str = Form(""),
    timestamp: str = Form(...),
    user_id: str | None = Form(None),
    tenant_id: str | None = Form(None),
    classification_level: str = Form("restrito")
):
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
        
        # Conecta no Postgres para registrar o artefato no Portal (Django Admin)
        try:
            conn = psycopg2.connect(
                dbname=os.getenv("POSTGRES_DB", "inteligencia_aberta"),
                user=os.getenv("POSTGRES_USER", "postgres"),
                password=os.getenv("POSTGRES_PASSWORD", "substitua-por-senha-segura"),
                host=os.getenv("POSTGRES_HOST", "db"),
                port=os.getenv("POSTGRES_PORT", "5432")
            )
            cursor = conn.cursor()
            
            # Fallback de Usuário (Pega o usuário passado ou o primeiro do banco)
            if user_id:
                cursor.execute("SELECT id FROM accounts_user WHERE id = %s;", (user_id,))
            else:
                cursor.execute("SELECT id FROM accounts_user ORDER BY date_joined ASC LIMIT 1;")
                
            user_row = cursor.fetchone()
            if not user_row:
                raise Exception("Nenhum usuário encontrado no banco para atrelar o artefato.")
            resolved_user_id = user_row[0]
            
            # Fallback de Organização (Tenant)
            resolved_tenant_id = tenant_id
            if not resolved_tenant_id:
                # Tenta achar alguma organização do usuário
                cursor.execute("SELECT id FROM accounts_organization WHERE owner_id = %s LIMIT 1;", (resolved_user_id,))
                org_row = cursor.fetchone()
                
                if org_row:
                    resolved_tenant_id = org_row[0]
                else:
                    # Se não tem organização, CRIA uma automaticamente
                    resolved_tenant_id = str(uuid.uuid4())
                    now = datetime.now()
                    cursor.execute("""
                        INSERT INTO accounts_organization (id, name, slug, org_type, owner_id, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (resolved_tenant_id, "Uso Individual", f"individual-{str(uuid.uuid4())[:8]}", "individual", resolved_user_id, now))
            
            # Inserir na tabela artifacts_artifact
            insert_query = """
                INSERT INTO artifacts_artifact (
                    id, artifact_type, content, classification_level, 
                    tenant_id, allow_external_llm, classified_by_id, 
                    classified_at, info_type, sources, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """
            
            artifact_content = json.dumps({
                "title": title,
                "url": url,
                "capture_timestamp": timestamp,
                "mhtml_bucket": MHTML_BUCKET_NAME,
                "mhtml_path": object_name
            })
            
            now = datetime.now()
            
            cursor.execute(insert_query, (
                artifact_id,
                "documento", # type = DOCUMENT
                artifact_content,
                classification_level, 
                resolved_tenant_id,
                False,       # allow_external_llm
                resolved_user_id,
                now,         # classified_at
                "fato",      # info_type
                json.dumps([]), # sources
                now,
                now
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as db_err:
            # Se der erro no banco, loga mas não quebra se o arquivo salvou
            print(f"Erro ao salvar no BD: {db_err}")
            raise Exception(f"Salvo no MinIO, mas erro no BD: {db_err}")
        
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
