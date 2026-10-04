"""Identificador global `urn:uuid:` — F0 da ADR 010."""
import uuid

import pytest

from apps.accounts.models import Organization, User
from apps.artifacts.models import Artifact
from apps.federacao.ids import eh_urn_valida, normalizar, urn_de, uuid_de_urn

UUID_V4 = uuid.UUID("3f2504e0-4f89-41d3-9a0c-0305e82c3301")
URN = "urn:uuid:3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def test_urn_de_uuid_e_de_string():
    assert urn_de(UUID_V4) == URN
    assert urn_de(str(UUID_V4)) == URN


def test_urn_emitida_e_sempre_minuscula():
    assert urn_de("3F2504E0-4F89-41D3-9A0C-0305E82C3301") == URN


def test_ida_e_volta():
    for _ in range(20):
        u = uuid.uuid4()
        assert uuid_de_urn(urn_de(u)) == u


def test_leitura_aceita_maiusculas_e_normaliza():
    entrada = "URN:UUID:3F2504E0-4F89-41D3-9A0C-0305E82C3301"
    assert uuid_de_urn(entrada) == UUID_V4
    assert normalizar(entrada) == URN


def test_aceita_outras_versoes_rfc4122():
    assert eh_urn_valida(urn_de(uuid.uuid1()))
    assert eh_urn_valida("urn:uuid:018f5e2e-7c3a-7b1e-8a55-3c1f6b7d9e10")  # v7


@pytest.mark.parametrize("valor", [
    None, 42, "", URN[len("urn:uuid:"):],               # sem prefixo
    "urn:uuid:", "urn:uuid:abc",
    "urn:isbn:0451450523", "uuid:3f2504e0-4f89-41d3-9a0c-0305e82c3301",
    "urn:uuid:3f2504e04f8941d39a0c0305e82c3301",         # sem hífens
    "urn:uuid:{3f2504e0-4f89-41d3-9a0c-0305e82c33}",     # chaves
    "urn:uuid:urn:uuid:3f2504e0-4f89-41d3-9a0c-0305e82c3301",
    URN + " ", " " + URN, URN + "\n",
    "urn:uuid:3f2504e0-4f89-41d3-9a0c-0305e82c330g",
    "urn:uuid:00000000-0000-0000-0000-000000000000",     # nulo
    "urn:uuid:ffffffff-ffff-ffff-ffff-ffffffffffff",     # máximo
])
def test_urn_invalida(valor):
    assert not eh_urn_valida(valor)
    with pytest.raises(ValueError):
        uuid_de_urn(valor)


def test_urn_de_recusa_uuid_invalido():
    for lixo in ("nao-e-uuid", "00000000-0000-0000-0000-000000000000"):
        with pytest.raises(ValueError):
            urn_de(lixo)


@pytest.mark.django_db
def test_artifact_urn_e_o_proprio_pk():
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)
    a = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, content={},
        info_type=Artifact.InfoType.FACT,
    )
    assert a.urn == f"urn:uuid:{a.id}"
    assert Artifact.objects.get(pk=uuid_de_urn(a.urn)) == a
