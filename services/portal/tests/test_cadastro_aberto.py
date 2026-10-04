"""Cadastro aberto: o primeiro usuário vai direto ao cadastro e vira o administrador; quem vem depois
também se cadastra, ganha uma organização só sua e não vê nada dos outros."""
import io
from unittest import mock

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.models import Artifact

pytestmark = pytest.mark.django_db


def _dados(nome):
    return {"username": nome, "email": f"{nome}@x.com", "password1": "Senha-forte-123", "password2": "Senha-forte-123"}


# ── primeiro usuário ────────────────────────────────────────────────────────

def test_sem_nenhum_usuario_a_tela_de_entrar_manda_direto_para_o_cadastro(client):
    resp = client.get("/entrar/")
    assert resp.status_code == 302 and resp["Location"] == "/registro/"


def test_sem_nenhum_usuario_qualquer_pagina_protegida_acaba_no_cadastro(client):
    resp = client.get("/", follow=True)
    assert resp.redirect_chain[-1][0] == "/registro/" and resp.status_code == 200
    assert client.get("/cluster/", follow=True).redirect_chain[-1][0] == "/registro/"


def test_o_cadastro_avisa_que_o_primeiro_usuario_sera_o_administrador(client):
    html = client.get("/registro/").content.decode()
    assert "ainda não tem nenhum usuário" in html and "administrador" in html
    assert "Já tem conta?" not in html                          # não há conta para entrar


def test_o_primeiro_cadastro_vira_superusuario_com_organizacao_propria_e_entra_logado(client):
    resp = client.post("/registro/", _dados("primeira"))
    assert resp.status_code == 302
    usuario = User.objects.get(username="primeira")
    assert usuario.is_superuser and usuario.is_staff
    assert Membership.objects.filter(user=usuario, role="owner").exists()
    assert client.get("/").status_code == 200                   # já está logado


def test_depois_do_primeiro_usuario_a_tela_de_entrar_volta_a_ser_o_login(client):
    User.objects.create_user(username="alguem", password="x")
    assert client.get("/entrar/").status_code == 200
    assert client.get("/", follow=True).redirect_chain[-1][0].startswith("/entrar/")


# ── cadastro aberto para os outros ──────────────────────────────────────────

def test_a_pagina_de_cadastro_continua_aberta_depois_do_primeiro_usuario(client):
    User.objects.create_user(username="alguem", password="x")
    resp = client.get("/registro/")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "organização só sua" in html and "ainda não tem nenhum usuário" not in html and "Já tem conta?" in html


def test_o_segundo_cadastro_nao_e_administrador_e_ganha_a_propria_organizacao(client):
    client.post("/registro/", _dados("primeira"))
    client.logout()
    resp = client.post("/registro/", _dados("segunda"))
    assert resp.status_code == 302
    segunda = User.objects.get(username="segunda")
    assert not segunda.is_superuser and not segunda.is_staff
    org = Organization.objects.get(owner=segunda)
    assert Membership.objects.filter(user=segunda, organization=org, role="owner").exists()
    assert not Membership.objects.filter(user=segunda).exclude(organization=org).exists()   # só na dela
    assert User.objects.filter(is_superuser=True).count() == 1


def test_quem_se_cadastra_depois_nao_ve_os_dados_de_quem_ja_estava(client):
    client.post("/registro/", _dados("primeira"))
    org_primeira = Organization.objects.get(owner__username="primeira")
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org_primeira, info_type=Artifact.InfoType.FACT,
        content={"url": "https://exemplo.org/", "mhtml_path": "x", "favicon_data_uri": "data:image/png;base64,aGk="},
    )
    assert client.get(f"/artifacts/{artefato.id}/favicon/").status_code == 200       # a dona vê
    client.logout()
    client.post("/registro/", _dados("segunda"))
    for rota in ("favicon", "content", "mhtml"):
        assert client.get(f"/artifacts/{artefato.id}/{rota}/").status_code == 404     # a outra não
    assert str(artefato.id) not in client.get("/artifacts/gallery/").content.decode()


def test_o_cluster_e_a_lista_de_pares_so_aparecem_para_a_organizacao_da_maquina(client):
    client.post("/registro/", _dados("admin"))
    org_admin = Organization.objects.get(owner__username="admin")
    from apps.cluster.models import Maquina

    maquina = Maquina.objects.create(organizacao=org_admin, dono=org_admin.owner, apelido="maquina-do-admin-xyz")
    assert "maquina-do-admin-xyz" in client.get("/cluster/").content.decode()
    client.logout()
    client.post("/registro/", _dados("outra"))
    assert "maquina-do-admin-xyz" not in client.get("/cluster/").content.decode()
    assert client.get(f"/cluster/maquinas/{maquina.id}/modelos/").status_code == 404


# ── o travão opcional continua existindo ────────────────────────────────────

@override_settings(REGISTRO_ABERTO=False)
def test_com_o_travao_ligado_o_primeiro_cadastro_ainda_passa_e_o_segundo_nao(client):
    assert client.post("/registro/", _dados("primeira")).status_code == 302
    client.logout()
    assert client.get("/registro/").status_code == 403
    assert client.post("/registro/", _dados("segunda")).status_code == 403
    assert not User.objects.filter(username="segunda").exists()


# ── bootstrap sem as variáveis ──────────────────────────────────────────────

def test_o_bootstrap_sem_as_variaveis_nao_cria_ninguem_e_diz_que_o_primeiro_cadastro_sera_o_administrador(monkeypatch):
    for nome in ("DJANGO_SUPERUSER_USERNAME", "DJANGO_SUPERUSER_EMAIL", "DJANGO_SUPERUSER_PASSWORD"):
        monkeypatch.delenv(nome, raising=False)
    saida, erro = io.StringIO(), io.StringIO()
    with mock.patch("apps.accounts.management.commands.bootstrap_instancia.Minio") as minio:
        minio.return_value.bucket_exists.return_value = True
        call_command("bootstrap_instancia", stdout=saida, stderr=erro)
    assert not User.objects.exists()
    assert "primeiro cadastro" in saida.getvalue() and erro.getvalue() == ""
