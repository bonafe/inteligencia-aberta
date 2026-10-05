"""Papel efetivo num workspace do Ultima Agora: o papel na organização dá o teto (R-IA-5)."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Membership, Organization, User
from apps.agora import acesso
from apps.agora.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    organizacao = Organization.objects.create(name="Org", slug="org", org_type="team", owner=dono)
    Membership.objects.create(user=dono, organization=organizacao, role=Membership.Role.OWNER)
    return organizacao


def _pessoa(org, nome, papel_org):
    pessoa = User.objects.create_user(username=nome, password="x")
    if papel_org:
        Membership.objects.create(user=pessoa, organization=org, role=papel_org)
    return pessoa


@pytest.fixture
def workspace(org):
    return Workspace.objects.create(title="Investigação", organization=org, owner=org.owner)


def test_o_dono_do_workspace_e_owner(workspace):
    assert acesso.papel_efetivo(workspace.owner, workspace) == "owner"


@pytest.mark.parametrize("papel_org, esperado", [
    (Membership.Role.OWNER, "editor"), (Membership.Role.ADMIN, "editor"),
    (Membership.Role.MEMBER, "participant"), (Membership.Role.GUEST, "viewer"),
])
def test_sem_papel_explicito_vale_o_padrao_da_organizacao(org, workspace, papel_org, esperado):
    assert acesso.papel_efetivo(_pessoa(org, f"p-{papel_org}", papel_org), workspace) == esperado


def test_quem_nao_e_da_organizacao_nao_tem_acesso_nem_com_linha_explicita(org, workspace):
    estranho = _pessoa(org, "estranho", None)
    WorkspaceMember.objects.create(workspace=workspace, user=estranho, role="owner")
    assert acesso.papel_efetivo(estranho, workspace) is None


def test_papel_explicito_sobe_ate_o_teto_e_nao_alem(org, workspace):
    membro = _pessoa(org, "membro", Membership.Role.MEMBER)
    WorkspaceMember.objects.create(workspace=workspace, user=membro, role="owner")
    assert acesso.papel_efetivo(membro, workspace) == "editor"          # teto de um membro


def test_convidado_nunca_passa_de_viewer(org, workspace):
    convidado = _pessoa(org, "convidado", Membership.Role.GUEST)
    WorkspaceMember.objects.create(workspace=workspace, user=convidado, role="editor")
    assert acesso.papel_efetivo(convidado, workspace) == "viewer"


def test_papel_explicito_pode_rebaixar(org, workspace):
    admin = _pessoa(org, "admin", Membership.Role.ADMIN)
    WorkspaceMember.objects.create(workspace=workspace, user=admin, role="viewer")
    assert acesso.papel_efetivo(admin, workspace) == "viewer"


def test_papel_explicito_expirado_volta_ao_padrao(org, workspace):
    membro = _pessoa(org, "membro", Membership.Role.MEMBER)
    WorkspaceMember.objects.create(workspace=workspace, user=membro, role="viewer", expires_at=timezone.now() - timedelta(days=1))
    assert acesso.papel_efetivo(membro, workspace) == "participant"


def test_membership_da_organizacao_expirada_tira_o_acesso(org, workspace):
    membro = _pessoa(org, "membro", Membership.Role.MEMBER)
    Membership.objects.filter(user=membro).update(expires_at=timezone.now() - timedelta(minutes=1))
    assert acesso.papel_efetivo(membro, workspace) is None


def test_o_dono_que_virou_convidado_perde_o_poder_na_organizacao(org):
    ex_dono = _pessoa(org, "ex", Membership.Role.GUEST)
    workspace = Workspace.objects.create(title="Meu", organization=org, owner=ex_dono)
    assert acesso.papel_efetivo(ex_dono, workspace) == "viewer"


def test_workspaces_visiveis_filtra_por_organizacao_e_arquivados(org, workspace):
    outra = Organization.objects.create(name="Outra", slug="outra", org_type="team", owner=org.owner)
    Workspace.objects.create(title="De outra org", organization=outra, owner=org.owner)
    Workspace.objects.create(title="Arquivado", organization=org, owner=org.owner, archived_at=timezone.now())
    membro = _pessoa(org, "membro", Membership.Role.MEMBER)
    assert [(w.title, papel) for w, papel in acesso.workspaces_visiveis(membro)] == [("Investigação", "participant")]


def test_so_dono_admin_e_membro_podem_criar(org):
    assert acesso.pode_criar(_pessoa(org, "m", Membership.Role.MEMBER), org)
    assert not acesso.pode_criar(_pessoa(org, "g", Membership.Role.GUEST), org)
    assert not acesso.pode_criar(_pessoa(org, "x", None), org)
