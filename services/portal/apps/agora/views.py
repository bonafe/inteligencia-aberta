"""API de workspaces do Ultima Agora (sessão + CSRF, como o resto do portal).

Tudo que muda estado emite um evento operacional (ADR 005) com identificadores, nunca com conteúdo.
"""

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie

from apps.accounts.models import Membership
from apps.accounts.permissoes import papel_do_usuario
from apps.accounts.views import orgs_do_usuario
from apps.artifacts.models import Artifact
from apps.events.emit import emit

from . import acesso, sync
from .models import Workspace, WorkspaceMember

CLASSIFICACOES = {valor for valor, _ in Artifact.ClassificationLevel.choices}
PAPEIS = {valor for valor, _ in Workspace.Role.choices}


def _json(request) -> dict:
    try:
        corpo = json.loads(request.body or b"{}")
    except ValueError:
        raise Http404("JSON inválido") from None
    return corpo if isinstance(corpo, dict) else {}


def _evento(request, nome, workspace, **payload):
    emit(
        f"agora.{nome}", "ok", source="portal", subject_type="workspace", subject_id=workspace.pk,
        tenant_id=workspace.organization_id, user_id=request.user.pk, payload={"workspace": str(workspace.pk), **payload},
    )


def _serializar(workspace, papel):
    return {
        "id": str(workspace.pk), "title": workspace.title, "role": papel, "classification": workspace.classification,
        "organization": str(workspace.organization_id), "space": str(workspace.space_id) if workspace.space_id else None,
        "owner": str(workspace.owner_id), "updated_at": workspace.updated_at.isoformat(),
        "archived_at": workspace.archived_at.isoformat() if workspace.archived_at else None,
    }


def _carregar(request, workspace_id, *, exige=None):
    """O workspace e o papel do usuário nele. 404 (e não 403) para quem não tem acesso: não revela que existe."""
    workspace = get_object_or_404(Workspace.objects.select_related("organization"), pk=workspace_id)
    papel = acesso.papel_efetivo(request.user, workspace)
    if papel is None:
        raise Http404("Workspace não encontrado")
    if exige is not None and papel not in exige:
        raise PermissionDenied("Seu papel neste workspace não permite isso.")
    return workspace, papel


@method_decorator(ensure_csrf_cookie, name="dispatch")
class AppView(View):
    """A página que hospeda o Ultima Agora. Entrega o cookie CSRF que a API exige."""

    def get(self, request):
        from django.conf import settings

        return render(request, "agora/app.html", {"configuracao": {
            "sync_url": settings.AGORA_SYNC_URL,                     # vazio: sem colaboração em tempo real
            "api": "/agora/api/v1",
            "usuario": {"id": str(request.user.pk), "name": request.user.get_full_name() or request.user.get_username()},
        }})


class WorkspacesView(View):
    def get(self, request):
        lista = sorted(acesso.workspaces_visiveis(request.user), key=lambda item: item[0].updated_at, reverse=True)
        return JsonResponse({"workspaces": [_serializar(w, papel) for w, papel in lista]})

    def post(self, request):
        dados = _json(request)
        orgs = orgs_do_usuario(request.user)
        organizacao = orgs.filter(pk=dados["organization"]).first() if dados.get("organization") else None
        organizacao = organizacao or next((o for o in orgs.order_by("pk") if acesso.pode_criar(request.user, o)), None)
        if organizacao is None or not acesso.pode_criar(request.user, organizacao):
            raise PermissionDenied("Você não pode criar workspaces nesta organização.")
        classificacao = dados.get("classification", Artifact.ClassificationLevel.RESTRICTED)
        if classificacao not in CLASSIFICACOES:
            return JsonResponse({"error": "classificação inválida"}, status=400)
        workspace = Workspace.objects.create(
            title=(dados.get("title") or "Novo workspace")[:200], organization=organizacao, owner=request.user, classification=classificacao,
        )
        _evento(request, "workspace.criado", workspace, classification=classificacao)
        return JsonResponse(_serializar(workspace, acesso.papel_efetivo(request.user, workspace)), status=201)


class WorkspaceView(View):
    def get(self, request, workspace_id):
        workspace, papel = _carregar(request, workspace_id)
        return JsonResponse(_serializar(workspace, papel))

    def patch(self, request, workspace_id):
        workspace, _ = _carregar(request, workspace_id, exige={"owner"})
        dados = _json(request)
        alteradas = []
        if "title" in dados:
            workspace.title = str(dados["title"])[:200] or workspace.title
            alteradas.append("title")
        if "classification" in dados:
            if dados["classification"] not in CLASSIFICACOES:
                return JsonResponse({"error": "classificação inválida"}, status=400)
            anterior, workspace.classification = workspace.classification, dados["classification"]
            alteradas.append("classification")
            #Evento de segurança: reclassificar é sempre registrado (R-AUD-2)
            _evento(request, "workspace.reclassificado", workspace, de=anterior, para=workspace.classification)
        if "space" in dados:
            from apps.federacao.models import Space
            espaco = Space.objects.filter(pk=dados["space"], organizacao=workspace.organization).first() if dados["space"] else None
            if dados["space"] and espaco is None:
                return JsonResponse({"error": "espaço inexistente nesta organização"}, status=400)
            workspace.space = espaco
            alteradas.append("space")
        workspace.save()
        if alteradas:
            _evento(request, "workspace.alterado", workspace, campos=alteradas)
        return JsonResponse(_serializar(workspace, "owner"))


class ArquivarView(View):
    def post(self, request, workspace_id):
        workspace, _ = _carregar(request, workspace_id, exige={"owner"})
        workspace.archived_at = timezone.now()
        workspace.save(update_fields=["archived_at", "updated_at"])
        _evento(request, "workspace.arquivado", workspace)
        return JsonResponse(_serializar(workspace, "owner"))


class TokenView(View):
    """Token curto para o `agora-sync`, com o papel que o usuário tem agora (R-API-3)."""

    def post(self, request, workspace_id):
        workspace, papel = _carregar(request, workspace_id)
        if workspace.archived_at and papel != "owner":
            raise Http404("Workspace não encontrado")
        emit(
            "agora.sync.token", "ok", source="portal", subject_type="workspace", subject_id=workspace.pk,
            tenant_id=workspace.organization_id, user_id=request.user.pk, payload={"workspace": str(workspace.pk), "role": papel},
        )
        try:
            token = sync.emitir_token(request.user, workspace, papel)
        except sync.SyncNaoConfigurado:
            return JsonResponse({"error": "A colaboração em tempo real não está configurada nesta instância."}, status=503)
        return JsonResponse({"token": token, "role": papel, "expires_in": sync.TOKEN_TTL_S})


class MembrosView(View):
    """Papéis explícitos. Só o dono lista e altera; o papel efetivo continua limitado pelo da organização."""

    def get(self, request, workspace_id):
        workspace, _ = _carregar(request, workspace_id, exige={"owner"})
        linhas = []
        for membro in orgs_membros(workspace):
            explicito = WorkspaceMember.objects.filter(workspace=workspace, user=membro.user).first()
            linhas.append({
                "user": str(membro.user_id), "username": membro.user.get_username(), "org_role": membro.role,
                "explicit_role": explicito.role if explicito else None, "role": acesso.papel_efetivo(membro.user, workspace),
            })
        return JsonResponse({"members": linhas})

    def post(self, request, workspace_id):
        return self._definir(request, workspace_id)

    patch = post

    def _definir(self, request, workspace_id):
        workspace, _ = _carregar(request, workspace_id, exige={"owner"})
        dados = _json(request)
        if dados.get("role") not in PAPEIS:
            return JsonResponse({"error": "papel inválido"}, status=400)
        alvo = get_user_model().objects.filter(username=dados.get("username")).first()
        if alvo is None or papel_do_usuario(alvo, workspace.organization) is None:
            return JsonResponse({"error": "a pessoa precisa ser membro da organização"}, status=400)
        if alvo.pk == workspace.owner_id:
            return JsonResponse({"error": "o dono do workspace não muda de papel"}, status=400)
        WorkspaceMember.objects.update_or_create(workspace=workspace, user=alvo, defaults={"role": dados["role"], "invited_by": request.user})
        efetivo = acesso.papel_efetivo(alvo, workspace)
        _evento(request, "workspace.papel_alterado", workspace, alvo=str(alvo.pk), papel=dados["role"], efetivo=efetivo)
        sync.avisar_papel(workspace, alvo, efetivo)
        return JsonResponse({"user": str(alvo.pk), "role": efetivo, "requested": dados["role"]})

    def delete(self, request, workspace_id):
        workspace, _ = _carregar(request, workspace_id, exige={"owner"})
        alvo = get_user_model().objects.filter(username=_json(request).get("username")).first()
        removidos = WorkspaceMember.objects.filter(workspace=workspace, user=alvo).delete()[0] if alvo else 0
        if removidos:
            efetivo = acesso.papel_efetivo(alvo, workspace)
            _evento(request, "workspace.papel_removido", workspace, alvo=str(alvo.pk), efetivo=efetivo)
            if efetivo:
                sync.avisar_papel(workspace, alvo, efetivo)
        return JsonResponse({"removed": bool(removidos)})


def orgs_membros(workspace):
    return Membership.objects.filter(organization=workspace.organization).select_related("user")
