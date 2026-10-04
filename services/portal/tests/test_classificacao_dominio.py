"""Classificação por domínio de origem na captura."""
import json

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.classificacao_dominio import (
    aplicar_regras, host_da_url, nivel_efetivo, normalizar_dominio, sufixos,
)
from apps.artifacts.models import Artifact, RegraClassificacaoDominio

pytestmark = pytest.mark.django_db


# ── normalização ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("entrada,esperado", [
    ("bancodobrasil.com.br", "bancodobrasil.com.br"),
    ("  BancoDoBrasil.COM.br  ", "bancodobrasil.com.br"),
    ("*.bancodobrasil.com.br", "bancodobrasil.com.br"),
    (".bancodobrasil.com.br", "bancodobrasil.com.br"),
    ("bancodobrasil.com.br.", "bancodobrasil.com.br"),
    ("https://www.bb.com.br/pessoa-fisica?x=1", "www.bb.com.br"),
    ("bb.com.br:8443/login", "bb.com.br"),
    ("bücher.example", "xn--bcher-kva.example"),
])
def test_normalizar_dominio(entrada, esperado):
    assert normalizar_dominio(entrada) == esperado


@pytest.mark.parametrize("entrada", [
    "", "   ", "com", "localhost", "192.168.0.1", "10.0.0.1:8000", "a..b.com",
    "-ruim.com", "ruim-.com", "tem espaco.com", "under_score.com", "https://",
])
def test_normalizar_dominio_invalido(entrada):
    with pytest.raises(ValueError):
        normalizar_dominio(entrada)


def test_host_da_url():
    assert host_da_url("https://WWW.BB.com.br:443/x") == "www.bb.com.br"
    assert host_da_url("https://bb.com.br./x") == "bb.com.br"
    for ruim in ("", None, "isto não é url", "file:///etc/passwd", "http://[::1]/"):
        assert host_da_url(ruim) in (None, "::1")


def test_sufixos():
    assert sufixos("login.bb.com.br") == ["login.bb.com.br", "bb.com.br", "com.br", "br"]


# ── só sobe o nível ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("pedido,regra,esperado", [
    ("publico", "confidencial", "confidencial"),
    ("interno", "restrito", "restrito"),
    ("restrito", "confidencial", "confidencial"),
    ("confidencial", "publico", "confidencial"),   # nunca rebaixa
    ("restrito", "interno", "restrito"),
    ("restrito", "restrito", "restrito"),
    ("lixo", "interno", "lixo"),                    # desconhecido conta como restrito
    ("lixo", "confidencial", "confidencial"),
])
def test_nivel_efetivo(pedido, regra, esperado):
    assert nivel_efetivo(pedido, regra) == esperado


# ── casamento e isolamento ──────────────────────────────────────────────────

@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    return Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)


def _regra(org, dominio="bancodobrasil.com.br", nivel="confidencial", **kw):
    return RegraClassificacaoDominio.objects.create(tenant=org, dominio=dominio, nivel=nivel, **kw)


@pytest.mark.parametrize("url", [
    "https://bancodobrasil.com.br/",
    "https://www.bancodobrasil.com.br/pessoa-fisica",
    "https://login.ap.bancodobrasil.com.br:8443/x?y=1",
    "https://WWW.BancoDoBrasil.com.br./",
])
def test_regra_cobre_o_dominio_e_subdominios(org, url):
    _regra(org)
    r = aplicar_regras(org.id, url, "publico")
    assert r.nivel == "confidencial" and r.elevado and r.dominio == "bancodobrasil.com.br"


@pytest.mark.parametrize("url", [
    "https://meubancodobrasil.com.br/",
    "https://bancodobrasil.com.br.outro.com/",
    "https://com.br/",
    "https://exemplo.org/bancodobrasil.com.br",
    "isto não é url", "",
])
def test_regra_nao_casa_com_o_que_nao_e_o_dominio(org, url):
    _regra(org)
    r = aplicar_regras(org.id, url, "restrito")
    assert r.nivel == "restrito" and not r.elevado and r.regra_id is None


def test_sem_regra_devolve_o_nivel_pedido_intacto(org):
    r = aplicar_regras(org.id, "https://exemplo.org/", "valor-estranho")
    assert r.nivel == "valor-estranho" and not r.elevado


def test_varias_regras_vale_a_de_maior_nivel(org):
    _regra(org, "bb.com.br", "interno")
    _regra(org, "ap.bb.com.br", "confidencial")
    assert aplicar_regras(org.id, "https://login.ap.bb.com.br/", "publico").nivel == "confidencial"
    assert aplicar_regras(org.id, "https://www.bb.com.br/", "publico").nivel == "interno"


def test_regra_inativa_e_ignorada(org):
    _regra(org, ativa=False)
    assert not aplicar_regras(org.id, "https://bancodobrasil.com.br/", "publico").elevado


def test_regra_de_outra_organizacao_nao_se_aplica(org):
    outra = Organization.objects.create(
        name="X", slug="x", org_type="individual", owner=User.objects.create_user(username="x", password="x"),
    )
    _regra(outra)
    assert not aplicar_regras(org.id, "https://bancodobrasil.com.br/", "publico").elevado


def test_regra_que_nao_eleva_ainda_informa_qual_casou(org):
    regra = _regra(org, nivel="restrito")
    r = aplicar_regras(org.id, "https://bancodobrasil.com.br/", "confidencial")
    assert r.nivel == "confidencial" and not r.elevado and r.regra_id == str(regra.id)


# ── modelo ──────────────────────────────────────────────────────────────────

def test_save_normaliza_o_dominio(org):
    assert _regra(org, "  *.BancoDoBrasil.com.br ").dominio == "bancodobrasil.com.br"


def test_dominio_duplicado_na_mesma_organizacao_e_recusado(org):
    _regra(org)
    with pytest.raises(IntegrityError), transaction.atomic():
        _regra(org, "WWW.x.com", "interno")
        _regra(org, "bancodobrasil.com.br", "interno")


def test_dominio_invalido_nao_e_gravado(org):
    with pytest.raises(ValueError):
        _regra(org, "192.168.0.1")


def test_clean_do_admin_devolve_erro_de_validacao(org):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        RegraClassificacaoDominio(tenant=org, dominio="com", nivel="restrito").full_clean()


# ── API interna de captura ──────────────────────────────────────────────────

@pytest.fixture
def api(client, settings, org):
    settings.INTERNAL_API_TOKEN = "token-de-teste"
    dono = org.owner
    Membership.objects.create(user=dono, organization=org, role="owner")

    def postar(url, nivel="publico", allow_llm=True):
        corpo = {
            "artifact_type": "documento",
            "content": {"url": url, "mhtml_path": "x.mhtml"},
            "tenant_id": str(org.id), "user_id": str(dono.id), "info_type": "fato",
            "classification_level": nivel, "allow_external_llm": allow_llm,
        }
        return client.post(
            "/artifacts/api/v1/artefatos/", data=json.dumps(corpo),
            content_type="application/json", HTTP_X_INTERNAL_TOKEN="token-de-teste",
        )
    return postar


def test_api_eleva_o_nivel_e_fecha_o_llm_externo(org, api):
    _regra(org)
    resp = api("https://www.bancodobrasil.com.br/", nivel="publico", allow_llm=True)
    assert resp.status_code == 201
    corpo = resp.json()
    assert corpo["classification_level"] == "confidencial" and corpo["classificacao_elevada"] is True
    a = Artifact.objects.get(pk=corpo["artifact_id"])
    assert a.classification_level == "confidencial"
    assert a.allow_external_llm is False   # pedido pela extensão, negado pela elevação


def test_api_sem_regra_nao_muda_nada(org, api):
    resp = api("https://exemplo.org/", nivel="publico", allow_llm=True)
    corpo = resp.json()
    assert corpo["classification_level"] == "publico" and corpo["classificacao_elevada"] is False
    a = Artifact.objects.get(pk=corpo["artifact_id"])
    assert a.classification_level == "publico" and a.allow_external_llm is True


def test_api_nunca_rebaixa_o_que_o_usuario_escolheu(org, api):
    _regra(org, nivel="interno")
    corpo = api("https://bancodobrasil.com.br/", nivel="confidencial").json()
    assert corpo["classification_level"] == "confidencial" and corpo["classificacao_elevada"] is False


def test_api_registra_evento_quando_eleva_e_so_entao(org, api):
    from apps.events.models import PipelineEvent

    _regra(org)
    api("https://exemplo.org/", nivel="publico")
    assert not PipelineEvent.objects.filter(stage="captura.classificada").exists()

    corpo = api("https://bancodobrasil.com.br/", nivel="publico").json()
    ev = PipelineEvent.objects.get(stage="captura.classificada")
    assert str(ev.subject_id) == corpo["artifact_id"]
    assert ev.payload["nivel_pedido"] == "publico" and ev.payload["nivel_efetivo"] == "confidencial"
    assert ev.payload["allow_external_llm_pedido"] is True and ev.payload["allow_external_llm_efetivo"] is False


def test_regra_nova_nao_reclassifica_o_que_ja_existe(org, api):
    antigo = api("https://bancodobrasil.com.br/", nivel="publico").json()["artifact_id"]
    _regra(org)
    api("https://bancodobrasil.com.br/", nivel="publico")
    assert Artifact.objects.get(pk=antigo).classification_level == "publico"
