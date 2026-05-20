import os
import email
import base64
from email import policy
from django.shortcuts import render, get_object_or_404
from django.views import View
from django.http import HttpResponse, Http404
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.utils.decorators import method_decorator
from .models import Artifact
from minio import Minio
from minio.error import S3Error

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
