"""Tela de membros: o admin adiciona uma pessoa já cadastrada à organização e, opcionalmente, a um workspace."""
import pytest
from django.test import Client
from django.urls import reverse

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


@pytest.fixture
def ws(org):
    return Workspace.objects.create(title="Caso", organization=org, owner=org.owner)


def _logar(nome):
    c = Client()
    c.force_login(User.objects.get(username=nome))
    return c


def _url(org):
    return reverse("membros", kwargs={"org_id": org.pk})


def test_admin_adiciona_membro_a_organizacao(org):
    User.objects.create_user(username="ana", password="x")
    r = _logar("dono").post(_url(org), {"username": "ana", "papel": "member"})
    assert r.status_code == 302
    m = Membership.objects.get(user__username="ana", organization=org)
    assert m.role == "member" and m.invited_by.username == "dono"


def test_adiciona_ao_workspace_e_os_dois_enxergam(org, ws):
    ana = User.objects.create_user(username="ana", password="x")
    _logar("dono").post(_url(org), {"username": "ana", "papel": "member", "workspace": str(ws.pk), "papel_workspace": "editor"})
    assert WorkspaceMember.objects.get(workspace=ws, user=ana).role == "editor"
    assert acesso.papel_efetivo(ana, ws) == "editor"


def test_convidado_fica_no_teto_de_leitura(org, ws):
    ana = User.objects.create_user(username="ana", password="x")
    _logar("dono").post(_url(org), {"username": "ana", "papel": "guest", "workspace": str(ws.pk), "papel_workspace": "editor"})
    assert acesso.papel_efetivo(ana, ws) == "viewer"


def test_membro_comum_nao_administra(org):
    User.objects.create_user(username="bia", password="x")
    User.objects.create_user(username="ana", password="x")
    Membership.objects.create(user=User.objects.get(username="bia"), organization=org, role="member")
    c = _logar("bia")
    assert c.get(_url(org)).status_code == 403
    assert c.post(_url(org), {"username": "ana", "papel": "member"}).status_code == 403
    assert not Membership.objects.filter(user__username="ana").exists()


def test_nao_concede_dono_nem_usuario_inexistente(org):
    User.objects.create_user(username="ana", password="x")
    c = _logar("dono")
    c.post(_url(org), {"username": "ana", "papel": "owner"})
    c.post(_url(org), {"username": "fantasma", "papel": "member"})
    assert not Membership.objects.filter(user__username="ana", organization=org).exists()


def test_membro_existente_nao_tem_papel_alterado(org):
    ana = User.objects.create_user(username="ana", password="x")
    Membership.objects.create(user=ana, organization=org, role="admin")
    _logar("dono").post(_url(org), {"username": "ana", "papel": "guest"})
    assert Membership.objects.get(user=ana, organization=org).role == "admin"


def test_workspace_de_outra_organizacao_e_recusado(org):
    outro = User.objects.create_user(username="outro", password="x")
    org2 = Organization.objects.create(name="O2", slug="o2", org_type="team", owner=outro)
    Membership.objects.create(user=outro, organization=org2, role="owner")
    ws2 = Workspace.objects.create(title="Alheio", organization=org2, owner=outro)
    User.objects.create_user(username="ana", password="x")
    _logar("dono").post(_url(org), {"username": "ana", "papel": "member", "workspace": str(ws2.pk), "papel_workspace": "editor"})
    assert not WorkspaceMember.objects.filter(workspace=ws2).exists()
    assert not Membership.objects.filter(user__username="ana", organization=org).exists()


def test_sem_org_na_url_redireciona_para_a_do_admin(org):
    r = _logar("dono").get(reverse("membros"))
    assert r.status_code == 302 and r.url == _url(org)
