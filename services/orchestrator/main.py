import base64
import email
import os
import re
import time
import uuid
import json
import httpx
import jwt
from email import policy as email_policy
from io import BytesIO
from urllib.parse import urljoin
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from minio import Minio
from policy_engine import check, registrar_decisao
from eventos import emitir, nova_correlacao

from segredos import validar_segredos
from conteudo_hash import hash_ni

validar_segredos(["JWT_SIGNING_KEY", "INTERNAL_API_TOKEN", "S3_SECRET_KEY", "POSTGRES_PASSWORD"])

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

# CORS. A extensão do Chrome chama com host_permissions (fetch do service worker),
# que não depende de CORS; origens só são necessárias para páginas web. Sem a
# variável definida (desenvolvimento) fica aberto; em produção o compose a define,
# e vazia significa "nenhuma origem". Ex.: https://ia.exemplo.com.br,chrome-extension://<id>
_cors_env = os.getenv("CORS_ALLOWED_ORIGINS")
CORS_ORIGINS = ["*"] if _cors_env is None else [o.strip() for o in _cors_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuração do MinIO
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "garage:3900")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
MHTML_BUCKET_NAME = "inteligencia-aberta-mhtml"

minio_client = Minio(
    S3_ENDPOINT,
    access_key=S3_ACCESS_KEY,
    secret_key=S3_SECRET_KEY,
    secure=False
)

# Garante que o bucket existe ao iniciar
if not minio_client.bucket_exists(MHTML_BUCKET_NAME):
    minio_client.make_bucket(MHTML_BUCKET_NAME)
FAVICON_MAX_BYTES = 300_000
# Tag `<link rel="icon">`/`<link rel="shortcut icon">` — procurado como texto
# em vez de com um parser de HTML de verdade: o orchestrator não depende de
# nenhuma lib de parsing (isso é trabalho do portal), e achar um único atributo
# não justifica adicionar uma. Falha graciosa (favicon é cosmético) cobre o
# caso de HTML fora do padrão que o regex não reconheça.
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_REL_ICON_RE = re.compile(r'rel\s*=\s*["\']?\s*(?:shortcut\s+icon|icon)\b', re.IGNORECASE)
_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def _href_do_icone(html: str) -> str | None:
    for tag in _LINK_TAG_RE.findall(html):
        if _REL_ICON_RE.search(tag):
            m = _HREF_RE.search(tag)
            if m:
                return m.group(1)
    return None


def extrair_favicon_do_mhtml(mhtml_bytes: bytes, page_url: str, favicon_url: str) -> tuple[str | None, str]:
    """Acha o favicon dentro do próprio MHTML — o Chrome já baixou esse
    recurso como parte de carregar a página, então não tem motivo pra buscar
    de novo por fora. Nunca faz requisição de rede: é exatamente uma
    requisição de rede na hora da captura (ex.: Wikimedia bloqueando com 403
    um download feito fora do navegador) que este desenho evita.

    Nunca levanta — favicon é cosmético, não pode derrubar a captura.
    Devolve (data_uri, motivo)."""
    # Ícone embutido como data URI de propósito (comum em SVG minimalista) —
    # já vem pronto, nem precisa olhar o MHTML.
    if favicon_url.startswith("data:image/"):
        return favicon_url, "data_uri_direta"

    try:
        msg = email.message_from_bytes(mhtml_bytes, policy=email_policy.default)
    except Exception as err:
        return None, f"MHTML ilegível: {err}"

    imagens_por_local: dict[str, object] = {}
    html = None
    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type == "text/html" and html is None:
            payload = part.get_payload(decode=True)
            if payload:
                html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        local = part.get("Content-Location", "")
        if local and content_type.startswith("image/"):
            imagens_por_local[local] = part

    if not imagens_por_local:
        return None, "MHTML não tem nenhuma imagem embutida"

    # Duas fontes de candidato, nessa ordem: a URL que a extensão reportou
    # (tab.favIconUrl — já é a resolução do próprio Chrome), depois o que o
    # HTML capturado declara via <link rel="icon">, caso a primeira não bata
    # com nenhuma imagem embutida.
    candidatos = [u for u in (favicon_url,) if u]
    if html:
        href = _href_do_icone(html)
        if href:
            candidatos.append(urljoin(page_url, href))

    for candidato in candidatos:
        part = imagens_por_local.get(candidato)
        if part is None:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        if len(payload) > FAVICON_MAX_BYTES:
            return None, f"favicon maior que o limite ({len(payload)} bytes)"
        return f"data:{part.get_content_type()};base64,{base64.b64encode(payload).decode()}", "ok"

    return None, "favicon não encontrado entre as imagens embutidas no MHTML"


class InvestigationRequest(BaseModel):
    query: str
    classification: str = "restrito"


@app.post("/investigar")
async def investigar(request: InvestigationRequest, claims: dict = Depends(require_jwt)):
    # tenant_id/user_id vêm das claims autenticadas, não do corpo da requisição.
    tenant_id = claims["tenant_id"]
    correlacao = nova_correlacao()
    policy = check(
        operation="chamar_llm_externo",
        classification=request.classification,
        tenant_id=tenant_id,
        requesting_tenant=tenant_id,
    )
    registrar_decisao(
        policy,
        operation="chamar_llm_externo",
        classification=request.classification,
        tenant_id=tenant_id,
        requesting_tenant=tenant_id,
        correlation_id=correlacao,
        user_id=claims.get("user_id"),
    )
    if policy["decision"] == "BLOQUEADO":
        raise HTTPException(status_code=403, detail=policy["reason"])

    # TODO Fase 0: executar grafo LangGraph
    return {"status": "em_desenvolvimento", "query": request.query,
            "correlation_id": correlacao}


@app.post("/api/v1/capture/mhtml")
async def capture_mhtml(
    file: UploadFile = File(...),
    url: str = Form(...),
    title: str = Form(""),
    favicon_url: str = Form(""),
    timestamp: str = Form(...),
    classification_level: str = Form("restrito"),
    allow_external_llm: bool = Form(False),
    correlation_id: str = Form(""),
    claims: dict = Depends(require_jwt),
):
    # Identidade autenticada — vinda do JWT, não de campos do formulário.
    user_id = claims["user_id"]
    tenant_id = claims["tenant_id"]

    # A correlação acompanha esta captura por todo o sistema. Preferimos a que a
    # extensão gerou (assim a timeline começa no clique do usuário); se ela não
    # mandou nenhuma, criamos aqui.
    correlacao = correlation_id or nova_correlacao()

    def evento(stage, status, **kw):
        emitir(stage, status, correlation_id=correlacao,
               tenant_id=tenant_id, user_id=user_id, **kw)

    try:
        # Lê o conteúdo do arquivo
        content = await file.read()
        file_size = len(content)
        # Identidade do conteúdo (ADR 010): calculada sobre os bytes exatos que
        # serão gravados, no ato da captura, antes de qualquer transformação.
        conteudo_hash = hash_ni(content)

        evento("captura.recebida", "ok",
               message=f"MHTML recebido de {url}",
               payload={"url": url, "titulo": title, "bytes": file_size,
                        "classificacao": classification_level,
                        "allow_external_llm": allow_external_llm,
                        "capture_timestamp": timestamp,
                        "hash_ni": conteudo_hash})

        # Gera um ID único para o artefato
        artifact_id = str(uuid.uuid4())
        object_name = f"{artifact_id}.mhtml"

        # Salva no MinIO
        t0 = time.perf_counter()
        minio_client.put_object(
            bucket_name=MHTML_BUCKET_NAME,
            object_name=object_name,
            data=BytesIO(content),
            length=file_size,
            content_type=file.content_type or "application/x-mimearchive"
        )
        evento("captura.armazenada", "ok",
               message=f"MHTML gravado no MinIO ({file_size} bytes)",
               payload={"bucket": MHTML_BUCKET_NAME, "path": object_name,
                        "bytes": file_size, "url": url, "hash_ni": conteudo_hash},
               duration_ms=int((time.perf_counter() - t0) * 1000))

        favicon_data_uri, motivo_favicon = extrair_favicon_do_mhtml(content, url, favicon_url)
        evento("captura.favicon", "ok" if favicon_data_uri else "vazio",
               message=motivo_favicon,
               payload={"url": url, "favicon_url": (favicon_url or "")[:200]})

        artifact_content = {
            "title": title,
            "url": url,
            "capture_timestamp": timestamp,
            "mhtml_bucket": MHTML_BUCKET_NAME,
            "mhtml_path": object_name,
        }
        if favicon_data_uri:
            artifact_content["favicon_data_uri"] = favicon_data_uri

        # Registra o artefato no Portal via API Django (dispara o pipeline automaticamente).
        # X-Internal-Token autentica o canal serviço-a-serviço; user_id/tenant_id
        # vêm do JWT já validado, então o portal pode confiar neles.
        classificacao_elevada = False
        try:
            resp = httpx.post(
                f"{PORTAL_URL}/artifacts/api/v1/artefatos/",
                json={
                    "artifact_type": "documento",
                    "content": artifact_content,
                    "classification_level": classification_level,
                    "allow_external_llm": allow_external_llm,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "correlation_id": correlacao,
                    "blob_hash": conteudo_hash,
                    "info_type": "fato",
                    "sources": [],
                },
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
                timeout=10.0,
            )
            resp.raise_for_status()
            corpo_portal = resp.json()
            artifact_id = corpo_portal["artifact_id"]
            # O portal pode elevar o nível por regra de domínio; devolvemos o efetivo.
            classification_level = corpo_portal.get("classification_level", classification_level)
            classificacao_elevada = bool(corpo_portal.get("classificacao_elevada", False))
        except Exception as api_err:
            # Captura órfã: o MHTML está no MinIO mas nenhum Artifact existe, então
            # o catch-up do Beat (que varre Artifact, não o bucket) jamais a verá.
            # Antes disto, esse caso sumia sem deixar rastro em lugar nenhum.
            print(f"Erro ao registrar artefato no Portal: {api_err}")
            emitir("captura.orfa", "falhou", correlation_id=correlacao,
                   tenant_id=tenant_id, user_id=user_id,
                   message="MHTML gravado no MinIO mas não registrado no portal",
                   payload={"bucket": MHTML_BUCKET_NAME, "path": object_name, "url": url,
                            "hash_ni": conteudo_hash},
                   error=str(api_err), sincrono=True)
            raise Exception(f"Salvo no MinIO, mas erro ao registrar no Portal: {api_err}")

        # TODO: Enviar o texto extraído para o Qdrant

        return {
            "status": "success",
            "artifact_id": artifact_id,
            "classification_level": classification_level,
            "classificacao_elevada": classificacao_elevada,
            "correlation_id": correlacao,
            "message": "MHTML capturado e salvo no armazenamento seguro."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "servico": "orchestrator",
        "instancia": os.getenv("INSTANCIA_NOME", ""),
        "versao": os.getenv("IA_VERSION", ""),
    }
