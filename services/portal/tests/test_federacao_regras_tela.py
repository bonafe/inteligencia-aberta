"""Tela de regras de replicação (/cluster/regras/) e o MER gerado dos models."""
import pytest
from django.apps import apps
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Membership, Organization, User
from apps.federacao import mer
from apps.federacao.models import RegraReplicacao

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    o = Organization.objects.create(name="O", slug="o", org_type="institutional", owner=dono)
    Membership.objects.create(user=dono, organization=o, role="owner")
    return o


def _cliente(user):
    c = Client()
    c.force_login(user)
    return c


def _membro(org):
    u = User.objects.create_user(username="membro", password="x")
    Membership.objects.create(user=u, organization=org, role="member")
    return u


def test_exige_login():
    assert Client().get(reverse("cluster:regras")).status_code in (302, 401, 403)


def test_membro_ve_mas_nao_simula(org):
    c = _cliente(_membro(org))
    assert c.get(reverse("cluster:regras")).status_code == 200
    r = c.post(reverse("cluster:regras"), {"organizacao": org.pk, "sentido": "enviar", "par_tipo": "terceiro", "nivel": "publico"})
    assert r.status_code == 403


def test_admin_simula_piso_e_padrao(org):
    c = _cliente(org.owner)
    url = reverse("cluster:regras")
    base = {"organizacao": org.pk, "sentido": "enviar"}
    # terceiro + restrito: o piso nega mesmo com a regra padrão
    r = c.post(url, {**base, "par_tipo": "terceiro", "nivel": "restrito"})
    assert r.status_code == 200 and not r.context["decisao"].permitido
    assert r.context["decisao"].origem == "piso"
    # próprio + confidencial: a padrão permite
    r = c.post(url, {**base, "par_tipo": "proprio", "nivel": "confidencial"})
    assert r.context["decisao"].permitido


def test_simulacao_nao_deixa_rastro_de_auditoria(org):
    from apps.artifacts.models import AuditLog
    from apps.events.models import PipelineEvent

    c = _cliente(org.owner)
    c.post(reverse("cluster:regras"), {"organizacao": org.pk, "sentido": "enviar", "par_tipo": "proprio", "nivel": "confidencial"})
    assert not AuditLog.objects.exists()
    assert not PipelineEvent.objects.filter(stage="federacao.decisao").exists()


def test_simulacao_invalida_da_400(org):
    r = _cliente(org.owner).post(reverse("cluster:regras"), {"organizacao": org.pk, "sentido": "x", "par_tipo": "proprio", "nivel": "publico"})
    assert r.status_code == 400


def test_so_regras_da_propria_organizacao(org):
    outra_dono = User.objects.create_user(username="outro", password="x")
    outra = Organization.objects.create(name="Outra", slug="outra", org_type="individual", owner=outra_dono)
    RegraReplicacao.objects.create(organizacao=outra, efeito="negar", sentido="enviar", par_ref="segredo-alheio")
    r = _cliente(org.owner).get(reverse("cluster:regras"))
    corpo = r.content.decode()
    assert "segredo-alheio" not in corpo
    assert _cliente(org.owner).get(reverse("cluster:regras"), {"organizacao": outra.pk}).status_code == 404


def test_mer_campos_curados_existem():
    for rotulo, campos in mer.CAMPOS.items():
        m = apps.get_model(rotulo)
        for nome in campos:
            m._meta.get_field(nome)  # levanta FieldDoesNotExist se o model mudou


def test_mer_mermaid_rotula_o_planejado():
    texto = mer.mermaid()
    assert texto.startswith("erDiagram")
    assert "Space ||--o{ EspacoArtefato" in texto
    for nome in ("Foto", "Tag"):
        assert f'"{nome} (planejado)"' in texto
