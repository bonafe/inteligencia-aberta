import base64
import email
import json
import os
import uuid
from email import policy

from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csrf import csrf_exempt
from minio import Minio
from minio.error import S3Error

from apps.accounts.models import Organization, User
from .models import Artifact

@method_decorator(csrf_exempt, name="dispatch")
class ArtefatoCreateAPIView(View):
    """Endpoint interno para criação de artefatos via ORM (dispara signal → pipeline)."""

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "JSON inválido"}, status=400)

        user = self._resolve_user(data.get("user_id"))
        if user is None:
            return JsonResponse({"error": "Nenhum usuário encontrado"}, status=400)

        org = self._resolve_org(data.get("tenant_id"), user)

        artifact = Artifact.objects.create(
            artifact_type=data.get("artifact_type", Artifact.Type.DOCUMENT),
            content=data.get("content", {}),
            classification_level=data.get("classification_level", Artifact.ClassificationLevel.RESTRICTED),
            tenant=org,
            allow_external_llm=False,
            classified_by=user,
            info_type=data.get("info_type", Artifact.InfoType.FACT),
            sources=data.get("sources", []),
        )

        return JsonResponse({"artifact_id": str(artifact.id)}, status=201)

    def _resolve_user(self, user_id):
        if user_id:
            return User.objects.filter(id=user_id).first()
        return User.objects.order_by("date_joined").first()

    def _resolve_org(self, tenant_id, user):
        if tenant_id:
            org = Organization.objects.filter(id=tenant_id).first()
            if org:
                return org
        org = Organization.objects.filter(owner=user).first()
        if org:
            return org
        return Organization.objects.create(
            name="Uso Individual",
            slug=f"individual-{str(uuid.uuid4())[:8]}",
            org_type=Organization.Type.INDIVIDUAL,
            owner=user,
        )


class ArtifactGalleryView(View):
    def get(self, request):
        # Pega todos os documentos, ordenando pelos mais recentes
        artifacts_qs = Artifact.objects.filter(artifact_type="documento").order_by("-created_at")
        
        valid_artifacts = []
        for artifact in artifacts_qs:
            content = artifact.content or {}
            # Filtra apenas os que possuem MHTML
            if content.get("mhtml_path"):
                valid_artifacts.append(artifact)
                
        context = {
            "artifacts": valid_artifacts
        }
        return render(request, "artifacts/gallery.html", context)

class ServeMHTMLView(View):
    @method_decorator(xframe_options_sameorigin)
    def get(self, request, artifact_id):
        artifact = get_object_or_404(Artifact, id=artifact_id)
        content = artifact.content or {}
        
        mhtml_path = content.get("mhtml_path")
        mhtml_bucket = content.get("mhtml_bucket", "inteligencia-aberta-mhtml")
        
        if not mhtml_path:
            raise Http404("Caminho do MHTML não encontrado no artefato.")
            
        # Conexão com MinIO
        MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
        MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
        MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "substitua-por-senha-segura")
        
        try:
            client = Minio(
                MINIO_ENDPOINT,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                secure=False
            )
            
            # Pega o objeto inteiro na memória para conversão
            response = client.get_object(mhtml_bucket, mhtml_path)
            mhtml_bytes = response.read()
            response.close()
            response.release_conn()
            
            # Converte o formato MIME (MHTML) para um HTML único com Base64
            msg = email.message_from_bytes(mhtml_bytes, policy=policy.default)
            html_part = None
            resources = {}
            
            for part in msg.walk():
                content_type = part.get_content_type()
                content_id = part.get("Content-ID")
                content_location = part.get("Content-Location")
                
                # Encontra o arquivo HTML principal
                if content_type == "text/html" and not html_part:
                    charset = part.get_content_charset('utf-8') or 'utf-8'
                    payload = part.get_payload(decode=True)
                    if payload:
                        html_part = payload.decode(charset, errors='replace')
                # O resto são assets (imagens, css, etc)
                else:
                    payload = part.get_payload(decode=True)
                    if payload:
                        b64_payload = base64.b64encode(payload).decode('ascii')
                        data_uri = f"data:{content_type};base64,{b64_payload}"
                        
                        if content_location:
                            resources[content_location] = data_uri
                        if content_id:
                            cid = content_id.strip('<>')
                            resources[f"cid:{cid}"] = data_uri
                            
            if not html_part:
                raise Http404("Arquivo HTML não encontrado dentro do MHTML.")
                
            # Substitui as URLs originais pelas imagens em base64 no HTML
            for loc, data_uri in resources.items():
                html_part = html_part.replace(loc, data_uri)
                
            # Retorna o HTML processado nativamente
            return HttpResponse(html_part, content_type="text/html; charset=utf-8")
            
        except S3Error as e:
            print(f"Erro no MinIO: {e}")
            raise Http404("Arquivo não encontrado no MinIO")
        except Exception as e:
            print(f"Erro ao processar MHTML: {e}")
            raise Http404("Erro ao converter MHTML para visualização.")
