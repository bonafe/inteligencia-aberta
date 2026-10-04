"""Hash de conteúdo (RFC 6920) e `blob_hash` do artefato — F0 da ADR 010."""
import io
import json

import pytest
from django.core.management import call_command

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.management.commands import calcular_hashes
from apps.artifacts.models import Artifact
from config.conteudo_hash import formato_valido, hash_ni, verificar

pytestmark = pytest.mark.django_db

# Vetor de teste do próprio RFC 6920 (seção 3).
HELLO = "ni:///sha-256;f4OxZX_x_FO5LcGBSKHWXfwtSx-j1ncoSt3SABJtkGk"


# ── formato ─────────────────────────────────────────────────────────────────

def test_hash_ni_bate_com_o_vetor_do_rfc():
    assert hash_ni(b"Hello World!") == HELLO


def test_hash_ni_nao_tem_padding_e_tem_tamanho_fixo():
    h = hash_ni(b"qualquer coisa")
    assert "=" not in h and len(h) == len("ni:///sha-256;") + 43


@pytest.mark.parametrize("valor", [
    None, 123, "", "sha256:abc", HELLO + "x", HELLO[:-1],
    "ni:///sha-512;" + "A" * 43, "ni:///sha-256;" + "+" * 43,
])
def test_formato_invalido(valor):
    assert not formato_valido(valor)


def test_verificar_aceita_o_correto_e_recusa_divergente_e_malformado():
    assert verificar(b"Hello World!", HELLO)
    assert not verificar(b"Hello World?", HELLO)
    assert not verificar(b"Hello World!", "lixo")


# ── API interna ─────────────────────────────────────────────────────────────

@pytest.fixture
def api(client, settings):
    settings.INTERNAL_API_TOKEN = "token-de-teste"
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="Org", slug="org", org_type="individual", owner=dono)
    Membership.objects.create(user=dono, organization=org, role="owner")

    def postar(**extra):
        corpo = {
            "artifact_type": "documento",
            "content": {"url": "https://exemplo.org", "mhtml_path": "x.mhtml"},
            "tenant_id": str(org.id), "user_id": str(dono.id), "info_type": "fato",
            **extra,
        }
        return client.post(
            "/artifacts/api/v1/artefatos/", data=json.dumps(corpo),
            content_type="application/json", HTTP_X_INTERNAL_TOKEN="token-de-teste",
        )
    return postar


def test_api_grava_blob_hash_enviado_pelo_orchestrator(api):
    resp = api(blob_hash=HELLO)
    assert resp.status_code == 201
    assert Artifact.objects.get(pk=resp.json()["artifact_id"]).blob_hash == HELLO


def test_api_sem_blob_hash_continua_funcionando(api):
    resp = api()
    assert resp.status_code == 201
    assert Artifact.objects.get(pk=resp.json()["artifact_id"]).blob_hash is None


def test_api_recusa_blob_hash_malformado_sem_criar_artefato(api):
    resp = api(blob_hash="md5:abc")
    assert resp.status_code == 400
    assert not Artifact.objects.exists()


# ── calcular_hashes ─────────────────────────────────────────────────────────

@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono2", password="x")
    return Organization.objects.create(name="Org2", slug="org2", org_type="individual", owner=dono)


def _artefato(org, caminho, **kw):
    return Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        content={"mhtml_bucket": "b", "mhtml_path": caminho}, **kw,
    )


@pytest.fixture
def blobs(monkeypatch):
    armazenamento = {}

    def ler(bucket, caminho):
        if caminho not in armazenamento:
            raise FileNotFoundError(caminho)
        return armazenamento[caminho]

    monkeypatch.setattr(calcular_hashes, "ler_blob", ler)
    return armazenamento


def _rodar(*args):
    saida = io.StringIO()
    call_command("calcular_hashes", *args, stdout=saida)
    return saida.getvalue()


def test_grava_hash_dos_artefatos_sem_hash(org, blobs):
    blobs["a.mhtml"] = b"Hello World!"
    a = _artefato(org, "a.mhtml")
    saida = _rodar()
    a.refresh_from_db()
    assert a.blob_hash == HELLO
    assert "1 hash(es) gravado(s)" in saida


def test_simular_nao_grava(org, blobs):
    blobs["a.mhtml"] = b"Hello World!"
    a = _artefato(org, "a.mhtml")
    saida = _rodar("--simular")
    a.refresh_from_db()
    assert a.blob_hash is None
    assert "simulação" in saida


def test_sem_verificar_ignora_quem_ja_tem_hash(org, blobs):
    blobs["a.mhtml"] = b"outro conteudo"
    _artefato(org, "a.mhtml", blob_hash=HELLO)
    assert "0 artefato(s) examinado(s)" in _rodar()


def test_verificar_relata_divergencia_sem_sobrescrever(org, blobs):
    blobs["a.mhtml"] = b"o blob mudou"
    a = _artefato(org, "a.mhtml", blob_hash=HELLO)
    saida = _rodar("--verificar")
    a.refresh_from_db()
    assert a.blob_hash == HELLO
    assert "1 divergente(s)" in saida and "DIVERGENTE" in saida


def test_verificar_confirma_o_que_confere(org, blobs):
    blobs["a.mhtml"] = b"Hello World!"
    _artefato(org, "a.mhtml", blob_hash=HELLO)
    assert "1 confirmado(s)" in _rodar("--verificar")


def test_blob_ausente_e_relatado_e_nao_interrompe_o_resto(org, blobs):
    blobs["b.mhtml"] = b"Hello World!"
    sem_blob = _artefato(org, "a.mhtml")
    com_blob = _artefato(org, "b.mhtml")
    saida = _rodar()
    sem_blob.refresh_from_db()
    com_blob.refresh_from_db()
    assert sem_blob.blob_hash is None and com_blob.blob_hash == HELLO
    assert "1 sem blob legível" in saida and "SEM BLOB" in saida


def test_gravar_hash_preserva_updated_at(org, blobs):
    blobs["a.mhtml"] = b"Hello World!"
    a = _artefato(org, "a.mhtml")
    antes = Artifact.objects.get(pk=a.pk).updated_at
    _rodar()
    a.refresh_from_db()
    assert a.updated_at == antes and a.blob_hash == HELLO

