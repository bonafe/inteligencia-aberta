"""Quem pode o quê num workspace.

Regras (R-IA-2, R-IA-5):

1. Só enxerga o workspace quem é membro **vigente** da organização dele. Uma linha em `WorkspaceMember`
   sozinha não basta: sair da organização tira o acesso.
2. O papel no workspace **nunca amplia** o papel na organização: o papel na organização dá um teto.
3. Sem linha explícita, vale o padrão do papel na organização.
4. O dono do workspace é `owner` (dentro do teto).

    papel na organização | padrão no workspace | teto
    ---------------------+---------------------+-------
    owner, admin         | editor              | owner
    member               | participant         | editor
    guest                | viewer              | viewer
"""

from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Membership
from apps.accounts.permissoes import papel_do_usuario

from .models import Workspace, WorkspaceMember

R = Workspace.Role
ORDEM = {R.VIEWER: 0, R.PARTICIPANT: 1, R.EDITOR: 2, R.OWNER: 3}
TETO = {Membership.Role.OWNER: R.OWNER, Membership.Role.ADMIN: R.OWNER, Membership.Role.MEMBER: R.EDITOR, Membership.Role.GUEST: R.VIEWER}
PADRAO = {Membership.Role.OWNER: R.EDITOR, Membership.Role.ADMIN: R.EDITOR, Membership.Role.MEMBER: R.PARTICIPANT, Membership.Role.GUEST: R.VIEWER}
#: Papéis na organização que podem criar workspaces
PODEM_CRIAR = frozenset({Membership.Role.OWNER, Membership.Role.ADMIN, Membership.Role.MEMBER})


def _menor(a, b):
    return a if ORDEM[a] <= ORDEM[b] else b


def papel_efetivo(user, workspace) -> str | None:
    """O papel do usuário no workspace, ou `None` se ele não tem acesso."""
    papel_org = papel_do_usuario(user, workspace.organization)
    if papel_org is None:
        return None
    teto = TETO[papel_org]
    if workspace.owner_id == user.pk:
        base = R.OWNER
    else:
        explicito = WorkspaceMember.objects.filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()), workspace=workspace, user=user,
        ).first()
        base = R(explicito.role) if explicito else PADRAO[papel_org]
    return str(_menor(base, teto))


def workspaces_visiveis(user):
    """Workspaces (não arquivados) em que o usuário tem algum papel."""
    from apps.accounts.views import orgs_do_usuario

    candidatos = Workspace.objects.filter(organization__in=orgs_do_usuario(user), archived_at__isnull=True).select_related("organization")
    return [(w, papel) for w in candidatos if (papel := papel_efetivo(user, w)) is not None]


def pode_criar(user, organizacao) -> bool:
    return papel_do_usuario(user, organizacao) in PODEM_CRIAR
