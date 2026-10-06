"""Adicionar uma pessoa já cadastrada a uma organização (e, se quiser, a um workspace do Agora).

Não há convite por e-mail nem link: quem administra a organização informa o nome de usuário e a pessoa
passa a ser membro na hora. O papel `owner` não se concede aqui, e quem já é membro não tem o papel
alterado por este caminho (rebaixar ou promover dono/administrador é decisão do admin do Django).
"""

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.events.emit import emit

from .models import Membership
from .permissoes import exige_admin

PAPEIS_CONCEDIVEIS = (Membership.Role.ADMIN, Membership.Role.MEMBER, Membership.Role.GUEST)


class ErroMembro(Exception):
    """Entrada recusada; a mensagem é segura para mostrar a quem administra."""


@dataclass
class Resultado:
    usuario: object
    membership: Membership
    ja_era_membro: bool
    workspace_papel: str | None = None  # papel efetivo no workspace, se foi pedido


def adicionar_membro(ator, organizacao, username: str, papel: str, *, workspace=None, papel_workspace: str | None = None) -> Resultado:
    exige_admin(ator, organizacao)
    if papel not in PAPEIS_CONCEDIVEIS:
        raise ErroMembro("Papel inválido para a organização.")
    alvo = get_user_model().objects.filter(username=(username or "").strip(), is_active=True).first()
    if alvo is None:
        raise ErroMembro("Não existe usuário ativo com esse nome. A pessoa precisa se cadastrar em /registro/ antes.")
    if workspace is not None and (workspace.organization_id != organizacao.pk or workspace.archived_at):
        raise ErroMembro("Workspace inválido para esta organização.")

    with transaction.atomic():
        membership, criado = Membership.objects.get_or_create(
            user=alvo, organization=organizacao, defaults={"role": papel, "invited_by": ator},
        )
        ja_era_membro = not criado
        if ja_era_membro and membership.expires_at and membership.expires_at <= timezone.now():
            # vínculo vencido: reativa com o papel pedido (não é um membro vigente)
            membership.role, membership.expires_at, membership.invited_by = papel, None, ator
            membership.save(update_fields=["role", "expires_at", "invited_by"])
            ja_era_membro = False
        if not ja_era_membro:
            emit(
                "accounts.membro_adicionado", "ok", source="portal", subject_type="organization", subject_id=organizacao.pk,
                tenant_id=organizacao.pk, user_id=ator.pk, payload={"alvo": str(alvo.pk), "papel": membership.role},
            )

        resultado = Resultado(alvo, membership, ja_era_membro)
        if workspace is not None:
            resultado.workspace_papel = _no_workspace(ator, workspace, alvo, papel_workspace)
    return resultado


def _no_workspace(ator, workspace, alvo, papel_workspace) -> str:
    from apps.agora import acesso, sync
    from apps.agora.models import Workspace, WorkspaceMember

    if papel_workspace not in {Workspace.Role.EDITOR, Workspace.Role.PARTICIPANT, Workspace.Role.VIEWER}:
        raise ErroMembro("Papel inválido para o workspace.")
    if alvo.pk != workspace.owner_id:
        WorkspaceMember.objects.update_or_create(
            workspace=workspace, user=alvo, defaults={"role": papel_workspace, "invited_by": ator, "expires_at": None},
        )
    efetivo = acesso.papel_efetivo(alvo, workspace)  # o teto da organização pode ser menor que o pedido
    emit(
        "agora.papel_alterado", "ok", source="portal", subject_type="workspace", subject_id=workspace.pk,
        tenant_id=workspace.organization_id, user_id=ator.pk,
        payload={"workspace": str(workspace.pk), "alvo": str(alvo.pk), "papel": papel_workspace, "efetivo": efetivo},
    )
    sync.avisar_papel(workspace, alvo, efetivo)
    return efetivo
