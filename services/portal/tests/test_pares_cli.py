"""Comandos `par_*`: pareamento sem tela, com conferência da impressão digital esperada."""
import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import Membership, Organization, User
from apps.cluster import pares
from apps.cluster.models import Maquina
from apps.federacao.chaves import garantir_chave_ativa
from apps.federacao.did import impressao_digital
from tests._instancias import ChaveOutraInstancia
from tests.test_pares_enrolamento import _codigo, _enviar_de

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def ambiente(settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "pares-cli"}}
    settings.FEDERACAO_ENDPOINT_ANUNCIADO = "http://10.0.0.5:8000"


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x", is_superuser=True)
    org = Organization.objects.create(name="O", slug="o", org_type="institutional", owner=dono)
    Membership.objects.create(user=dono, organization=org, role="owner")
    return org


@pytest.fixture
def local():
    return garantir_chave_ativa()[0]


@pytest.fixture
def remota():
    return ChaveOutraInstancia()


def rodar(*args):
    saida = StringIO()
    call_command(*args, stdout=saida)
    return saida.getvalue().strip()


def test_identidade_traz_did_e_impressao(org, local):
    d = json.loads(rodar("par_identidade", "--json"))
    assert d["did"] == local.did and d["impressao_digital"] == impressao_digital(local.did)


def test_convite_proprio_exige_flag_e_devolve_codigo(org, local):
    with pytest.raises(CommandError, match="confirmo-proprio"):
        rodar("par_convite", "--tipo", "proprio")
    d = json.loads(rodar("par_convite", "--tipo", "proprio", "--confirmo-proprio", "--json"))
    assert pares.decodificar_codigo(d["codigo"])["did"] == local.did and d["tipo"] == "proprio"


def test_sem_organizacao_falha_claro(local):
    with pytest.raises(CommandError, match="organização"):
        rodar("par_listar")


def test_aceitar_recusa_impressao_divergente_e_nao_cadastra(org, local, remota, monkeypatch):
    monkeypatch.setattr(pares, "_enviar", _enviar_de(remota))
    with pytest.raises(CommandError, match="divergente"):
        rodar("par_aceitar", _codigo(remota), "--impressao-esperada", "0000-0000-0000-0000-0000")
    assert not Maquina.objects.filter(did=remota.did).exists()


def test_aceitar_e_confirmar_sao_idempotentes(org, local, remota, monkeypatch):
    monkeypatch.setattr(pares, "_enviar", _enviar_de(remota))
    imp = impressao_digital(remota.did)
    args = ["par_aceitar", _codigo(remota), "--impressao-esperada", imp, "--tipo", "proprio", "--confirmo-proprio", "--json"]
    assert json.loads(rodar(*args))["mudou"] is True
    assert json.loads(rodar(*args))["mudou"] is False
    conf = ["par_confirmar", "--did", remota.did, "--impressao-esperada", imp, "--json"]
    assert json.loads(rodar(*conf))["mudou"] is True
    assert json.loads(rodar(*conf))["mudou"] is False
    par = Maquina.objects.get(did=remota.did)
    assert (par.tipo, par.estado) == ("proprio", "confirmado")
    assert json.loads(rodar("par_listar", "--json"))[0]["estado"] == "confirmado"


def test_confirmar_recusa_impressao_divergente_e_par_inexistente(org, local, remota):
    with pytest.raises(CommandError, match="divergente"):
        rodar("par_confirmar", "--did", remota.did, "--impressao-esperada", "0000-0000-0000-0000-0000")
    with pytest.raises(CommandError, match="não há par"):
        rodar("par_confirmar", "--did", remota.did, "--impressao-esperada", impressao_digital(remota.did))


def test_aceitar_proprio_sem_flag_recusa(org, local, remota, monkeypatch):
    monkeypatch.setattr(pares, "_enviar", _enviar_de(remota))
    with pytest.raises(CommandError, match="confirmo-proprio"):
        rodar("par_aceitar", _codigo(remota), "--impressao-esperada", impressao_digital(remota.did), "--tipo", "proprio")
